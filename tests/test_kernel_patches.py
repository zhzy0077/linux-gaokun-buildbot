"""Apply the complete series to pristine Linux 7.2.9 without editing the input.

Set GAOKUN_KERNEL_SRC to an unpacked kernel.org tarball or pristine checkout.
Only paths touched by the patches are copied to a temporary Git repository.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
KERNEL_SRC = os.environ.get('GAOKUN_KERNEL_SRC')


@unittest.skipUnless(KERNEL_SRC, 'set GAOKUN_KERNEL_SRC to pristine Linux 7.2.9')
class KernelPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='gaokun-patch-test-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.tree = Path(cls.directory.name)
        upstream = Path(KERNEL_SRC)
        version = dict(re.findall(r'^(VERSION|PATCHLEVEL|SUBLEVEL) = (\d+)$',
                                 (upstream / 'Makefile').read_text(), re.MULTILINE))
        if version != {'VERSION': '7', 'PATCHLEVEL': '2', 'SUBLEVEL': '9'}:
            raise ValueError('GAOKUN_KERNEL_SRC must contain Linux 7.2.9')
        cls.standard = [p for group in ('upstream', 'others', 'media')
                        for p in sorted((ROOT / 'patches' / group).glob('*.patch'))]
        cls.standard.append(ROOT / 'patches/0099-arm64-gaokun3-import-local-dts-and-defconfig.patch')
        cls.el2 = sorted((ROOT / 'patches/el2').glob('*.patch'))
        paths = {path for patch in cls.standard + cls.el2
                 for path in re.findall(r'^diff --git a/(\S+) b/\S+$',
                                        patch.read_text(), re.MULTILINE)}
        for path in paths:
            source = upstream / path
            if source.exists():
                target = cls.tree / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        cls.git('init', '-q')
        cls.git('config', 'user.name', 'Patch regression test')
        cls.git('config', 'user.email', 'test@localhost')
        cls.git('config', 'commit.gpgsign', 'false')
        cls.git('add', '-A')
        cls.git('commit', '-qm', 'Pristine Linux 7.2.9 patch targets')
        # Same order and commands as scripts/ci/20_build_kernel_variants.sh.
        for group in ('upstream', 'others', 'media'):
            cls.git('am', *sorted((ROOT / 'patches' / group).glob('*.patch')))
        cls.git('am', cls.standard[-1])

    @classmethod
    def git(cls, *args):
        result = subprocess.run(['git', *map(str, args)], cwd=cls.tree,
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'git {args[0]} failed:\n{result.stdout}{result.stderr}')
        return result.stdout

    def test_complete_standard_and_el2_series(self):
        for name in ('sc8280xp-huawei-gaokun3.dts', 'sc8280xp-huawei-gaokun3-camera.dtsi'):
            self.assertEqual((self.tree / 'arch/arm64/boot/dts/qcom' / name).read_bytes(),
                             (ROOT / 'dts' / name).read_bytes())
        self.assertEqual((self.tree / 'arch/arm64/configs/gaokun3_defconfig').read_bytes(),
                         (ROOT / 'defconfig/gaokun3_defconfig').read_bytes())
        for name in ('himax-spi-core.c', 'hx-algo.c', 'hx-algo.h'):
            self.assertEqual((self.tree / 'drivers/input/touchscreen' / name).read_bytes(),
                             (ROOT / 'drivers/touchscreen-hx83121a' / name).read_bytes())
        pinctrl = (self.tree / 'drivers/pinctrl/qcom/pinctrl-sc8280xp.c').read_text()
        self.assertNotRegex(pinctrl, r'\{\s*175,\s*237\s*\}')
        self.assertRegex(pinctrl, r'\{\s*174,\s*222\s*\}')
        self.git('apply', *self.el2)
        pas = (self.tree / 'drivers/remoteproc/qcom_q6v5_pas.c').read_text()
        self.assertEqual(pas.count('static int qcom_pas_attach('), 1)
        for name in ('qcom_pas_ops', 'qcom_pas_minidump_ops', 'qcom_pas_ops_no_reset'):
            ops = re.search(r'static const struct rproc_ops ' + name + r' = \{(.*?)\n\};',
                            pas, re.DOTALL).group(1)
            self.assertEqual(ops.count('.attach ='), 1, name)
        attach = re.search(r'static int qcom_pas_attach\(.*?\n\}', pas, re.DOTALL).group(0)
        self.assertIn('qcom_q6v5_attach(&pas->q6v5)', attach)
        self.assertIn('qcom_q6v5_unprepare(&pas->q6v5)', attach)
        self.assertIn('qcom_sysmon_shutdown_irq_state(pas->sysmon)', attach)
        core = (self.tree / 'drivers/remoteproc/remoteproc_core.c').read_text()
        self.assertIn('schedule_work(&rproc->attach_work)', core)
        self.assertIn('rproc->auto_boot != RPROC_AUTO_BOOT_RESTART_IF_FW_AVAILABLE', core)
        self.check_stop_behavior()

    def check_stop_behavior(self):
        if not shutil.which('cc'):
            self.skipTest('native C compiler required for stop-state behavior')
        source = (self.tree / 'drivers/remoteproc/qcom_q6v5.c').read_text()
        stop = re.search(r'int qcom_q6v5_request_stop\(.*?\n\}', source, re.DOTALL).group(0)
        detect = re.search(r'void qcom_q6v5_read_smp2p_state\(.*?\n\}', source, re.DOTALL).group(0)
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#define RPROC_DETACHED 1
#define RPROC_ATTACHED 2
#define RPROC_RUNNING 3
#define RPROC_CRASHED 4
#define IRQCHIP_STATE_LINE_LEVEL 0
#define HZ 100
#define BIT(n) (1U << (n))
struct rproc { int state; };
struct qcom_sysmon { bool ack; };
struct qcom_q6v5 {
    bool running;
    struct rproc *rproc;
    int state, stop_bit, stop_done;
    int handover_irq, ready_irq, fatal_irq, stop_irq;
};
static bool lines[4];
static int signals;
static bool qcom_sysmon_shutdown_acked(struct qcom_sysmon *s) { return s && s->ack; }
static int irq_get_irqchip_state(int irq, int kind, bool *value)
{ (void)kind; *value = lines[irq]; return 0; }
static void qcom_smem_state_update_bits(int state, unsigned mask, unsigned value)
{ (void)state; (void)mask; if (value) ++signals; }
static int wait_for_completion_timeout(int *completion, int timeout)
{ (void)completion; (void)timeout; return 1; }
''' + detect + '\n' + stop + r'''
int main(void)
{
    struct rproc r = {0};
    struct qcom_q6v5 q = {.rproc = &r, .handover_irq = 0, .ready_irq = 1,
                         .fatal_irq = 2, .stop_irq = 3};
    struct qcom_sysmon sysmon = {false};
    lines[0] = lines[1] = true;
    qcom_q6v5_read_smp2p_state(&q);
    assert(r.state == RPROC_DETACHED && q.running);
    assert(qcom_q6v5_request_stop(&q, &sysmon) == 0 && signals == 1);
    assert(!q.running);
    qcom_q6v5_request_stop(&q, &sysmon);
    assert(signals == 1); /* Do not send a second stop. */
    int states[] = {RPROC_ATTACHED, RPROC_RUNNING, RPROC_CRASHED};
    for (unsigned i = 0; i < sizeof(states) / sizeof(states[0]); ++i) {
        r.state = states[i]; q.running = true;
        qcom_q6v5_request_stop(&q, &sysmon);
        assert(signals == (int)i + 2); /* Logical crash still needs a stop. */
    }
    q.running = true; sysmon.ack = true;
    qcom_q6v5_request_stop(&q, &sysmon);
    assert(signals == 4); /* Sysmon already shut down. */
    q.running = false; sysmon.ack = false; r.state = RPROC_CRASHED;
    qcom_q6v5_request_stop(&q, &sysmon);
    assert(signals == 4); /* Watchdog/fatal cleared running. */
    lines[2] = true; r.state = 0;
    qcom_q6v5_read_smp2p_state(&q);
    assert(!q.running && r.state == 0);
    return 0;
}
'''
        path = self.tree / 'stop-test.c'
        binary = self.tree / 'stop-test'
        path.write_text(harness)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(path), '-o', str(binary)],
                       check=True, capture_output=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
