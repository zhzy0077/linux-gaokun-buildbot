"""RPM integration tests use temporary target roots and mocked command execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('boot',ROOT/'tools/boot/kernel-setup.py')
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
UUID='22222222-2222-4222-8222-222222222222'
LUKS='33333333-3333-4333-8333-333333333333'
KERNEL='7.2.9-gaokun3-el2+'
DTB='qcom/sc8280xp-huawei-gaokun3-el2.dtb'


class BootSetupTests(unittest.TestCase):
    def test_cmdline_from_target_fstab_and_crypt_graph(self):
        row=boot.root_entry(f'UUID={UUID} / btrfs subvol=root,compress=zstd:1 0 0\n')
        cmdline=boot.make_cmdline(row,'efi=noruntime quiet',[LUKS])
        self.assertIn('root=UUID='+UUID,cmdline)
        self.assertIn('rootflags=subvol=root',cmdline)
        self.assertIn('rd.luks.uuid=luks-'+LUKS,cmdline)
        self.assertNotIn('compress=',cmdline)
        nodes=[{'fstype':'btrfs','uuid':UUID,'children':[{'fstype':'crypto_LUKS','uuid':LUKS}]}]
        self.assertEqual(boot.luks_ancestors(nodes),[LUKS])

    def test_btrfs_name_and_id_in_fstab_are_supported(self):
        value=boot.make_cmdline([f'UUID={UUID}','/','btrfs','subvolid=256,subvol=/root'],'quiet',[])
        self.assertIn('rootflags=subvol=/root',value)

    def test_unencrypted_ext4_has_no_subvolume_or_luks_requirement(self):
        cmdline=boot.make_cmdline([f'UUID={UUID}','/','ext4','defaults'],'quiet',[])
        self.assertNotIn('rootflags=',cmdline)
        self.assertNotIn('rd.luks',cmdline)

    def test_rejects_ambiguous_root_and_platform_disk_arguments(self):
        with self.assertRaises(ValueError):boot.root_entry('')
        with self.assertRaises(ValueError):boot.root_entry('UUID=a / btrfs defaults\nUUID=b / btrfs defaults')
        with self.assertRaises(ValueError):boot.make_cmdline([f'UUID={UUID}','/','btrfs','subvol=root'],'root=/dev/sda',[])

    def test_existing_admin_cmdline_is_preserved(self):
        with tempfile.TemporaryDirectory() as d, patch.object(boot,'ROOT',Path(d)), patch.object(boot,'run') as run:
            p=Path(d)/'etc/kernel/cmdline';p.parent.mkdir(parents=True);p.write_text('root=UUID=custom quiet\n')
            self.assertEqual(boot.target_cmdline(),p.read_text())
            run.assert_not_called()

    def test_new_cmdline_never_reads_installation_proc_cmdline(self):
        with tempfile.TemporaryDirectory() as d, patch.object(boot,'ROOT',Path(d)), \
             patch.object(boot,'DATA',Path(d)/'data'), patch.object(boot,'run') as run:
            root=Path(d);(root/'etc').mkdir();(root/'data').mkdir()
            (root/'etc/fstab').write_text(f'UUID={UUID} / btrfs subvol=root 0 0\n')
            (root/'data/platform-cmdline').write_text('quiet\n')
            run.side_effect=['/dev/mapper/target',json.dumps({'blockdevices':[{'fstype':'crypto_LUKS','uuid':LUKS}]})]
            value=boot.target_cmdline()
            self.assertIn(UUID,value);self.assertIn(LUKS,value)
            self.assertEqual((root/'etc/kernel/cmdline').read_text(),value)

    def test_identity_is_initialized_once(self):
        with tempfile.TemporaryDirectory() as d, patch.object(boot,'ROOT',Path(d)):
            first=boot.entry_identity();second=boot.entry_identity()
            self.assertEqual(first,second);self.assertRegex(first,'^[0-9a-f]{32}$')

    def test_unmounted_image_build_skips_boot_installation(self):
        with tempfile.TemporaryDirectory() as d, patch.object(boot,'ROOT',Path(d)), \
             patch.object(boot.subprocess,'run') as run:
            boot.install(KERNEL,DTB)
            run.assert_not_called()

    def test_wrong_kernel_dtb_pair_is_rejected(self):
        with self.assertRaises(ValueError):boot.install(KERNEL,'qcom/sc8280xp-huawei-gaokun3.dtb')

    def test_only_one_dracut_and_no_bootctl(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);esp=root/'boot/efi';esp.mkdir(parents=True)
            (root/'etc').mkdir();(root/'etc/fstab').write_text(f'UUID={UUID} / btrfs subvol=root 0 0\n')
            (root/'boot'/('vmlinuz-'+KERNEL)).write_bytes(b'kernel')
            dtb=root/'usr/lib/modules'/KERNEL/'dtb'/DTB;dtb.parent.mkdir(parents=True);dtb.write_bytes(b'dtb')
            def command(args,**kwargs):
                if args[0]=='dracut':Path(args[-1]).write_bytes(b'initrd')
                return subprocess.CompletedProcess(args,0)
            with patch.object(boot,'ROOT',root), patch.object(boot,'ESP',esp), \
                 patch.object(boot,'mounted_esp',return_value=True), \
                 patch.object(boot,'target_cmdline',return_value=f'root=UUID={UUID}\n'), \
                 patch.object(boot,'efi_assets',return_value=[]), \
                 patch.object(boot.subprocess,'run',side_effect=command) as run:
                boot.install(KERNEL,DTB)
                commands=[c.args[0] for c in run.call_args_list]
                self.assertEqual(sum(c[0]=='dracut' for c in commands),1)
                self.assertFalse(any(c[0]=='bootctl' for c in commands))
                install=next(c for c in commands if c[0]=='kernel-install')
                self.assertEqual(install[-1],str(root/'boot'/('initramfs-'+KERNEL+'.img')))

    def test_no_delete_before_kernel_install_in_rpm(self):
        spec=(ROOT/'packaging/rpm/kernel-gaokun3.spec.in').read_text().split('%posttrans',1)[1].split('%preun',1)[0]
        self.assertIn('kernel-setup @KREL@',spec)
        self.assertNotIn('remove',spec)
        self.assertNotIn('dracut --force',spec)


if __name__=='__main__':unittest.main()
