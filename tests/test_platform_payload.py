"""Source/patch/package contracts established by native Fedora validation."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PlatformPayloadTests(unittest.TestCase):
    def test_firewall_config_is_mirrored_in_ci_patch(self):
        config = ROOT/'defconfig/gaokun3_defconfig'
        self.assertIn('CONFIG_NF_CONNTRACK_NETBIOS_NS=m', config.read_text().splitlines())
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git','apply','--include=arch/arm64/configs/gaokun3_defconfig',
                            str(ROOT/'patches/0099-arm64-gaokun3-import-local-dts-and-defconfig.patch')],
                           cwd=directory,check=True,capture_output=True)
            self.assertEqual((Path(directory)/'arch/arm64/configs/gaokun3_defconfig').read_bytes(),config.read_bytes())

    def test_added_firmware_is_original_unpersonalized_payload(self):
        manifest=json.loads((ROOT/'docs/firmware-wcn6855-source.json').read_text())
        for entry in manifest['added_missing_firmware']:
            path=ROOT/'firmware/qca'/Path(entry['path']).name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),entry['sha256'],str(path))

    def test_ucm_environment_covers_both_audio_processes(self):
        spec=(ROOT/'packaging/rpm/gaokun3-platform.spec.in').read_text()
        self.assertIn('for service in pipewire wireplumber; do',spec)
        for service in ('pipewire','wireplumber'):
            self.assertIn('%{_userunitdir}/'+service+'.service.d/50-gaokun3-ucm.conf',spec)
        self.assertIn('Environment=ALSA_CONFIG_UCM2=%{_datadir}/gaokun3/ucm2',spec)

    def test_transient_display_defaults_and_early_pcm_override_are_packaged(self):
        spec=(ROOT/'packaging/rpm/gaokun3-platform.spec.in').read_text()
        for name in ('%{_sysconfdir}/xdg/monitors.xml','%{_sysconfdir}/modprobe.d/dist-alsa.conf',
                     '93-gaokun-bluetooth.conf','94-gaokun-alsa.conf'):
            self.assertIn(name,spec)
        self.assertIn('monitors.xml:/etc/xdg/monitors.xml', (ROOT/'scripts/ci/lib/common_image.sh').read_text())
        dracut=(ROOT/'tools/audio/94-gaokun-alsa.conf').read_text()
        self.assertIn('/etc/modprobe.d/dist-alsa.conf',dracut)
        hook=(ROOT/'tools/bluetooth/bluetooth-nvm.conf').read_text()
        self.assertLess(hook.index('patch-nvm-bdaddr.py'),hook.index('--ignore-install hci_uart'))

if __name__ == '__main__':
    unittest.main()
