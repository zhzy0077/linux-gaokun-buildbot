"""Exercise first-boot display setup without touching system accounts or GDM."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'tools/monitors/gdm-monitor-sync'


class MonitorSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'greeter-home'
        self.home.mkdir()
        self.source = self.root / 'monitors.xml'
        self.source.write_text('<monitors version="2"/>\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        getent = self.bin / 'getent'
        getent.write_text('''#!/bin/sh
[ "$1" = passwd ] && [ "$2" = "$TEST_GDM_USER" ] || exit 2
printf '%s:x:%s:%s::%s:/sbin/nologin\\n' "$2" "$TEST_UID" "$TEST_GID" "$TEST_GDM_HOME"
''')
        getent.chmod(0o755)
        restorecon = self.bin / 'restorecon'
        restorecon.write_text('#!/bin/sh\nexit 0\n')
        restorecon.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
                        TEST_GDM_USER='gdm', TEST_GDM_HOME=str(self.home),
                        TEST_UID=str(os.getuid()), TEST_GID=str(os.getgid()))

    def run_sync(self):
        return subprocess.run(['bash', str(SCRIPT), str(self.source)],
                              env=self.env, capture_output=True, text=True)

    def test_seeds_gdm_before_any_desktop_user_exists(self):
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.home / '.config/monitors.xml'
        self.assertEqual(destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(destination.stat().st_uid, os.getuid())

    def test_preserves_existing_display_preferences(self):
        self.assertEqual(self.run_sync().returncode, 0)
        destination = self.home / '.config/monitors.xml'
        destination.write_text('user-selected display settings')
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(destination.read_text(), 'user-selected display settings')

    def test_missing_legacy_home_is_normal_with_transient_gdm_users(self):
        self.home.rmdir()
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertFalse(self.home.exists())

    def test_supports_ubuntu_gdm_account(self):
        self.env['TEST_GDM_USER'] = 'Debian-gdm'
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertTrue((self.home / '.config/monitors.xml').is_file())

    def test_no_gdm_account_leaves_home_untouched(self):
        self.env['TEST_GDM_USER'] = 'nobody'
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertFalse((self.home / '.config').exists())

    def test_does_not_follow_config_directory_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.home / '.config').symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_sync().returncode, 0)
        self.assertFalse((outside / 'monitors.xml').exists())

    def test_preserves_existing_monitor_symlink(self):
        (self.home / '.config').mkdir()
        destination = self.home / '.config/monitors.xml'
        destination.symlink_to(self.root / 'missing-administrator-config')
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertTrue(destination.is_symlink())
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
