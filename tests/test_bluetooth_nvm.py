"""Firmware parser, per-machine identity and atomic update regression tests."""
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nvm', ROOT/'tools/bluetooth/patch-nvm-bdaddr.py')
nvm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvm)


def sample(address=b'\xad\x5a\0\0\0\0'):
    body = struct.pack('<HH8x', 2, 6) + address
    return struct.pack('<I', (len(body) << 8) | 2) + body


class NvmTests(unittest.TestCase):
    def test_plain_and_nested_tlv(self):
        data = sample()
        self.assertEqual(nvm.parse_nvm_find_bdaddr(data), 16)
        nested = struct.pack('<I', (len(data) << 8) | 4) + data
        self.assertEqual(nvm.parse_nvm_find_bdaddr(nested), 20)

    def test_rejects_truncated_unsupported_and_duplicate_tags(self):
        data = sample()
        duplicate = struct.pack('<I', (36 << 8) | 2) + data[4:] * 2
        for bad in (data[:-1], data[:3], bytes([9]) + data[1:], duplicate,
                    struct.pack('<I', (999 << 8) | 2) + data[4:]):
            self.assertIsNone(nvm.parse_nvm_find_bdaddr(bad))

    def test_atomic_patch_preserves_alias_and_original_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'hpnv21.bin'
            original = sample(); path.write_bytes(original)
            alias = path.with_name('unrelated.bin'); os.link(path, alias)
            desired = nvm.generate_bdaddr('test-device')
            self.assertEqual(desired[0] & 3, 2)
            self.assertTrue(nvm.patch_file(path, desired))
            self.assertEqual(path.read_bytes(), original[:16] + desired[::-1])
            self.assertEqual(alias.read_bytes(), original)
            self.assertEqual(path.with_name(path.name+'.orig').read_bytes(), original)
            self.assertFalse(nvm.patch_file(path, desired))
            self.assertTrue(nvm.patch_file(path, nvm.generate_bdaddr('other-device')))
            self.assertEqual(path.with_name(path.name+'.orig').read_bytes(), original)

    def test_all_wcn6855_variants_without_backup_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(nvm, 'FIRMWARE_DIR', Path(directory)):
            names = ['hpnv21.bin','hpnv21.b8c','hpnv21g.bin','wcnhpnv21.bin','wcnhpnv21g.bin']
            for name in names + ['hpnv21.bin.orig', 'hpnv22.bin']:
                (Path(directory)/name).write_bytes(sample())
            self.assertEqual({p.name for p in nvm.iter_nvm_files()}, set(names))

    def test_rejects_firmware_link_outside_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); firmware = root/'qca'; firmware.mkdir()
            (root/'outside').write_bytes(sample())
            (firmware/'hpnv21.bin').symlink_to(root/'outside')
            with patch.object(nvm,'FIRMWARE_DIR',firmware), self.assertRaises(ValueError):
                list(nvm.iter_nvm_files())

    def test_refuses_symlink_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'hpnv21.bin'; p.write_bytes(sample())
            p.with_name(p.name+'.orig').symlink_to(Path(directory)/'outside')
            with self.assertRaises(ValueError): nvm.patch_file(p, nvm.generate_bdaddr('test'))
            self.assertEqual(p.read_bytes(), sample())

    def test_fallback_identity_is_unique_and_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); machine=root/'machine-id'
            with patch.object(nvm,'SERIAL_PATH',root/'absent'), patch.object(nvm,'MACHINE_ID_PATH',machine):
                machine.write_text('a'*32)
                first=nvm.generate_bdaddr(nvm.read_serial())
                machine.write_text('b'*32)
                self.assertNotEqual(first,nvm.generate_bdaddr(nvm.read_serial()))
                machine.write_text('0'*32)
                with self.assertRaises(ValueError): nvm.read_serial()


if __name__ == '__main__':
    unittest.main()
