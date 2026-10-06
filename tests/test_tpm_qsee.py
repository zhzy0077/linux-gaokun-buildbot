"""Regression guards for fixed firmware memory and non-provisioning TPM bring-up."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'drivers/tpm-qcom-qsee/tpm_qcom_qsee.c'


def function(source, signature):
    return re.search(re.escape(signature)+r'[^\{]*\{.*?\n\}',source,re.S).group(0)


class TpmQseeTests(unittest.TestCase):
    @staticmethod
    def compile_run(code):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'test.c').write_text(code)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(path/'test.c'),'-o',str(path/'test')],check=True,capture_output=True)
            subprocess.run([str(path/'test')],check=True)

    def test_patch_driver_matches_local_mirror(self):
        patch=ROOT/'patches/others/0008-firmware-qcom-add-Gaokun-QSEE-TPM-validation.patch'
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git','apply','--include=drivers/char/tpm/tpm_qcom_qsee.c',str(patch)],cwd=directory,check=True,capture_output=True)
            self.assertEqual((Path(directory)/'drivers/char/tpm/tpm_qcom_qsee.c').read_bytes(),SOURCE.read_bytes())

    @unittest.skipUnless(shutil.which('cc'),'native C compiler required')
    def test_only_non_provisioning_commands_are_allowed(self):
        helper=function(SOURCE.read_text(),'static bool qcom_tpm_qsee_command_allowed')
        code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef uint32_t u32;
#define TPM2_CC_SELF_TEST 0x143
#define TPM2_CC_GET_CAPABILITY 0x17a
#define TPM2_CC_GET_RANDOM 0x17b
#define TPM2_CC_PCR_READ 0x17e
''' + helper + r'''
int main(void) {
    for (u32 n=0; n<0x10000; n++) {
        bool expected=n==0x143 || n==0x17a || n==0x17b || n==0x17c || n==0x17e;
        assert(qcom_tpm_qsee_command_allowed(n)==expected);
    }
    assert(!qcom_tpm_qsee_command_allowed(0xffffffff));
    return 0;
}
'''
        self.compile_run(code)

    @unittest.skipUnless(shutil.which('cc'),'native C compiler required')
    def test_fixed_control_area_always_uses_hlos_and_releases_bridge(self):
        source=SOURCE.read_text()
        remove=function(source,'static void qcom_tpm_qsee_delete_control_area_bridge')
        create=function(source,'static int qcom_tpm_qsee_create_control_area_bridge')
        code=r'''
#include <assert.h>
#include <stdint.h>
typedef uint64_t u64;
#define QCOM_SCM_PERM_RW 6
#define QCOM_SCM_VMID_HLOS 3
#define QCOM_TPM_QSEE_CONTROL_AREA_SIZE 12288
#define QCOM_TPM_QSEE_NUM_VM_SHIFT 9
struct device { int unused; };
struct qseecom_client { struct { struct device dev; } aux_dev; };
struct qcom_tpm_qsee { struct qseecom_client *client; u64 control_area_phys, control_area_shm_bridge; };
static int create_result, action_result, actions, deleted;
static u64 returned_handle = 0x1234;
static int qcom_scm_shm_bridge_create(u64 ns, u64 secure, u64 size, u64 vmid, u64 *handle) {
    assert(ns==(0x80890000|6)); assert(secure==ns);
    assert(size==(12288|(1ULL<<9))); assert(vmid==3);
    *handle=returned_handle; return create_result;
}
static void qcom_scm_shm_bridge_delete(u64 handle) { assert(handle==returned_handle); deleted++; }
static int devm_add_action_or_reset(struct device *dev, void (*fn)(void *), void *data) {
    (void)dev; actions++;
    if (action_result) fn(data);
    return action_result;
}
''' + remove + '\n' + create + r'''
int main(void) {
    struct qseecom_client client={0};
    struct qcom_tpm_qsee t={.client=&client,.control_area_phys=0x80890000};
    assert(qcom_tpm_qsee_create_control_area_bridge(&t)==0);
    assert(actions==1);
    qcom_tpm_qsee_delete_control_area_bridge(&t); assert(deleted==1);
    /* A successful opaque handle may be zero and must still be released. */
    returned_handle=0;
    assert(qcom_tpm_qsee_create_control_area_bridge(&t)==0);assert(actions==2);
    qcom_tpm_qsee_delete_control_area_bridge(&t);assert(deleted==2);
    create_result=-1;
    assert(qcom_tpm_qsee_create_control_area_bridge(&t)==-1);assert(actions==2);
    assert(deleted==2);
    /* Failure to register cleanup must invoke it immediately. */
    create_result=0;action_result=-12;returned_handle=0x1234;
    assert(qcom_tpm_qsee_create_control_area_bridge(&t)==-12);
    assert(actions==3 && deleted==3);
    return 0;
}
'''
        self.compile_run(code)

    @unittest.skipUnless(shutil.which('cc'),'native C compiler required')
    def test_reserved_region_requires_complete_non_ram_coverage(self):
        source=SOURCE.read_text()
        region=re.search(r'struct qcom_tpm_qsee_region \{.*?\n\};',source,re.S).group(0)
        helper=function(source,'static int qcom_tpm_qsee_reserved')
        code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef uint64_t resource_size_t;
#define IORESOURCE_MEM 0x200
#define IORESOURCE_SYSTEM_RAM (IORESOURCE_MEM | 0x01000000)
#define IORESOURCE_BUSY 0x80000000
#define IORES_DESC_NONE 0
struct resource { resource_size_t start, end; unsigned long flags, desc; };
''' + region + '\n' + helper + r'''
int main(void) {
    struct qcom_tpm_qsee_region area={.start=0x1000,.end=0x3fff};
    struct resource res={.start=0x1000,.end=0x3fff,.flags=IORESOURCE_MEM};
    qcom_tpm_qsee_reserved(&res,&area);assert(area.reserved);
    area.reserved=false;res.end=0x2fff;
    qcom_tpm_qsee_reserved(&res,&area);assert(!area.reserved);
    res.end=0x3fff;res.start=0x2000;
    qcom_tpm_qsee_reserved(&res,&area);assert(!area.reserved);
    res.start=0x1000;res.flags|=IORESOURCE_BUSY;
    qcom_tpm_qsee_reserved(&res,&area);assert(!area.reserved);
    res.flags=IORESOURCE_SYSTEM_RAM;
    qcom_tpm_qsee_reserved(&res,&area);assert(!area.reserved);
    res.flags=IORESOURCE_MEM;res.desc=7;
    qcom_tpm_qsee_reserved(&res,&area);assert(!area.reserved);
    return 0;
}
'''
        self.compile_run(code)

    def test_guards_precede_control_writes_and_global_allocator_is_unchanged(self):
        source=SOURCE.read_text()
        send=function(source,'static int qcom_tpm_qsee_send(')
        self.assertLess(send.index('qcom_tpm_qsee_command_allowed'),send.index('mutex_lock'))
        self.assertIn('be32_to_cpu(header.length) != cmd_len',send)
        self.assertIn('check_add_overflow(control_area',source)
        self.assertIn('region_intersects(control_area',source)
        self.assertIn('if (!allow_probe || !of_machine_is_compatible("huawei,gaokun3"))',source)
        self.assertIn('qcom_tzmem_alloc(qtpm->mempool',source)
        self.assertNotIn('control_hlos',source)
        self.assertNotIn('read_sysreg',source)
        patch=(ROOT/'patches/others/0008-firmware-qcom-add-Gaokun-QSEE-TPM-validation.patch').read_text()
        self.assertNotIn('diff --git a/drivers/firmware/qcom/qcom_tzmem.c',patch)

    def test_kernel_configuration_keeps_provisioning_off(self):
        config=(ROOT/'defconfig/gaokun3_defconfig').read_text().splitlines()
        for line in ('CONFIG_TCG_TPM=m','CONFIG_TCG_QCOM_TPM_QSEE=m',
                     '# CONFIG_TCG_TPM2_HMAC is not set','# CONFIG_HW_RANDOM_TPM is not set'):
            self.assertIn(line,config)
        self.assertIn('static bool allow_probe;',SOURCE.read_text())

if __name__=='__main__':unittest.main()
