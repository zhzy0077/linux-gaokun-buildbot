"""Distribution policy is applied before compilation and enforced at packaging."""
import importlib.util
import json
import shutil
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
            path, origin = check.baseline(ROOT, distro)
            config = check.parse_config(path.read_text())
            self.assertEqual(check.digest(path), origin['config_sha256'])
            self.assertEqual(config['CONFIG_SECURITY_' + primary], 'y')
            # Ubuntu compiles SELinux too; its default LSM list selects AppArmor.
            self.assertIn(primary.lower(), config['CONFIG_LSM'])
            self.assertNotIn(disabled.lower(), config['CONFIG_LSM'])

    def test_distribution_fragments_do_not_override_hardware_or_tpm(self):
        for p in (ROOT / 'defconfig/distro').glob('*.config'):
            for key in check.parse_config(p.read_text()):
                self.assertFalse(key.startswith(('CONFIG_QCOM_', 'CONFIG_DRM_', 'CONFIG_TCG_',
                                                 'CONFIG_ACPI', 'CONFIG_ARM64_')))

    def test_vendor_baseline_not_gaokun_defconfig_is_the_starting_point(self):
        script = (ROOT / 'scripts/ci/configure_kernel.sh').read_text()
        self.assertNotIn('gaokun3_defconfig', script)
        self.assertIn('cp "$baseline" "$out_dir/.config"', script)
        self.assertIn('vendor-normalized.config', script)
        for distro in ('fedora', 'ubuntu'):
            path, _ = check.baseline(ROOT, distro)
            values = check.parse_config(path.read_text())
            for key in ('CONFIG_XFS_FS', 'CONFIG_KPROBES', 'CONFIG_DEBUG_INFO_BTF',
                        'CONFIG_VIRTIO_NET', 'CONFIG_DM_SNAPSHOT', 'CONFIG_RUST'):
                self.assertIn(values[key], ('y', 'm'))

    def test_rejects_changed_baseline_and_post_build_config(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'; out = Path(directory) / 'out'; out.mkdir()
            shutil.copytree(ROOT / 'defconfig', repo / 'defconfig')
            path, _ = check.baseline(repo, 'fedora')
            requirements = {}
            for fragment in check.fragments(repo, 'fedora', False):
                requirements.update(check.parse_config(fragment.read_text()))
            text = ''.join(f'{k}={v}\n' for k, v in requirements.items())
            (out / '.config').write_text(text)
            (out / 'vendor-normalized.config').write_text(path.read_text())
            check.audit(repo, 'fedora', out, False)
            check.verify_package(repo, 'fedora', out)
            (out / '.config').write_text(text + 'CONFIG_TEST=y\n')
            with self.assertRaises(ValueError):check.verify_package(repo, 'fedora', out)
            path.write_text(path.read_text() + '\n')
            with self.assertRaises(ValueError):check.baseline(repo, 'fedora')

    def test_build_and_package_wiring(self):
        build = (ROOT / 'scripts/ci/20_build_kernel_variants.sh').read_text()
        self.assertIn('KERNEL_DISTRO:?set KERNEL_DISTRO', build)
        self.assertLess(build.index('configure_kernel.sh'), build.index('-j"$(nproc)"'))
        for kind, distro in [('rpms', 'fedora'), ('debs', 'ubuntu')]:
            workflow = (ROOT / f'.github/workflows/gaokun3-package-{kind}.yml').read_text()
            self.assertIn('KERNEL_DISTRO: ' + distro, workflow)
            package = (ROOT / f'scripts/ci/70_build_package_{kind}.sh').read_text()
            self.assertIn('cat "$out_dir/gaokun-distro"', package)
            self.assertIn(f'--distro {distro} --package', package)
            self.assertIn('kernel-config-${variant_key}.json', package)
            self.assertIn(f'"kernel_distro": "{distro}"', package)


if __name__ == '__main__':
    unittest.main()
