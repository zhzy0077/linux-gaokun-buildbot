"""Camera graph, build payload and shared display/camera rail regressions."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DTS = ROOT / 'dts/sc8280xp-huawei-gaokun3-camera.dtsi'
IMPORT = ROOT / 'patches/0099-arm64-gaokun3-import-local-dts-and-defconfig.patch'
DRIVER = ROOT / 'patches/others/0009-media-i2c-ov13b10-support-Gaokun-camera-power.patch'


class CameraSupportTests(unittest.TestCase):
    def test_rear_sensor_and_reciprocal_graph(self):
        source = DTS.read_text()
        self.assertIn('camera_rear: camera@36 {', source)
        self.assertIn('compatible = "ovti,ov13b10";', source)
        self.assertIn('reg = <0x36>;', source)
        self.assertIn('remote-endpoint = <&ov13b10_ep>;', source)
        self.assertRegex(source, r'ov13b10_ep: endpoint \{\s*data-lanes = <1 2 3 4>;\s*'
                         r'link-frequencies = /bits/ 64 <560000000>;\s*'
                         r'remote-endpoint = <&csiphy0_ep>;')
        self.assertNotIn('s5k3l6', source)
        self.assertIn('remote-endpoint = <&hi846_ep>;', source)
        self.assertIn('remote-endpoint = <&csiphy3_ep>;', source)

    def test_board_power_clock_and_privacy_led(self):
        source = DTS.read_text()
        rear = source.split('camera_rear: camera@36 {', 1)[1].split('// eeprom', 1)[0]
        for property in ('avdd-supply = <&vreg_l2b>;',
                         'dovdd-supply = <&vreg_l2c>;',
                         'dvdd-supply = <&vreg_camr>;',
                         'reset-gpios = <&tlmm 7 GPIO_ACTIVE_LOW>;',
                         'assigned-clocks = <&camcc CAMCC_MCLK4_CLK>;',
                         'assigned-clock-rates = <19200000>;',
                         'lens-focus = <&voice_coil_motor>;',
                         'leds = <&privacy_led>;', 'led-names = "privacy";'):
            self.assertIn(property, rear)
        core = source.split('vreg_camr: regulator-camr {', 1)[1].split('};', 1)[0]
        self.assertIn('gpio = <&tlmm 92 GPIO_ACTIVE_HIGH>;', core)
        self.assertIn('regulator-min-microvolt = <1200000>;', core)
        self.assertIn('regulator-max-microvolt = <1200000>;', core)

    def test_both_config_paths_enable_sensor_and_lens(self):
        for name in ('gaokun3_defconfig', 'gaokun3-required.config'):
            config = (ROOT / 'defconfig' / name).read_text().splitlines()
            for symbol in ('VIDEO_OV13B10', 'VIDEO_HI846', 'VIDEO_DW9714'):
                self.assertIn(f'CONFIG_{symbol}=m', config, name)

    def test_import_patch_matches_camera_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git', 'apply',
                            '--include=arch/arm64/boot/dts/qcom/*camera.dtsi',
                            '--include=arch/arm64/configs/gaokun3_defconfig', str(IMPORT)],
                           cwd=directory, check=True, capture_output=True)
            tree = Path(directory)
            self.assertEqual((tree / 'arch/arm64/boot/dts/qcom' / DTS.name).read_bytes(),
                             DTS.read_bytes())
            self.assertEqual((tree / 'arch/arm64/configs/gaokun3_defconfig').read_bytes(),
                             (ROOT / 'defconfig/gaokun3_defconfig').read_bytes())

    def test_driver_dt_matching_and_board_scoping(self):
        patch = DRIVER.read_text()
        self.assertIn('+\t{ .compatible = "ovti,ov13b10" },', patch)
        self.assertIn('+MODULE_DEVICE_TABLE(of, ov13b10_of_ids);', patch)
        self.assertIn('+\t\t.of_match_table = ov13b10_of_ids,', patch)
        self.assertIn('+\tov13b->gaokun3 = of_machine_is_compatible("huawei,gaokun3");', patch)
        self.assertIn('+\tif (ov13b10->gaokun3)\n'
                      '+\t\treturn ov13b10_gaokun3_power_on(ov13b10);', patch)
        self.assertIn('+\tif (ov13b10->gaokun3)\n'
                      '+\t\tov13b10_gaokun3_release_avdd(ov13b10);', patch)

    def test_camera_bus_clocks_park_on_safe_source(self):
        patch = (ROOT / 'patches/others/0010-clk-qcom-camcc-sc8280xp-park-camera-clocks.patch').read_text()
        for name in ('camcc_camnoc_axi_clk_src', 'camcc_fast_ahb_clk_src',
                     'camcc_slow_ahb_clk_src'):
            self.assertRegex(patch, r'\.name = "' + name + r'",[\s\S]*?'
                             r'\n-\s*\.ops = &clk_rcg2_ops,\n'
                             r'\+\s*\.ops = &clk_rcg2_shared_ops,')
        self.assertEqual(patch.count('+\t\t.ops = &clk_rcg2_shared_ops,'), 3)

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
    def test_power_sequence_and_failure_unwind(self):
        # Compile the exact helper bodies carried by the kernel patch.
        added = '\n'.join(line[1:] for line in DRIVER.read_text().splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        helpers = '\n'.join(re.search(r'static (?:void|int) ' + name + r'\(.*?\n\}',
                                      added, re.DOTALL).group(0)
                            for name in ('ov13b10_gaokun3_release_avdd',
                                         'ov13b10_gaokun3_power_on'))
        harness = r'''
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define dev_warn(dev, ...) ((void)(dev))
struct device { int unused; };
struct regulator { int unused; };
struct regulator_bulk_data { struct regulator *consumer; };
struct ov13b10 {
    struct device *dev;
    struct regulator_bulk_data supplies[3];
    int reset, img_clk;
};
static const char * const ov13b10_supply_names[] = {"dovdd", "avdd", "dvdd"};
static int failure, enabled, clock_on, reset_asserted, voltage_min;
static int events[16], count;
static void event(int value) { assert(count < 16); events[count++] = value; }
static void gpiod_set_value_cansleep(int gpio, int value)
{ (void)gpio; reset_asserted = value; event(value ? 1 : 5); }
static int regulator_set_voltage(struct regulator *reg, int minimum, int maximum)
{
    (void)reg;
    if (minimum == 2800000) {
        assert(maximum == 2800000); event(2);
        if (failure == 1) return -EINVAL;
    } else {
        assert(minimum == 1800000 && maximum == 2800000); event(11);
    }
    voltage_min = minimum;
    return 0;
}
static int regulator_bulk_enable(size_t n, struct regulator_bulk_data *supplies)
{
    assert(n == 3 && supplies); event(3);
    if (failure == 2) return -EIO;
    enabled = 1; return 0;
}
static void regulator_bulk_disable(size_t n, struct regulator_bulk_data *supplies)
{ assert(n == 3 && supplies); enabled = 0; event(10); }
static void usleep_range(unsigned minimum, unsigned maximum)
{
    assert(maximum >= minimum);
    event(minimum == 1000 ? 4 : minimum == 10000 ? 6 : 8);
}
static int clk_prepare_enable(int clock)
{
    (void)clock; event(7);
    if (failure == 3) return -EBUSY;
    clock_on = 1; return 0;
}
''' + helpers + r'''
int main(void)
{
    struct device dev = {0};
    struct regulator avdd = {0};
    struct ov13b10 sensor = {.dev = &dev, .supplies = {{0}, {&avdd}, {0}}};
    assert(ov13b10_gaokun3_power_on(&sensor) == 0);
    assert(enabled && clock_on && !reset_asserted && voltage_min == 2800000);
    assert(count == 8);
    for (int i = 0; i < count; ++i) assert(events[i] == i + 1);
    for (failure = 1; failure <= 3; ++failure) {
        count = enabled = clock_on = 0;
        voltage_min = 1800000;
        int result = ov13b10_gaokun3_power_on(&sensor);
        assert(result == (failure == 1 ? -EINVAL : failure == 2 ? -EIO : -EBUSY));
        assert(!enabled && !clock_on && reset_asserted);
        assert(voltage_min == 1800000);
        if (failure != 1) assert(events[count - 1] == 11);
        if (failure == 3) assert(events[count - 3] == 1 && events[count - 2] == 10);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'power-test.c'
            binary = Path(directory) / 'power-test'
            source.write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
