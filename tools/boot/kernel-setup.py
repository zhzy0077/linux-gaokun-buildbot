#!/usr/bin/python3
"""RPM-owned Gaokun kernel installation, independent of installer disk selection."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path('/')
DATA = ROOT / 'usr/share/gaokun3'
ESP = ROOT / 'boot/efi'


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as stream:
        stream.write(value)
        temporary = Path(stream.name)
    temporary.chmod(0o644)
    temporary.replace(path)


def fstab_entries(text):
    return [shlex.split(line, comments=True) for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def root_entry(text):
    roots = [row for row in fstab_entries(text) if len(row) >= 4 and row[1] == '/']
    if len(roots) != 1:
        raise ValueError('Expected one target root in /etc/fstab')
    return roots[0]


def make_cmdline(row, hardware, luks_ids):
    source, _, filesystem, options = row[:4]
    if not source.startswith(('UUID=', 'PARTUUID=')):
        raise ValueError('The target root must be identified by UUID or PARTUUID')
    uuid.UUID(source.split('=', 1)[1])
    result = ['root=' + source, 'rw']
    if filesystem == 'btrfs':
        names = {v for v in options.split(',') if v.startswith('subvol=')}
        ids = {v for v in options.split(',') if v.startswith('subvolid=')}
        if len(names) > 1 or len(ids) > 1:
            raise ValueError('Ambiguous Btrfs root subvolume')
        subvol = sorted(names or ids)
        if subvol:
            if not re.fullmatch(r'subvol(?:id)?=[a-zA-Z0-9_@./+-]+', subvol[0]):
                raise ValueError('Unsupported Btrfs subvolume argument')
            result.append('rootflags=' + subvol[0])
    for identifier in sorted(set(luks_ids)):
        uuid.UUID(identifier)
        result.append('rd.luks.uuid=luks-' + identifier)
    arguments = shlex.split(hardware)
    if any(x.split('=', 1)[0] in ('root', 'rootflags', 'rd.luks.uuid', 'initrd', 'BOOT_IMAGE')
           for x in arguments):
        raise ValueError('Disk-specific argument in platform defaults')
    return ' '.join(result + arguments) + '\n'


def luks_ancestors(nodes):
    found = set()
    for node in nodes:
        if node.get('fstype') == 'crypto_LUKS' and node.get('uuid'):
            found.add(node['uuid'])
        found.update(luks_ancestors(node.get('children', [])))
    return sorted(found)


def target_cmdline():
    path = ROOT / 'etc/kernel/cmdline'
    if path.exists() and path.read_text().strip():
        # Anaconda's final configuration and later administrator changes win.
        return path.read_text().strip() + '\n'
    row = root_entry((ROOT / 'etc/fstab').read_text())
    device = run('findfs', row[0])
    nodes = json.loads(run('lsblk', '--json', '--inverse', '--paths', '--output',
                          'PATH,FSTYPE,UUID', device))['blockdevices']
    value = make_cmdline(row, (DATA / 'platform-cmdline').read_text(), luks_ancestors(nodes))
    atomic_write(path, value)
    return value


def mounted_esp():
    p = subprocess.run(['findmnt', '--json', '--mountpoint', str(ESP), '--output',
                        'TARGET,FSTYPE,OPTIONS'], text=True, capture_output=True)
    if p.returncode:
        return False
    rows = json.loads(p.stdout)['filesystems']
    if len(rows) != 1 or rows[0]['fstype'] != 'vfat' or 'rw' not in rows[0]['options'].split(','):
        raise ValueError('Expected a writable FAT ESP mounted at /boot/efi')
    return True


def entry_identity():
    path = ROOT / 'etc/machine-id'
    identity = path.read_text().strip() if path.exists() else ''
    if not re.fullmatch('[0-9a-f]{32}', identity) or identity == '0' * 32:
        identity = uuid.uuid4().hex
        atomic_write(path, identity + '\n')
    atomic_write(ROOT / 'etc/kernel/entry-token', identity + '\n')
    return identity


def efi_assets(el2):
    if not el2:
        return []
    files = [(DATA / 'el2' / name, ESP / 'EFI/systemd/drivers' / name)
             for name in ('slbounceaa64.efi', 'qebspilaa64.efi')]
    files.append((DATA / 'el2/tcblaunch.exe', ESP / 'tcblaunch.exe'))
    for name in ('qcadsp8280.mbn', 'qccdsp8280.mbn', 'qcslpi8280.mbn'):
        relative = Path('qcom/sc8280xp/HUAWEI/gaokun3') / name
        files.append((ROOT / 'usr/lib/firmware' / relative, ESP / 'firmware' / relative))
    return files


def required_free(files, margin=16 * 1024**2):
    needed = margin
    for source, target in files:
        if not source.is_file():
            raise ValueError('Missing boot input: ' + str(source))
        if target.is_symlink():
            raise ValueError('Refusing boot destination symlink: ' + str(target))
        needed += max(0, source.stat().st_size - (target.stat().st_size if target.exists() else 0))
    return needed


def install(kernel, devicetree):
    if not re.fullmatch(r'[0-9A-Za-z._+-]+-gaokun3(?:-el2)?\+?', kernel):
        raise ValueError('Unsupported Gaokun kernel release')
    el2 = '-gaokun3-el2' in kernel
    expected = 'qcom/sc8280xp-huawei-gaokun3' + ('-el2' if el2 else '') + '.dtb'
    if devicetree != expected:
        raise ValueError('Kernel/DTB variant mismatch')
    if not (ROOT / 'etc/fstab').is_file() or not mounted_esp():
        print('Gaokun: target filesystems not mounted; image builder must finalize boot later.')
        return
    cmdline = target_cmdline()
    token = entry_identity()
    image = ROOT / 'boot' / ('vmlinuz-' + kernel)
    initrd = ROOT / 'boot' / ('initramfs-' + kernel + '.img')
    dtb = ROOT / 'usr/lib/modules' / kernel / 'dtb' / devicetree
    assets = efi_assets(el2)
    # Generate exactly once. Passing it to kernel-install skips its dracut pass.
    subprocess.run(['dracut', '--force', '--no-hostonly', '--no-hostonly-cmdline',
                    '--kver', kernel, str(initrd)], check=True)
    destination = ESP / token / kernel
    files = [(image, destination / 'linux'), (initrd, destination / initrd.name),
             (dtb, destination / dtb.name), *assets]
    if shutil.disk_usage(ESP).free < required_free(files):
        raise ValueError('Insufficient ESP space; archive obsolete kernels before updating')
    for source, target in assets:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest():
            continue
        temporary = target.with_name(target.name + '.gaokun-new')
        if temporary.exists() or temporary.is_symlink():
            raise ValueError('Interrupted EFI write needs review: ' + str(temporary))
        shutil.copyfile(source, temporary)
        temporary.replace(target)
    with tempfile.TemporaryDirectory(prefix='gaokun-kernel-') as directory:
        conf = Path(directory)
        (conf / 'install.conf').write_text('layout=bls\n')
        (conf / 'cmdline').write_text(cmdline)
        (conf / 'devicetree').write_text(devicetree + '\n')
        subprocess.run(['kernel-install', '--esp-path=/boot/efi', '--entry-token=machine-id',
                        '--make-entry-directory=yes', 'add', kernel, str(image), str(initrd)],
                       env=dict(os.environ, KERNEL_INSTALL_CONF_ROOT=str(conf)), check=True)
    subprocess.run(['sync', '-f', str(ESP)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel')
    parser.add_argument('devicetree')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root')
    lock = Path('/run/lock/gaokun-kernel-setup.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        install(args.kernel, args.devicetree)
