"""Anaconda owns storage and bootloader installation; our post validates it."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT/'tools/installer/gaokun_installer.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
UUID = '22222222-2222-4222-8222-222222222222'
LUKS = '33333333-3333-4333-8333-333333333333'


class InstallerTests(unittest.TestCase):
    def test_native_anaconda_storage_and_sdboot(self):
        ks = (ROOT/'tools/installer/interactive-defaults.ks').read_text()
        self.assertIn('bootloader --sdboot', ks)
        self.assertNotIn('bootloader --disabled', ks)
        for directive in ('ignoredisk ', 'clearpart ', 'part ', 'btrfs ', 'autopart '):
            self.assertFalse(any(line.startswith(directive) for line in ks.splitlines()))
        for forbidden in ('test-target.json', 'storage.ks', '--exclude-weakdeps', 'prepare-target', ' finalize'):
            self.assertNotIn(forbidden, ks)
        self.assertIn('file:///opt/installer/repo', ks)
        for package in ('kernel-gaokun3-el2', 'kernel-modules-gaokun3-el2', 'gaokun3-platform',
                        'linux-firmware-gaokun3', 'sdubby', 'systemd-pam', 'dbus-daemon', 'cracklib-dicts'):
            self.assertIn(package, ks.splitlines())

    def test_encrypted_btrfs_boot_arguments(self):
        root = {'uuid': UUID, 'fstype': 'btrfs', 'fsroot': '/root'}
        installer.check_root_options(f'root=UUID={UUID} rootflags=subvol=root rd.luks.uuid=luks-{LUKS}',root,[LUKS])
        with self.assertRaisesRegex(ValueError, 'LUKS'):
            installer.check_root_options(f'root=UUID={UUID} rootflags=subvol=root',root,[LUKS])
        with self.assertRaisesRegex(ValueError, 'subvolume'):
            installer.check_root_options(f'root=UUID={UUID} rootflags=subvol=@ rd.luks.uuid={LUKS}',root,[LUKS])

    def test_unencrypted_root_is_allowed(self):
        installer.check_root_options(f'root=UUID={UUID}', {'uuid':UUID,'fstype':'ext4','fsroot':'/'}, [])

    def test_conflicting_installation_media_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'BLS root'):
            installer.check_root_options(f'root=UUID={UUID} root=UUID=installer',
                                         {'uuid':UUID,'fstype':'ext4','fsroot':'/'}, [])

    def test_boot_references_cannot_escape_esp(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);esp=root/'esp';esp.mkdir();outside=root/'outside';outside.write_text('preserve')
            (esp/'link').symlink_to(outside)
            for value in ('../outside', '/link', '/missing'):
                with self.assertRaises(ValueError): installer.contained_file(esp,value)
            (esp/'linux').write_text('kernel')
            self.assertEqual(installer.contained_file(esp,'/linux'),esp/'linux')

    def test_bls_parser_keeps_multiple_initrds_for_validation(self):
        fields=installer.boot_fields('# comment\nlinux /linux\ninitrd /a\ninitrd /b\noptions root=x\n')
        self.assertEqual(fields['initrd'],['/a','/b'])

    def test_network_and_ssh_are_explicit_target_only_policy(self):
        for retain in (False,True):
            with tempfile.TemporaryDirectory() as d, patch.object(installer,'TARGET',Path(d)), \
                 patch.object(installer.subprocess,'run') as run:
                p=Path(d)/'etc/NetworkManager/system-connections/test.nmconnection'
                p.parent.mkdir(parents=True);p.write_text('synthetic test data')
                installer.apply_policy({'copy_network':retain,'enable_ssh':retain})
                self.assertEqual(p.exists(),retain)
                self.assertEqual(run.call_args.args[0],['systemctl','--root',d,'enable' if retain else 'disable','sshd.service'])

    def test_network_policy_rejects_escape_and_invalid_booleans(self):
        with self.assertRaises(ValueError):installer.validate_install_policy({'copy_network':'false','enable_ssh':False})
        with tempfile.TemporaryDirectory() as d, patch.object(installer,'TARGET',Path(d)):
            p=Path(d)/'etc/NetworkManager/system-connections';p.parent.mkdir(parents=True);p.symlink_to('/tmp')
            with self.assertRaises(ValueError):installer.apply_policy(installer.DEFAULT_POLICY)

    def test_post_helper_has_no_partition_or_boot_install_actions(self):
        source=(ROOT/'tools/installer/gaokun_installer.py').read_text()
        for operation in ("'bootctl'", "'kernel-install'", "'dracut'", "'mkfs'", "'parted'",'test-target.json'):
            self.assertNotIn(operation,source)


if __name__=='__main__':
    unittest.main()
