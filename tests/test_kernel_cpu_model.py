"""Validate the board-scoped kernel CPU name without changing user-space libraries."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'patches/0099-arm64-gaokun3-import-local-dts-and-defconfig.patch'
CPUINFO = 'arch/arm64/kernel/cpuinfo.c'
MODEL = 'Snapdragon (TM) 8cx Gen 3 @ 3.0 GHz'
ADDED_BRANCH = '\telse if (of_machine_is_compatible("huawei,gaokun3"))\n\t\tseq_puts(m, "model name\\t: ' + MODEL + '\\n");\n'


@unittest.skipUnless(os.environ.get('GAOKUN_UPSTREAM_CPUINFO'),
                     'set GAOKUN_UPSTREAM_CPUINFO for kernel CPU-name checks')
class KernelCpuModelTests(unittest.TestCase):
    def patched_source(self, directory):
        target = Path(directory) / CPUINFO
        target.parent.mkdir(parents=True)
        original = Path(os.environ['GAOKUN_UPSTREAM_CPUINFO']).read_text()
        target.write_text(original)
        subprocess.run(['git', 'apply', '--whitespace=error', '--include=' + CPUINFO, str(PATCH)],
                       cwd=directory, check=True, capture_output=True)
        return original, target.read_text()

    def test_patch_only_adds_of_include_and_board_name(self):
        with tempfile.TemporaryDirectory() as directory:
            original, patched = self.patched_source(directory)
        self.assertEqual(patched.count(ADDED_BRANCH), 1)
        restored = patched.replace('#include <linux/of.h>\n', '', 1).replace(ADDED_BRANCH, '', 1)
        self.assertEqual(restored, original)

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
    def test_native_gaokun_only_and_compatibility_output_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            _, patched = self.patched_source(directory)
            start = patched.index('\tif (compat)\n\t\tseq_printf(m, "model name')
            end = patched.index('\n\n\tseq_printf(m, "BogoMIPS', start)
            branch = patched[start:end]
            program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define MIDR_REVISION(midr) ((midr) & 15)
#define COMPAT_ELF_PLATFORM "v8l"
struct seq_file { char text[256]; };
static const char *board;
static bool of_machine_is_compatible(const char *name) { return !strcmp(board, name); }
static void seq_printf(struct seq_file *m, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(m->text, sizeof(m->text), fmt, ap);
    va_end(ap);
}
static void seq_puts(struct seq_file *m, const char *text) { seq_printf(m, "%s", text); }
static void emit_name(struct seq_file *m, bool compat) {
    unsigned midr = 2;
''' + branch + r'''
}
int main(void) {
    const char *boards[] = {"huawei,gaokun3", "lenovo,thinkpad-x13s", "qcom,x1e80100"};
    for (unsigned i = 0; i < 3; i++) {
        struct seq_file output = {{0}};
        board = boards[i];
        emit_name(&output, false);
        assert(!strcmp(output.text, i == 0 ?
            "model name\t: Snapdragon (TM) 8cx Gen 3 @ 3.0 GHz\n" : ""));
        output.text[0] = 0;
        emit_name(&output, true);
        assert(!strcmp(output.text, "model name\t: ARMv8 Processor rev 2 (v8l)\n"));
    }
    return 0;
}
'''
            source = Path(directory) / 'test.c'
            binary = Path(directory) / 'test'
            source.write_text(program)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
