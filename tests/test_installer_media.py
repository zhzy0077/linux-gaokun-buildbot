"""Static media composition contracts and offline package selection."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('media',ROOT/'tools/installer/build-media.py')
media=importlib.util.module_from_spec(spec);spec.loader.exec_module(media)


class InstallerMediaTests(unittest.TestCase):
    def test_offline_target_selection_uses_four_gaokun_packages(self):
        packages,exclude=media.package_selection((ROOT/'tools/installer/interactive-defaults.ks').read_text())
        self.assertIn('@^workstation-product-environment',packages)
        for name in ('kernel-gaokun3-el2','kernel-modules-gaokun3-el2','linux-firmware-gaokun3','gaokun3-platform'):
            self.assertIn(name,packages)
        self.assertNotIn('anaconda-live',packages)
        self.assertIn('kernel-core',exclude)
        self.assertNotIn('sdubby',exclude)

    def test_hardware_boot_options_are_rendered_from_one_source(self):
        template=(ROOT/'tools/installer/interactive-defaults.ks').read_text()
        hardware=(ROOT/'tools/boot/platform-cmdline').read_text()
        rendered=media.render_defaults(template,hardware)
        self.assertNotIn('@PLATFORM_CMDLINE@',rendered)
        self.assertIn('--append="'+hardware.strip()+'"',rendered)
        with self.assertRaises(ValueError):media.render_defaults(template,'root="bad"')

    def test_autostart_and_desktop_open_anaconda_without_extra_dialogs(self):
        launcher=(ROOT/'tools/installer/gaokun-install').read_text()
        backend=(ROOT/'tools/installer/gaokun-install-backend').read_text()
        image=(ROOT/'scripts/ci/52_build_installer_image.sh').read_text()
        self.assertNotIn('zenity',launcher)
        self.assertIn('exec pkexec /usr/local/libexec/gaokun-install-backend',launcher)
        self.assertIn('/opt/installer/gaokun_installer.py preflight',backend)
        self.assertIn('flock -n 8',backend)
        self.assertIn('exec /usr/bin/liveinst',backend)
        self.assertNotIn('--copy-network',backend)
        self.assertNotIn('--enable-ssh',backend)
        self.assertIn('cp /usr/share/applications/gaokun-install.desktop /home/installer/.config/autostart/',image)

    def test_live_privilege_is_only_for_active_session_launcher(self):
        tree=ET.parse(ROOT/'tools/installer/org.gaokun.installer.policy')
        action=tree.getroot().find('action')
        self.assertEqual(action.find('defaults/allow_any').text,'no')
        self.assertEqual(action.find('defaults/allow_inactive').text,'no')
        self.assertEqual(action.find('defaults/allow_active').text,'yes')
        self.assertEqual(action.find('annotate').text,'/usr/local/libexec/gaokun-install-backend')
        platform=(ROOT/'packaging/rpm/gaokun3-platform.spec.in').read_text()
        self.assertNotIn('org.gaokun.installer.policy',platform)

    def test_image_formats_only_new_image_and_allocated_loop(self):
        script=(ROOT/'scripts/ci/52_build_installer_image.sh').read_text()
        self.assertIn('Refusing to overwrite an existing image',script)
        self.assertIn('losetup --find --show --partscan "$IMAGE"',script)
        self.assertIn('losetup --noheadings --output BACK-FILE "$LOOP"',script)
        self.assertNotIn('/dev/nvme',script)
        self.assertNotIn('/dev/sda',script)
        self.assertIn('mktemp -d "$WORKDIR/mount.',script)
        self.assertIn('systemctl disable sshd.service',script)
        self.assertNotIn('user:user',script)
        self.assertIn('bootctl --no-variables',script)
        self.assertIn('/home/installer/.config/gnome-initial-setup-done',script)
        self.assertIn('vfat defaults,umask=0077',script)
        self.assertIn('rm -f /boot/efi/loader/random-seed /var/lib/systemd/random-seed',script)

    def test_desktop_image_still_supplies_kernel_integration_dependency(self):
        workflow=(ROOT/'.github/workflows/fedora-gaokun3-release.yml').read_text()
        self.assertIn(".packages.platform",workflow)
        self.assertIn('$PACKAGE_RPMS_DIR/$platform_rpm_name',workflow)
        image=(ROOT/'scripts/ci/50_make_image_fedora.sh').read_text()
        self.assertNotIn('kernel-install --entry-token=machine-id remove',image)
        self.assertIn('"$krel" "$image" "/boot/initramfs-${krel}.img"',image)

    def test_rpm_capability_query_uses_name_not_dependency_expression(self):
        source=(ROOT/'tools/installer/build-media.py').read_text()
        self.assertIn("'--whatprovides', 'gaokun3-boot-integration'",source)
        self.assertNotIn("'--whatprovides', 'gaokun3-boot-integration >= 1'",source)
        workflow=(ROOT/'.github/workflows/gaokun3-installer-image.yml').read_text()
        self.assertIn('inputs.package_release_tag',workflow)

    def test_installer_uses_stock_anaconda_sdubby(self):
        boot=(ROOT/'tools/boot/kernel-setup.py').read_text()
        installer=(ROOT/'tools/installer/gaokun_installer.py').read_text()
        self.assertNotIn('updateloaderentries',boot+installer)
        self.assertFalse(list((ROOT/'packaging/rpm').glob('*sdubby*')))
        spec=(ROOT/'packaging/rpm/gaokun3-platform.spec.in').read_text()
        self.assertIn('Provides:       gaokun3-boot-integration = 1',spec)
        self.assertIn('tools/el2/$file',spec)


if __name__=='__main__':unittest.main()
