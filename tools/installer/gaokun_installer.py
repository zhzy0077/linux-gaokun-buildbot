#!/usr/bin/python3
"""Check installation media and validate Anaconda's completed Gaokun installation.

Storage and first bootloader installation belong to Anaconda. Kernel RPMs create
versioned boot files. This helper never partitions disks or installs a bootloader.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import subprocess

SOURCE = Path('/opt/installer')
TARGET = Path('/mnt/sysroot')
RUNTIME = Path('/run/gaokun-installer')
DEFAULT_POLICY = {'copy_network': False, 'enable_ssh': False}


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)


def validate_install_policy(policy):
    if not isinstance(policy, dict) or set(policy) != set(DEFAULT_POLICY):
        raise ValueError('Invalid installation policy keys')
    if any(type(v) is not bool for v in policy.values()):
        raise ValueError('Installation policy values must be booleans')
    return policy


def mount_info(path):
    rows = json.loads(run('findmnt', '--json', '--mountpoint', str(path), '--output',
                          'SOURCE,FSTYPE,UUID,FSROOT'))['filesystems']
    if len(rows) != 1:
        raise ValueError('Expected one mounted filesystem: ' + str(path))
    return rows[0]


def nodes(device):
    data = json.loads(run('lsblk', '--json', '--inverse', '--paths', '--output',
                          'PATH,TYPE,FSTYPE,UUID', device))['blockdevices']
    def walk(items):
        for item in items:
            yield item
            yield from walk(item.get('children', []))
    return list(walk(data))


def device_path(mount):
    return mount['source'].split('[', 1)[0]


def media_preflight():
    marker = Path('/etc/gaokun-installer-media.json')
    media = json.loads(marker.read_text())
    manifest = SOURCE / 'rpm-manifest.json'
    if digest(manifest) != media['manifest_sha256']:
        raise ValueError('Installer media manifest mismatch')
    data = json.loads(manifest.read_text())
    if not re.fullmatch(r'[0-9A-Za-z._+-]+-gaokun3-el2\+?', data['kernel_release']):
        raise ValueError('Expected an EL2 kernel release in the media manifest')
    for name, expected in data['sha256'].items():
        if Path(name).name != name or not name.endswith('.rpm'):
            raise ValueError('Invalid RPM filename')
        if digest(SOURCE / 'repo' / name) != expected:
            raise ValueError('RPM digest mismatch: ' + name)
    if run('uname', '-m') != 'aarch64':
        raise ValueError('This installation media requires aarch64')
    compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    if b'huawei,gaokun3' not in compatible:
        raise ValueError('This media is for Huawei Gaokun3')
    live = mount_info('/')
    source_disks = sorted({n['path'] for n in nodes(device_path(live)) if n['type'] == 'disk'})
    if not source_disks:
        raise ValueError('Cannot identify the running installation disk')
    write_json(RUNTIME / 'source.json', {'disks': source_disks, 'uuid': live['uuid'],
                                        'kernel': data['kernel_release']})


def boot_fields(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        pair = line.split(None, 1)
        if len(pair) == 2:
            result.setdefault(pair[0], []).append(pair[1])
    return result


def check_root_options(options, root, luks):
    tokens = shlex.split(options)
    roots = {x for x in tokens if x.startswith('root=')}
    if roots != {'root=UUID=' + root['uuid']}:
        raise ValueError('BLS root does not match the installed filesystem')
    if root['fstype'] == 'btrfs' and root['fsroot'] != '/':
        expected = 'subvol=' + root['fsroot'].lstrip('/')
        flags = {v for token in tokens if token.startswith('rootflags=')
                 for v in token.split('=', 1)[1].split(',')}
        normalized = {'subvol=' + v.split('=', 1)[1].lstrip('/') if v.startswith('subvol=') else v
                      for v in flags}
        if expected not in normalized:
            raise ValueError('BLS has the wrong Btrfs subvolume')
    identifiers = {x.split('=', 1)[1].removeprefix('luks-')
                   for x in tokens if x.startswith('rd.luks.uuid=')}
    if not set(luks).issubset(identifiers):
        raise ValueError('BLS lacks the root LUKS UUID')


def contained_file(root, relative):
    candidate = root / relative.lstrip('/')
    if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError('Invalid boot file reference: ' + relative)
    return candidate


def apply_policy(policy):
    policy = validate_install_policy(policy)
    profiles = TARGET / 'etc/NetworkManager/system-connections'
    if not profiles.resolve().is_relative_to(TARGET.resolve()):
        raise ValueError('Network profiles escape the target')
    if not policy['copy_network'] and profiles.exists():
        files = list(profiles.iterdir())
        if any(p.is_dir() and not p.is_symlink() for p in files):
            raise ValueError('Unexpected directory in NetworkManager profiles')
        for p in files:
            p.unlink()
    action = 'enable' if policy['enable_ssh'] else 'disable'
    subprocess.run(['systemctl', '--root', str(TARGET), action, 'sshd.service'], check=True)
    if not policy['enable_ssh'] and (TARGET / 'usr/lib/systemd/system/sshd.socket').exists():
        subprocess.run(['systemctl', '--root', str(TARGET), 'disable', 'sshd.socket'], check=True)


def validate_target():
    source = json.loads((RUNTIME / 'source.json').read_text())
    root = mount_info(TARGET)
    esp = mount_info(TARGET / 'boot/efi')
    root_nodes = nodes(device_path(root))
    used = {n['path'] for n in root_nodes + nodes(device_path(esp)) if n['type'] == 'disk'}
    if used & set(source['disks']) or root['uuid'] == source['uuid']:
        raise ValueError('Installation target overlaps the running installation media')
    if esp['fstype'] != 'vfat':
        raise ValueError('Expected an EFI system partition at /boot/efi')
    luks = sorted({n['uuid'] for n in root_nodes if n['fstype'] == 'crypto_LUKS' and n['uuid']})
    kernel = source['kernel']
    token = (TARGET / 'etc/kernel/entry-token').read_text().strip()
    if not token or Path(token).name != token or token in ('.', '..'):
        raise ValueError('Invalid kernel entry token')
    efi = TARGET / 'boot/efi'
    entry = efi / 'loader/entries' / (token + '-' + kernel + '.conf')
    fields = boot_fields(entry.read_text())
    for key in ('linux', 'devicetree', 'options'):
        if len(fields.get(key, [])) != 1:
            raise ValueError('Incomplete or ambiguous BLS entry: ' + key)
    check_root_options(fields['options'][0], root, luks)
    dtb_name = 'sc8280xp-huawei-gaokun3-el2.dtb'
    expected = {'linux': TARGET / ('boot/vmlinuz-' + kernel),
                'devicetree': TARGET / ('usr/lib/modules/' + kernel + '/dtb/qcom/' + dtb_name)}
    for key, original in expected.items():
        if digest(contained_file(efi, fields[key][0])) != digest(original):
            raise ValueError('EFI payload mismatch: ' + key)
    if len(fields.get('initrd', [])) != 1:
        raise ValueError('Expected one generated initramfs')
    initrd = contained_file(efi, fields['initrd'][0])
    target_image = '/' + str(initrd.relative_to(TARGET))
    modules = run('chroot', str(TARGET), 'lsinitrd', '-m', target_image).split()
    if luks and 'crypt' not in modules:
        raise ValueError('Missing crypt support in initramfs')
    for member in ('etc/modprobe.d/dist-alsa.conf', 'usr/lib/firmware/qcom/a660_sqe.fw',
                   'usr/lib/firmware/qcom/a660_gmu.bin'):
        data = subprocess.check_output(['chroot', str(TARGET), 'lsinitrd', '-f', member, target_image])
        if not data or data != (TARGET / member).read_bytes():
            raise ValueError('Missing or incorrect initramfs member: ' + member)
    assets = [('usr/share/gaokun3/el2/' + n, 'EFI/systemd/drivers/' + n)
              for n in ('slbounceaa64.efi', 'qebspilaa64.efi')]
    assets += [('usr/share/gaokun3/el2/tcblaunch.exe', 'tcblaunch.exe'),
               ('usr/lib/systemd/boot/efi/systemd-bootaa64.efi', 'EFI/systemd/systemd-bootaa64.efi')]
    for n in ('qcadsp8280.mbn', 'qccdsp8280.mbn', 'qcslpi8280.mbn'):
        relative = 'qcom/sc8280xp/HUAWEI/gaokun3/' + n
        assets.append(('usr/lib/firmware/' + relative, 'firmware/' + relative))
    for original, deployed in assets:
        if digest(TARGET / original) != digest(contained_file(efi, deployed)):
            raise ValueError('Missing or incorrect EFI asset: ' + deployed)
    policy_file = RUNTIME / 'policy.json'
    apply_policy(json.loads(policy_file.read_text()) if policy_file.exists() else DEFAULT_POLICY)
    write_json(TARGET / 'var/lib/gaokun3/installation.json',
               {'kernel': kernel, 'root_uuid': root['uuid'], 'efi_uuid': esp['uuid'], 'luks_uuids': luks})
    print('Gaokun RPM boot files and Anaconda installation validated.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'set-policy', 'validate'))
    parser.add_argument('--copy-network', action='store_true')
    parser.add_argument('--enable-ssh', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run from the installer as root')
    if args.action == 'set-policy':
        write_json(RUNTIME / 'policy.json', {'copy_network': args.copy_network, 'enable_ssh': args.enable_ssh})
    elif args.action == 'preflight':
        media_preflight()
    else:
        validate_target()


if __name__ == '__main__':
    main()
