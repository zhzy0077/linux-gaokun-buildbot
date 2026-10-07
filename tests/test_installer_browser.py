"""Single-viewer integration uses Fedora-owned Live browser styling."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerBrowserTests(unittest.TestCase):
    def test_browser_uses_stock_live_theme_in_private_runtime_profile(self):
        script = ROOT / 'tools/installer/gaokun-install-browser'
        subprocess.run(['bash', '-n', str(script)], check=True)
        text = script.read_text()
        self.assertIn('mktemp -d "${XDG_RUNTIME_DIR:?', text)
        self.assertIn('/usr/share/anaconda/firefox-theme/live/.', text)
        self.assertIn('/usr/bin/firefox --new-instance --profile "$profile" "$@"', text)
        self.assertIn('MOZ_APP_REMOTINGNAME=liveinst', text)
        self.assertNotIn('webui-desktop', text)
        self.assertNotIn('rm -rf', text)

    def test_configured_browser_is_packaged_as_executable(self):
        config = (ROOT / 'tools/installer/90-gaokun.conf').read_text()
        source = (ROOT / 'tools/installer/build-media.py').read_text()
        backend = (ROOT / 'tools/installer/gaokun-install-backend').read_text()
        self.assertIn('webui_web_engine = /usr/local/libexec/gaokun-install-browser', config)
        self.assertIn("'gaokun-install-browser': '/usr/local/libexec/gaokun-install-browser'", source)
        self.assertIn("'gaokun-install-browser') else 0o644", source)
        self.assertIn('exec /usr/bin/anaconda --graphical', backend)
        self.assertNotIn('exec /usr/bin/liveinst', backend)


if __name__ == '__main__':
    unittest.main()
