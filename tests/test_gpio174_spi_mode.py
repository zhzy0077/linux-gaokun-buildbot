"""Regression checks for GPIO174 ordering and the CI patch/source mirrors."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / 'drivers/touchscreen-hx83121a/himax-spi-core.c'


class GPIO174Tests(unittest.TestCase):
    def test_driver_patch_matches_source_mirrors(self):
        patch = ROOT / 'patches/others/0003-Input-touchscreen-add-Himax-HX83121A-SPI-driver.patch'
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git', 'apply', '--include=drivers/input/touchscreen/*.c',
                            '--include=drivers/input/touchscreen/*.h', str(patch)],
                           cwd=directory, check=True, capture_output=True)
            for name in ('himax-spi-core.c', 'hx-algo.c', 'hx-algo.h'):
                self.assertEqual((Path(directory) / 'drivers/input/touchscreen' / name).read_bytes(),
                                 (ROOT / 'drivers/touchscreen-hx83121a' / name).read_bytes(), name)

    def test_device_tree_selects_physical_low(self):
        dts = (ROOT / 'dts/sc8280xp-huawei-gaokun3.dts').read_text()
        self.assertRegex(dts, r'mode-gpios\s*=\s*<&tlmm\s+174\s+GPIO_ACTIVE_HIGH>;')

    @unittest.skipUnless(os.environ.get('GAOKUN_UPSTREAM_DTS'), 'set GAOKUN_UPSTREAM_DTS for v7.2 DTS integration check')
    def test_dts_patch_applies_to_upstream_and_matches_mirrors(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'arch/arm64/boot/dts/qcom'
            target.mkdir(parents=True)
            shutil.copyfile(os.environ['GAOKUN_UPSTREAM_DTS'], target / 'sc8280xp-huawei-gaokun3.dts')
            subprocess.run(['git', 'apply', '--include=arch/arm64/boot/dts/qcom/*',
                            str(ROOT / 'patches/0099-arm64-gaokun3-import-local-dts-and-defconfig.patch')],
                           cwd=directory, check=True, capture_output=True)
            for name in ('sc8280xp-huawei-gaokun3.dts', 'sc8280xp-huawei-gaokun3-camera.dtsi'):
                self.assertEqual((target / name).read_bytes(), (ROOT / 'dts' / name).read_bytes(), name)

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
    def test_reset_reasserts_spi_before_reset_and_supports_legacy_dtb(self):
        source = DRIVER.read_text()
        function = re.search(r'static void himax_pin_reset\(struct himax_ts_data \*ts\)\n\{.*?\n\}',
                             source, re.DOTALL).group(0)
        harness = r'''
#include <assert.h>
#include <stddef.h>
struct gpio_desc { int pin; int value; };
struct himax_ts_data { struct gpio_desc *gpiod_rst; struct gpio_desc *gpiod_mode; };
struct event { int pin; int value; };
static struct event events[8];
static unsigned count;
static void gpiod_set_value_cansleep(struct gpio_desc *gpio, int value)
{
    assert(gpio && count < 8);
    events[count++] = (struct event){gpio->pin, value};
    gpio->value = value;
}
static void usleep_range(unsigned long minimum, unsigned long maximum)
{
    assert(maximum >= minimum);
}
''' + function + r'''
int main(void)
{
    struct gpio_desc mode = {174, 1}, reset = {99, 0};
    struct himax_ts_data ts = {&reset, &mode};
    /* Also simulate firmware leaving HIGH again before a later resume/reset. */
    for (int attempt = 0; attempt < 2; ++attempt) {
        mode.value = 1;
        count = 0;
        himax_pin_reset(&ts);
        assert(count == 3);
        assert(events[0].pin == 174 && events[0].value == 0);
        assert(events[1].pin == 99 && events[1].value == 1);
        assert(events[2].pin == 99 && events[2].value == 0);
        assert(mode.value == 0);
    }
    ts.gpiod_mode = NULL;
    count = 0;
    himax_pin_reset(&ts);
    assert(count == 2);
    assert(events[0].pin == 99 && events[0].value == 1);
    assert(events[1].pin == 99 && events[1].value == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'reset-test.c'
            binary = Path(directory) / 'reset-test'
            source.write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
