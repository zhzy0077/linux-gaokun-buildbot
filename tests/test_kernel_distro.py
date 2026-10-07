"""Distribution policy is applied before compilation and enforced at packaging."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('config_check', ROOT / 'tools/boot/check-kernel-config.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class KernelDistroTests(unittest.TestCase):
    def test_parse_disabled_and_string_options(self):
        self.assertEqual(check.parse_config('CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_LSM="a,b"\n'),
                         {'CONFIG_A': 'y', 'CONFIG_B': 'n', 'CONFIG_LSM': '"a,b"'})

    def test_reject_silently_dropped_or_changed_options(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            f = p / 'fragment'; c = p / '.config'
            f.write_text('CONFIG_SECURITY_SELINUX=y\nCONFIG_LSM="selinux"\n')
            for text in ('', '# CONFIG_SECURITY_SELINUX is not set\n',
                         'CONFIG_SECURITY_SELINUX=y\nCONFIG_LSM="apparmor"\n'):
                c.write_text(text)
                with self.assertRaises(ValueError):
                    check.verify(c, [f])
            c.write_text(f.read_text())
            check.verify(c, [f])

    def test_last_fragment_wins_and_absent_disabled_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            a = p / 'a'; b = p / 'b'; c = p / '.config'
            a.write_text('CONFIG_A=y\n')
            b.write_text('# CONFIG_A is not set\n')
            c.write_text('')
            check.verify(c, [a, b])

    def test_distinct_primary_security_policies(self):
        for distro, primary, disabled in [('fedora', 'SELINUX', 'APPARMOR'),
                                           ('ubuntu', 'APPARMOR', 'SELINUX')]:
            config = check.parse_config((ROOT / f'defconfig/distro/{distro}.config').read_text())
            self.assertEqual(config['CONFIG_SECURITY_' + primary], 'y')
            self.assertEqual(config['CONFIG_SECURITY_' + disabled], 'n')
            self.assertIn(primary.lower(), config['CONFIG_LSM'])
            self.assertNotIn(disabled.lower(), config['CONFIG_LSM'])

    def test_distribution_fragments_do_not_override_hardware_or_tpm(self):
        for p in (ROOT / 'defconfig/distro').glob('*.config'):
            for key in check.parse_config(p.read_text()):
                self.assertFalse(key.startswith(('CONFIG_QCOM_', 'CONFIG_DRM_', 'CONFIG_TCG_',
                                                 'CONFIG_ACPI', 'CONFIG_ARM64_', 'CONFIG_LOCALVERSION')))

    def test_build_and_package_wiring(self):
        build = (ROOT / 'scripts/ci/20_build_kernel_variants.sh').read_text()
        self.assertIn('KERNEL_DISTRO:?set KERNEL_DISTRO', build)
        self.assertLess(build.index('configure_kernel.sh'), build.index('-j"$(nproc)"'))
        for kind, distro in [('rpms', 'fedora'), ('debs', 'ubuntu')]:
            workflow = (ROOT / f'.github/workflows/gaokun3-package-{kind}.yml').read_text()
            self.assertIn('KERNEL_DISTRO: ' + distro, workflow)
            package = (ROOT / f'scripts/ci/70_build_package_{kind}.sh').read_text()
            self.assertIn('cat "$out_dir/gaokun-distro"', package)
            self.assertIn(f'defconfig/distro/{distro}.config', package)
            self.assertIn(f'"kernel_distro": "{distro}"', package)


if __name__ == '__main__':
    unittest.main()
