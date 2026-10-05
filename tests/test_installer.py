"""Installer guards and generated storage configuration (no device writes)."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] /
                                               'tools/installer/gaokun_installer.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

ROOT_UUID = '22222222-2222-4222-8222-222222222222'
LUKS_UUID = '33333333-3333-4333-8333-333333333333'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.root = {'uuid': ROOT_UUID, 'fstype': 'btrfs'}
        self.esp = {'uuid': 'ABCD-1234', 'fstype': 'vfat'}
        self.root_nodes = [
            {'path': '/dev/mapper/root', 'type': 'crypt', 'fstype': 'btrfs', 'uuid': ROOT_UUID},
            {'path': '/dev/nvme0n1p7', 'type': 'part', 'fstype': 'crypto_LUKS', 'uuid': LUKS_UUID},
            {'path': '/dev/nvme0n1', 'type': 'disk', 'fstype': None, 'uuid': None},
        ]
        self.esp_nodes = [{'path': '/dev/nvme0n1', 'type': 'disk', 'fstype': None, 'uuid': None}]
        self.source = {'uuid': 'source', 'disks': ['/dev/sda']}

    def test_encrypted_target_on_separate_disk(self):
        self.assertEqual(installer.check_target(self.root, self.esp, self.root_nodes,
                                                self.esp_nodes, self.source), [LUKS_UUID])

    def test_source_disk_is_rejected_even_with_different_filesystem_uuid(self):
        self.source['disks'] = ['/dev/nvme0n1']
        with self.assertRaisesRegex(ValueError, 'installer USB'):
            installer.check_target(self.root, self.esp, self.root_nodes, self.esp_nodes, self.source)

    def test_unencrypted_root_is_rejected(self):
        self.root_nodes[1]['fstype'] = 'btrfs'
        with self.assertRaisesRegex(ValueError, 'not encrypted'):
            installer.check_target(self.root, self.esp, self.root_nodes, self.esp_nodes, self.source)

    def test_efi_on_another_disk_is_rejected(self):
        self.esp_nodes[0]['path'] = '/dev/nvme1n1'
        with self.assertRaisesRegex(ValueError, 'same target disk'):
            installer.check_target(self.root, self.esp, self.root_nodes, self.esp_nodes, self.source)

    def test_cmdline_uses_new_uuids_and_actual_subvolume(self):
        cmdline = installer.kernel_cmdline(ROOT_UUID, '/root', [LUKS_UUID], 'efi=noruntime psi=1')
        self.assertIn('root=UUID=' + ROOT_UUID, cmdline)
        self.assertIn('rootflags=subvol=root', cmdline)
        self.assertIn('rd.luks.uuid=luks-' + LUKS_UUID, cmdline)

    def test_installer_root_argument_cannot_leak_into_platform_arguments(self):
        with self.assertRaises(ValueError):
            installer.kernel_cmdline(ROOT_UUID, '/root', [LUKS_UUID], 'root=/dev/sda2')

    def test_storage_selection_is_non_destructive_and_webui_compatible(self):
        plan = installer.scoped_disk_selection(['/dev/nvme0n1'])
        self.assertEqual(plan, 'ignoredisk --only-use=nvme0n1\nclearpart --none\n')
        for directive in ('part ', 'btrfs ', 'autopart '):
            self.assertFalse(any(line.startswith(directive) for line in plan.splitlines()))
        self.assertNotIn('--all', plan)
        self.assertNotIn('--passphrase', plan)

    def test_rejects_invalid_device_names(self):
        for devices in ([], ['/dev/sda', '/dev/nvme0n1'], ['/dev/nvme0n1\nclearpart --all']):
            with self.assertRaises(ValueError):
                installer.scoped_disk_selection(devices)

    def test_existing_unencrypted_partition_uses_root_uuid(self):
        with patch.object(installer, 'run', return_value=ROOT_UUID + '\n') as run:
            installer.check_old_partition('/dev/nvme0n1p7', {'old_root_uuid': ROOT_UUID})
            run.assert_called_once_with('blkid', '-s', 'UUID', '-o', 'value', '/dev/nvme0n1p7')

    def test_existing_luks_partition_uses_outer_uuid(self):
        plan = {'old_root_uuid': ROOT_UUID, 'old_partition_fs_uuid': LUKS_UUID}
        with patch.object(installer, 'run', return_value=LUKS_UUID + '\n'):
            installer.check_old_partition('/dev/nvme0n1p7', plan)
        with patch.object(installer, 'run', return_value=ROOT_UUID + '\n'):
            with self.assertRaisesRegex(ValueError, 'Target filesystem changed'):
                installer.check_old_partition('/dev/nvme0n1p7', plan)
        self.assertEqual(plan['old_root_uuid'], ROOT_UUID)

    def test_chroot_cannot_be_mistaken_for_booting_the_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'repo').mkdir()
            payload = source / 'repo/kernel-test.rpm'
            payload.write_bytes(b'test payload')
            (source / 'rpm-manifest.json').write_text(json.dumps({
                'sha256': {payload.name: hashlib.sha256(payload.read_bytes()).hexdigest()}}))
            plan = {'source_uuid': 'not-the-real-kernel-root', 'source_serial': 'test-usb'}
            (source / 'test-target.json').write_text(json.dumps(plan))
            mount = {'uuid': plan['source_uuid'], 'source': '/dev/sda2'}
            nodes = [{'type': 'disk', 'path': '/dev/sda', 'serial': 'test-usb'}]
            with patch.object(installer, 'SOURCE', source), \
                 patch.object(installer, 'mount_info', return_value=mount), \
                 patch.object(installer, 'ancestors', return_value=nodes), \
                 patch.object(installer, 'resolve_partition') as resolve:
                with self.assertRaisesRegex(ValueError, 'running kernel'):
                    installer.preflight()
                resolve.assert_not_called()

    def test_unused_rpm_initrd_is_removed_without_touching_current_image(self):
        with tempfile.TemporaryDirectory() as directory:
            esp = Path(directory)
            token, kernel = 'a' * 32, '7.2.0-rc2-gaokun3+'
            artifact = esp / token / kernel
            entries = esp / 'loader/entries'
            artifact.mkdir(parents=True)
            entries.mkdir(parents=True)
            (artifact / 'initrd').write_bytes(b'old')
            (artifact / 'current.img').write_bytes(b'current')
            (entries / 'current.conf').write_text(f'initrd /{token}/{kernel}/current.img\n')
            self.assertEqual(installer.discard_unused_initrd(esp, token, kernel), 3)
            self.assertFalse((artifact / 'initrd').exists())
            self.assertEqual((artifact / 'current.img').read_bytes(), b'current')

    def test_fallback_entry_keeps_rpm_initrd(self):
        with tempfile.TemporaryDirectory() as directory:
            esp = Path(directory)
            token, kernel = 'a' * 32, '7.2.0-rc2-gaokun3+'
            artifact = esp / token / kernel
            entries = esp / 'loader/entries'
            artifact.mkdir(parents=True)
            entries.mkdir(parents=True)
            (artifact / 'initrd').write_bytes(b'fallback')
            (entries / 'fallback.conf').write_text(f'initrd /{token}/{kernel}/initrd\n')
            self.assertEqual(installer.discard_unused_initrd(esp, token, kernel), 0)
            self.assertTrue((artifact / 'initrd').exists())

    def test_initrd_cleanup_rejects_invalid_namespace(self):
        with self.assertRaises(ValueError):
            installer.discard_unused_initrd('/nonexistent', 'a' * 32, '../outside')

    def test_encrypted_boot_includes_spi_touchscreen_driver(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'TARGET', Path(directory)), \
             patch.object(installer, 'SOURCE', Path(__file__).resolve().parents[1] / 'tools/installer'):
            installer.configure_boot_inputs({'installation_id': 'a' * 32},
                                             {'uuid': ROOT_UUID, 'fsroot': '/root'}, [LUKS_UUID])
            config = (Path(directory) / 'etc/dracut.conf.d/91-gaokun3-luks.conf').read_text()
            for driver in ('dm_crypt', 'btrfs', 'nvme', 'usbhid', 'msm',
                           'panel-himax-hx83121a', 'himax_hx83121a_spi'):
                self.assertIn(driver, config)

    def test_network_and_ssh_require_explicit_opt_in(self):
        self.assertEqual(installer.DEFAULT_POLICY, {'copy_network': False, 'enable_ssh': False})
        for retain in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profile = root / 'etc/NetworkManager/system-connections/test.nmconnection'
                profile.parent.mkdir(parents=True)
                profile.write_text('synthetic saved network credentials')
                outside = root / 'unrelated'
                outside.write_text('preserve')
                removed = installer.apply_network_policy(root, {'copy_network': retain, 'enable_ssh': retain})
                self.assertEqual(removed, 0 if retain else 1)
                self.assertEqual(profile.exists(), retain)
                self.assertEqual(outside.read_text(), 'preserve')

    def test_policy_rejects_live_root_invalid_types_and_escaping_directory(self):
        with self.assertRaises(ValueError):
            installer.apply_network_policy('/', installer.DEFAULT_POLICY)
        for value in ({}, {'copy_network': 'false', 'enable_ssh': False}):
            with self.assertRaises(ValueError): installer.validate_install_policy(value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc/NetworkManager').mkdir(parents=True)
            (root / 'etc/NetworkManager/system-connections').symlink_to('/tmp')
            with self.assertRaises(ValueError):
                installer.apply_network_policy(root, installer.DEFAULT_POLICY)

    def test_workstation_login_requirements_are_explicit(self):
        ks = (Path(__file__).resolve().parents[1] / 'tools/installer/interactive-defaults.ks').read_text()
        self.assertNotIn('--exclude-weakdeps', ks)
        for package in ('systemd-pam', 'dbus-daemon', 'cracklib-dicts', 'openssh-server'):
            self.assertIn(package, ks.splitlines())
        self.assertIn('--disabled=sshd', ks)

    def test_initrd_validation_rejects_empty_successful_extraction(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in installer.REQUIRED_INITRD_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'expected member')
            with patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=b'')):
                with self.assertRaisesRegex(ValueError, 'missing or differs'):
                    installer.validate_initrd_payload(root, '/boot/test.img')
            with patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=b'expected member')):
                installer.validate_initrd_payload(root, '/boot/test.img')

    def test_atomic_write_replaces_final_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            outside = base / 'original'
            outside.write_text('preserve')
            destination = base / 'config'
            destination.symlink_to(outside)
            installer.write(destination, 'new config')
            self.assertFalse(destination.is_symlink())
            self.assertEqual(outside.read_text(), 'preserve')
            self.assertEqual(destination.read_text(), 'new config')


if __name__ == '__main__':
    unittest.main()
