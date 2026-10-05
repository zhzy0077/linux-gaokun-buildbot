#!/usr/bin/python3
"""Prepare a scoped Fedora RPM installation and configure Gaokun LUKS boot."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

SOURCE = Path('/opt/installer')
RUNTIME = Path('/run/gaokun-installer')
TARGET = Path('/mnt/sysroot')
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
DTB = 'qcom/sc8280xp-huawei-gaokun3.dtb'
HARDWARE_KEYS = {
    'clk_ignore_unused', 'pd_ignore_unused', 'arm64.nopauth', 'iommu.passthrough',
    'iommu.strict', 'pcie_aspm.policy', 'efi', 'fbcon', 'usbhid.quirks',
    'consoleblank', 'loglevel', 'psi',
}


def run(*args, capture=True):
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.chmod(mode)
    temporary.replace(path)


def mount_info(path, exact=False):
    option = '--mountpoint' if exact else '--target'
    mounts = json.loads(run('findmnt', '--json', option, str(path), '--output',
                            'SOURCE,TARGET,FSTYPE,UUID,FSROOT,OPTIONS'))['filesystems']
    if len(mounts) != 1:
        raise ValueError(f'Expected one mount for {path}')
    return mounts[0]


def device_path(mount):
    return mount['source'].split('[', 1)[0]


def ancestors(device):
    result = json.loads(run('lsblk', '--json', '--inverse', '--paths', '--output',
                            'NAME,PATH,TYPE,FSTYPE,UUID,PARTUUID,SERIAL', device))
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children', []))
    return list(walk(result['blockdevices']))


def disk_paths(nodes):
    return {node['path'] for node in nodes if node['type'] == 'disk'}


def check_target(root, esp, root_nodes, esp_nodes, source):
    if root['uuid'] == source['uuid']:
        raise ValueError('The installation target is the running USB system')
    root_disks, esp_disks = disk_paths(root_nodes), disk_paths(esp_nodes)
    if not root_disks or not esp_disks:
        raise ValueError('Cannot identify target physical disks')
    if (root_disks | esp_disks) & set(source['disks']):
        raise ValueError('Target root or EFI partition is on the installer USB')
    if len(root_disks) != 1 or esp_disks != root_disks:
        raise ValueError('Root and EFI must be on the same target disk')
    if root['fstype'] != 'btrfs' or esp['fstype'] != 'vfat':
        raise ValueError('Expected Btrfs root and a FAT EFI partition')
    luks = sorted({n['uuid'] for n in root_nodes if n['fstype'] == 'crypto_LUKS' and n['uuid']})
    if not luks:
        raise ValueError('Root is not encrypted')
    if any(n['type'] not in {'part', 'disk', 'crypt'} for n in root_nodes):
        raise ValueError('This test supports one LUKS partition on one disk')
    return luks


def kernel_cmdline(root_uuid, fsroot, luks_uuids, hardware):
    uuid.UUID(root_uuid)
    subvolume = fsroot.lstrip('/')
    if not subvolume or not re.fullmatch(r'[a-zA-Z0-9_@./+-]+', subvolume):
        raise ValueError('Expected a named Btrfs root subvolume')
    tokens = shlex.split(hardware)
    if any(t.split('=', 1)[0] not in HARDWARE_KEYS for t in tokens):
        raise ValueError('Unexpected disk or installer argument in hardware cmdline')
    args = [f'root=UUID={root_uuid}', f'rootflags=subvol={subvolume}', 'rw']
    for luks_uuid in luks_uuids:
        uuid.UUID(luks_uuid)
        args.append(f'rd.luks.uuid=luks-{luks_uuid}')
    return ' '.join(args + tokens) + '\n'


def scoped_disk_selection(devices):
    if len(devices) != 1 or not re.fullmatch(r'/dev/[a-zA-Z0-9_-]+', devices[0]):
        raise ValueError('Expected one supported target disk device name')
    # WebUI 68 understands AUTOMATIC and MANUAL partitioning, while Kickstart
    # part/btrfs directives create CUSTOM and cause its startup to fail.
    # Use the integrated storage editor and mount-point assignment instead.
    return f'ignoredisk --only-use={Path(devices[0]).name}\nclearpart --none\n'


def resolve_partition(partuuid):
    uuid.UUID(partuuid)
    path = Path('/dev/disk/by-partuuid') / partuuid
    if not path.exists():
        raise ValueError('Expected partition is missing: ' + partuuid)
    return str(path.resolve())


DEFAULT_POLICY = {'copy_network': False, 'enable_ssh': False}


def validate_install_policy(policy):
    if not isinstance(policy, dict) or set(policy) != set(DEFAULT_POLICY):
        raise ValueError('Invalid installation policy keys')
    if any(type(value) is not bool for value in policy.values()):
        raise ValueError('Installation policy values must be booleans')
    return dict(policy)


def apply_network_policy(target, policy):
    """Remove Anaconda-copied keyfiles on the new root unless explicitly kept."""
    policy = validate_install_policy(policy)
    target = Path(target).resolve()
    if target == Path('/'):
        raise ValueError('Refusing to apply installation policy to the running root')
    directory = target / 'etc/NetworkManager/system-connections'
    if not directory.resolve().is_relative_to(target):
        raise ValueError('Network profile directory escapes the installation target')
    if policy['copy_network'] or not directory.exists():
        return 0
    files = list(directory.iterdir())
    if any(path.is_dir() and not path.is_symlink() for path in files):
        raise ValueError('Unexpected subdirectory in NetworkManager profiles')
    for path in files:
        path.unlink()
    return len(files)


def read_install_policy():
    path = RUNTIME / 'policy.json'
    return validate_install_policy(json.loads(path.read_text()) if path.exists() else DEFAULT_POLICY)


def check_old_partition(root_device, plan):
    # For LUKS, blkid sees the outer container UUID; BLS uses the inner Btrfs UUID.
    expected = plan.get('old_partition_fs_uuid', plan['old_root_uuid'])
    if run('blkid', '-s', 'UUID', '-o', 'value', root_device).strip() != expected:
        raise ValueError('Target filesystem changed; review the installation plan again')


def preflight():
    plan = json.loads((SOURCE / 'test-target.json').read_text())
    manifest = json.loads((SOURCE / 'rpm-manifest.json').read_text())
    for name, expected in manifest['sha256'].items():
        if Path(name).name != name or not name.endswith('.rpm'):
            raise ValueError('Invalid package name in manifest')
        if digest(SOURCE / 'repo' / name) != expected:
            raise ValueError('RPM SHA-256 mismatch: ' + name)
    root_mount = mount_info('/')
    repo_mount = mount_info(SOURCE / 'repo')
    source_nodes = ancestors(device_path(root_mount))
    source_disks = disk_paths(source_nodes)
    serials = {n['serial'] for n in source_nodes if n['type'] == 'disk'}
    if root_mount['uuid'] != plan['source_uuid'] or repo_mount['uuid'] != root_mount['uuid']:
        raise ValueError('Boot the prepared USB before starting this installer')
    if 'root=UUID=' + plan['source_uuid'] not in Path('/proc/cmdline').read_text().split():
        raise ValueError('The running kernel was not booted from the prepared USB')
    if serials != {plan['source_serial']}:
        raise ValueError('Unexpected installer USB identity')
    root_device = resolve_partition(plan['root_partuuid'])
    esp_device = resolve_partition(plan['esp_partuuid'])
    target_nodes = ancestors(root_device)
    if disk_paths(target_nodes) & source_disks:
        raise ValueError('The target is on the installer USB')
    if {n['serial'] for n in target_nodes if n['type'] == 'disk'} != {plan['target_serial']}:
        raise ValueError('Target disk serial mismatch')
    if disk_paths(ancestors(esp_device)) != disk_paths(target_nodes):
        raise ValueError('Unexpected EFI disk')
    check_old_partition(root_device, plan)
    if run('blkid', '-s', 'UUID', '-o', 'value', esp_device).strip() != plan['esp_uuid']:
        raise ValueError('EFI filesystem identity changed')
    if run('blockdev', '--getsize64', root_device).strip() != str(plan['root_size_bytes']):
        raise ValueError('Target partition size changed')
    state = {'uuid': root_mount['uuid'], 'disks': sorted(source_disks), 'plan': plan,
             'machine_id': Path('/etc/machine-id').read_text().strip(),
             'installation_id': uuid.uuid4().hex, 'policy': read_install_policy()}
    write(RUNTIME / 'source.json', json.dumps(state), 0o600)
    write(RUNTIME / 'storage.ks', scoped_disk_selection(sorted(disk_paths(target_nodes))))
    print(f'Prepared installation into {root_device}; reuse {esp_device} without formatting.')


def target_state():
    source = json.loads((RUNTIME / 'source.json').read_text())
    root = mount_info(TARGET, exact=True)
    esp = mount_info(TARGET / 'boot/efi', exact=True)
    nodes = ancestors(device_path(root))
    esp_nodes = ancestors(device_path(esp))
    luks = check_target(root, esp, nodes, esp_nodes, source)
    plan = source['plan']
    parts = {n['partuuid'] for n in nodes if n['type'] == 'part'}
    if parts != {plan['root_partuuid']}:
        raise ValueError('Target root is outside the approved partition')
    if {n['partuuid'] for n in esp_nodes if n['type'] == 'part'} != {plan['esp_partuuid']}:
        raise ValueError('Target EFI partition is outside the approved plan')
    if esp['uuid'] != plan['esp_uuid']:
        raise ValueError('The existing EFI filesystem must not be formatted')
    if run('lsblk', '-dn', '-o', 'PARTTYPE', device_path(esp)).strip().lower() != ESP_TYPE:
        raise ValueError('Expected a GPT EFI System Partition')
    return source, root, esp, luks


def configure_boot_inputs(source, root, luks):
    hardware = (SOURCE / 'platform-cmdline').read_text()
    cmdline = kernel_cmdline(root['uuid'], root['fsroot'], luks, hardware)
    token = source['installation_id']
    write(TARGET / 'etc/machine-id', token + '\n', 0o444)
    write(TARGET / 'etc/kernel/entry-token', token + '\n')
    write(TARGET / 'etc/kernel/cmdline', cmdline)
    write(TARGET / 'etc/kernel/devicetree', DTB + '\n')
    write(TARGET / 'etc/kernel/install.conf', 'layout=bls\n')
    for plugin in ('20-grub.install', '51-dracut-rescue.install',
                   '95-set-boot-entry.install', '99-grub-mkconfig.install'):
        write(TARGET / 'etc/kernel/install.d' / plugin, '#!/bin/sh\nexit 0\n', 0o755)
    write(TARGET / 'etc/dracut.conf.d/91-gaokun3-luks.conf',
          'hostonly="no"\nhostonly_cmdline="no"\nadd_dracutmodules+=" crypt "\n'
          'add_drivers+=" dm_crypt btrfs nvme usbhid i2c-hid-of hid-multitouch msm panel-himax-hx83121a himax_hx83121a_spi "\n')
    return token, cmdline


def backup_efi(source):
    esp = TARGET / 'boot/efi'
    backup = SOURCE / 'backups' / source['installation_id'] / 'efi'
    if backup.exists():
        raise ValueError('EFI backup already exists; review the interrupted installation')
    shutil.copytree(esp, backup)
    for file in esp.rglob('*'):
        if file.is_file() and digest(file) != digest(backup / file.relative_to(esp)):
            raise ValueError('EFI backup verification failed: ' + str(file))
    run('sync', '-f', str(backup), capture=False)
    return backup


def prepare_target():
    source, root, esp, luks = target_state()
    backup = backup_efi(source)
    # Reclaim only the old Fedora entries explicitly approved in test-target.json.
    # This runs after the user confirms Anaconda's installation, not at startup.
    plan = source['plan']
    token = plan['old_boot_token']
    if not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid old Fedora boot token')
    efi = TARGET / 'boot/efi'
    entries = list((efi / 'loader/entries').glob(token + '-*.conf'))
    if not entries:
        raise ValueError('Expected old Fedora boot entries are missing')
    for entry in entries:
        lines = entry.read_text().splitlines()
        options = [line.split(None, 1)[1] for line in lines if line.startswith('options ')]
        if len(options) != 1 or 'root=UUID=' + plan['old_root_uuid'] not in options[0].split():
            raise ValueError('Old boot entry does not belong to the replaced Fedora root')
    old_directory = efi / token
    if old_directory.is_dir():
        shutil.rmtree(old_directory)
    for entry in entries:
        entry.unlink()
    if shutil.disk_usage(efi).free < 128 * 1024 ** 2:
        raise ValueError('EFI needs 128 MiB free for the new boot files')
    configure_boot_inputs(source, root, luks)
    write(RUNTIME / 'prepared.json', json.dumps({'backup': str(backup), 'root_uuid': root['uuid']}), 0o600)
    print('Target boot inputs prepared; verified EFI backup: ' + str(backup))


@contextmanager
def chroot_mounts():
    owned = []
    try:
        for relative in ('dev', 'proc', 'sys', 'run'):
            target = TARGET / relative
            target.mkdir(parents=True, exist_ok=True)
            probe = subprocess.run(['mountpoint', '-q', str(target)])
            if probe.returncode == 0:
                continue
            if relative == 'run':
                run('mount', '-t', 'tmpfs', 'tmpfs', str(target), capture=False)
            else:
                run('mount', '--rbind', '/' + relative, str(target), capture=False)
                run('mount', '--make-rslave', str(target), capture=False)
            owned.append(target)
        yield
    finally:
        for target in reversed(owned):
            run('umount', '-R', str(target), capture=False)


def discard_unused_initrd(esp, token, kernel):
    """Remove the RPM-generated initrd only after BLS no longer references it."""
    if not re.fullmatch(r'[0-9a-f]{32}', token) or not re.fullmatch(r'[0-9a-zA-Z._+-]+', kernel):
        raise ValueError('Invalid boot artifact namespace')
    esp = Path(esp)
    obsolete = esp / token / kernel / 'initrd'
    if not obsolete.is_file() or obsolete.is_symlink():
        return 0
    for entry in (esp / 'loader/entries').glob('*.conf'):
        for line in entry.read_text().splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[0] == 'initrd':
                if (esp / fields[1].lstrip('/')).resolve() == obsolete.resolve():
                    return 0
    size = obsolete.stat().st_size
    obsolete.unlink()
    return size


REQUIRED_INITRD_FILES = (
    'etc/modprobe.d/dist-alsa.conf',
    'usr/lib/firmware/qcom/a660_sqe.fw',
    'usr/lib/firmware/qcom/a660_gmu.bin',
    'usr/lib/firmware/qcom/sc8280xp/HUAWEI/gaokun3/qcdxkmsuc8280.mbn',
)


def validate_initrd_payload(target, image):
    """lsinitrd can succeed with empty output for a missing member; compare bytes."""
    target = Path(target)
    for name in REQUIRED_INITRD_FILES:
        expected = (target / name).read_bytes()
        actual = subprocess.run(['chroot', str(target), 'lsinitrd', '-f', name, image],
                                check=True, stdout=subprocess.PIPE).stdout
        if not actual or actual != expected:
            raise ValueError('Initramfs member missing or differs from target: ' + name)


def finalize():
    source, root, esp, luks = target_state()
    policy = validate_install_policy(source.get('policy', DEFAULT_POLICY))
    prepared = json.loads((RUNTIME / 'prepared.json').read_text())
    if prepared['root_uuid'] != root['uuid']:
        raise ValueError('Target changed since pre-install')
    fstab = [shlex.split(line) for line in (TARGET / 'etc/fstab').read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if not any(len(e) >= 4 and e[0] == 'UUID=' + root['uuid'] and e[1] == '/' for e in fstab):
        raise ValueError('Anaconda fstab root does not match the installed filesystem')
    if not any(len(e) >= 4 and e[0] == 'UUID=' + esp['uuid'] and e[1] == '/boot/efi' for e in fstab):
        raise ValueError('Anaconda fstab EFI entry does not match the preserved filesystem')
    crypttab = (TARGET / 'etc/crypttab').read_text()
    for identifier in luks:
        if not any(len(e := line.split()) >= 2 and e[1] == 'UUID=' + identifier
                   for line in crypttab.splitlines() if not line.lstrip().startswith('#')):
            raise ValueError('Root LUKS UUID is missing from crypttab')
    token, cmdline = configure_boot_inputs(source, root, luks)
    kernels = sorted(p.name for p in (TARGET / 'usr/lib/modules').iterdir()
                     if re.fullmatch(r'[0-9a-zA-Z._+-]*-gaokun3\+', p.name))
    if not kernels:
        raise ValueError('No standard Gaokun3 kernel installed')
    def chroot(*args, capture=False):
        return run('chroot', str(TARGET), *args, capture=capture)
    with chroot_mounts():
        chroot('bootctl', '--no-variables', '--esp-path=/boot/efi', 'install')
        for kernel in kernels:
            initrd = f'/boot/initramfs-{kernel}.img'
            chroot('depmod', '-a', kernel)
            chroot('dracut', '--force', '--no-hostonly', '--no-hostonly-cmdline', '--kver', kernel, initrd)
            if 'crypt' not in chroot('lsinitrd', '-m', initrd, capture=True).split():
                raise ValueError('Generated initramfs is missing crypt')
            validate_initrd_payload(TARGET, initrd)
            chroot('kernel-install', '--esp-path=/boot/efi',
                   '--entry-token=machine-id', '--make-entry-directory=yes', 'add', kernel,
                   f'/boot/vmlinuz-{kernel}', initrd)
            entry = TARGET / f'boot/efi/loader/entries/{token}-{kernel}.conf'
            fields = [line.split(None, 1) for line in entry.read_text().splitlines()
                      if line.strip() and not line.startswith('#')]
            for kind in ('linux', 'initrd', 'devicetree'):
                paths = [value for key, value in fields if key == kind]
                if not paths or any(not (TARGET / 'boot/efi' / path.lstrip('/')).is_file() for path in paths):
                    raise ValueError('Incomplete BLS entry: ' + kind)
            options = next(value for key, value in fields if key == 'options')
            if not set(cmdline.split()).issubset(options.split()):
                raise ValueError('BLS entry has incorrect target kernel arguments')
            installed_initrd = next(value for key, value in fields if key == 'initrd')
            if digest(TARGET / 'boot/efi' / installed_initrd.lstrip('/')) != digest(TARGET / initrd.lstrip('/')):
                raise ValueError('EFI initramfs copy differs from the generated image')
            discard_unused_initrd(TARGET / 'boot/efi', token, kernel)
        removed_profiles = apply_network_policy(TARGET, policy)
        chroot('systemctl', 'enable', 'gdm', 'NetworkManager')
        chroot('systemctl', 'enable' if policy['enable_ssh'] else 'disable', 'sshd')
        if not policy['enable_ssh'] and (TARGET / 'usr/lib/systemd/system/sshd.socket').exists():
            chroot('systemctl', 'disable', 'sshd.socket')
        chroot('systemctl', 'set-default', 'graphical.target')
        chroot('ssh-keygen', '-A')
    write(TARGET / 'boot/efi/loader/loader.conf',
          f'default {token}-*\ntimeout 5\nconsole-mode keep\neditor no\n')
    result = {'root_uuid': root['uuid'], 'root_subvolume': root['fsroot'], 'luks_uuids': luks,
              'efi_uuid': esp['uuid'], 'kernels': kernels, 'machine_id': token,
              'efi_backup': prepared['backup'], 'policy': policy,
              'removed_network_profiles': removed_profiles}
    write(TARGET / 'root/gaokun-installer/result.json', json.dumps(result, indent=2) + '\n', 0o600)
    write(SOURCE / 'backups' / token / 'result.json', json.dumps(result, indent=2) + '\n', 0o600)
    run('sync', '-f', str(TARGET), capture=False)
    run('sync', '-f', str(TARGET / 'boot/efi'), capture=False)
    print('Gaokun systemd-boot, DTB and LUKS initramfs configured and checked.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('set-policy', 'preflight', 'prepare-target', 'finalize'))
    parser.add_argument('--copy-network', action='store_true')
    parser.add_argument('--enable-ssh', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run from the installer as root')
    if args.action == 'set-policy':
        policy = validate_install_policy({'copy_network': args.copy_network, 'enable_ssh': args.enable_ssh})
        write(RUNTIME / 'policy.json', json.dumps(policy) + '\n', 0o600)
        return
    if args.copy_network or args.enable_ssh:
        parser.error('Policy options are only accepted with set-policy')
    {'preflight': preflight, 'prepare-target': prepare_target, 'finalize': finalize}[args.action]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Gaokun installation failed: {error}', file=sys.stderr)
        sys.exit(1)
