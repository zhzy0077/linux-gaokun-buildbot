#!/usr/bin/python3
"""Compose an offline RPM source and an installer root tree (native aarch64)."""
import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def run(*args):
    print('+', *map(str, args), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package_selection(kickstart):
    packages, excludes = [], []
    inside = False
    for line in kickstart.splitlines():
        line = line.strip()
        if line.startswith('%packages'):
            inside = True
        elif line == '%end':
            inside = False
        elif inside and line and not line.startswith('#'):
            if line.startswith('-'):
                excludes.append(line[1:])
            else:
                packages.append(line)
    return packages, excludes


def fetch_comps(version, directory):
    base = f'https://dl.fedoraproject.org/pub/fedora/linux/releases/{version}/Everything/aarch64/os/'
    with urllib.request.urlopen(base + 'repodata/repomd.xml', timeout=60) as response:
        repomd = response.read()
    ns = {'r': 'http://linux.duke.edu/metadata/repo'}
    root = ET.fromstring(repomd)
    choices = {node.attrib['type']: node for node in root.findall('r:data', ns)}
    node = next((choices[k] for k in ('group', 'group_gz') if k in choices), None)
    if node is None:
        raise ValueError('Fedora repository has no supported comps metadata')
    location = node.find('r:location', ns).attrib['href']
    url = urllib.parse.urljoin(base, location)
    if not url.startswith(base):
        raise ValueError('Unexpected comps URL')
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    checksum = node.find('r:checksum', ns)
    if hashlib.new(checksum.attrib['type'], data).hexdigest() != checksum.text:
        raise ValueError('Comps metadata checksum mismatch')
    if data.startswith(b'\x1f\x8b'):
        data = gzip.decompress(data)
    elif data.startswith(b'\xfd7zXZ\x00'):
        data = lzma.decompress(data)
    elif data.startswith(b'\x28\xb5\x2f\xfd'):
        data = subprocess.check_output(['zstd', '-dc'], input=data)
    ET.fromstring(data)
    result = directory / 'comps.xml'
    result.write_bytes(data)
    return result


def render_defaults(template, hardware):
    if template.count('@PLATFORM_CMDLINE@') != 1:
        raise ValueError('Expected one hardware argument placeholder')
    hardware = hardware.strip()
    if any(c in hardware for c in ('"', '\n', '\r')):
        raise ValueError('Invalid platform arguments')
    return template.replace('@PLATFORM_CMDLINE@', hardware)


def prepare(repo, package_dir, work, rootfs, version):
    if os.geteuid() != 0 or os.uname().machine != 'aarch64':
        raise ValueError('Build in a dedicated root aarch64 Fedora environment')
    if rootfs.exists() or (work / 'offline-repo').exists():
        raise ValueError('Use a fresh build workspace')
    manifest = json.loads((package_dir / 'package-manifest.json').read_text())
    kernel = manifest['kernels']['el2']['release']
    wanted = [manifest['kernels']['el2']['packages'][key] for key in ('kernel', 'kernel_modules')]
    wanted += [manifest['packages'][key] for key in ('firmware', 'platform')]
    source = work / 'gaokun-repo'
    source.mkdir(parents=True)
    for name in wanted:
        if Path(name).name != name or not name.endswith('.rpm'):
            raise ValueError('Invalid RPM name')
        path = package_dir / name
        expected = json.loads((package_dir / 'download-sha256.json').read_text())[name]
        if sha(path) != expected:
            raise ValueError('Downloaded RPM digest mismatch: ' + name)
        shutil.copy2(path, source / name)
    run('createrepo_c', source)
    text = (repo / 'tools/installer/interactive-defaults.ks').read_text()
    packages, excludes = package_selection(text)
    # Include install-time requirements and filesystem tools requested by Anaconda.
    packages += ['efibootmgr', 'systemd-udev', 'sdubby', 'e2fsprogs', 'dosfstools', 'lvm2', 'mdadm']
    offline = work / 'offline-repo'
    offline.mkdir()
    common = ['dnf', '-y', '--releasever=' + version, '--use-host-config', '--forcearch=aarch64',
              '--setopt=install_weak_deps=True', '--exclude=' + ','.join(excludes)]
    source_options = ['--repofrompath=gaokun-build,file://' + str(source),
                      '--setopt=gaokun-build.gpgcheck=0']
    # A new installroot forces dependency closure rather than using builder packages.
    cache = work / 'download-cache'
    run(*common, '--installroot=' + str(work / 'solver'), *source_options,
        '--setopt=cachedir=' + str(cache), '--setopt=keepcache=True',
        'install', '--downloadonly', *packages)
    # DNF5 download-only writes repository caches; --destdir is a download-command option.
    for package in sorted(cache.rglob('*.rpm')):
        target = offline / package.name
        if target.exists() and sha(target) != sha(package):
            raise ValueError('Conflicting package filenames: ' + package.name)
        shutil.copy2(package, target)
    for name in wanted:
        if not (offline / name).exists():
            shutil.copy2(source / name, offline / name)
    comps = fetch_comps(version, work)
    run('createrepo_c', '-g', comps, offline)
    # Verify the complete target solve with only the media repository enabled.
    run(*common, '--installroot=' + str(work / 'offline-solver'), '--disablerepo=*',
        '--repofrompath=gaokun-offline,file://' + str(offline), '--enablerepo=gaokun-offline',
        '--setopt=gaokun-offline.gpgcheck=0',
        '--setopt=cachedir=' + str(work / 'offline-cache'),
        'install', '--downloadonly', *packages)
    # Installer-only packages never enter the target package list.
    live = ['anaconda-live', 'anaconda-webui', 'anaconda-install-env-deps', 'zenity',
            'cockpit-storaged', 'udisks2-btrfs', 'firefox', 'polkit']
    run(*common, '--installroot=' + str(rootfs), *source_options, 'install', *packages, *live)
    # Assert that the selected Gaokun kernel actually belongs to this compose.
    if not (rootfs / 'usr/lib/modules' / kernel).is_dir():
        raise ValueError('Unexpected installer kernel')
    run('chroot', rootfs, 'rpm', '-q', '--whatprovides', 'gaokun3-boot-integration >= 1')
    destination = rootfs / 'opt/installer'
    destination.mkdir(parents=True)
    shutil.copytree(offline, destination / 'repo')
    inventory = {'kernel_release': kernel,
                 'sha256': {p.name: sha(p) for p in sorted(offline.glob('*.rpm'))}}
    (destination / 'rpm-manifest.json').write_text(json.dumps(inventory, indent=2) + '\n')
    marker = rootfs / 'etc/gaokun-installer-media.json'
    marker.write_text(json.dumps({'format': 1, 'fedora_release': version,
                                 'manifest_sha256': sha(destination / 'rpm-manifest.json')}, indent=2) + '\n')
    assets = {
        'gaokun_installer.py': '/opt/installer/gaokun_installer.py',
        'gaokun-install': '/usr/local/bin/gaokun-install',
        'gaokun-install-backend': '/usr/local/libexec/gaokun-install-backend',
        'gaokun-install.desktop': '/usr/share/applications/gaokun-install.desktop',
        'org.gaokun.installer.policy': '/usr/share/polkit-1/actions/org.gaokun.installer.policy',
        '90-gaokun.conf': '/etc/anaconda/conf.d/90-gaokun.conf',
    }
    for name, relative in assets.items():
        target = rootfs / relative.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / 'tools/installer' / name, target)
        target.chmod(0o755 if name in ('gaokun-install', 'gaokun-install-backend') else 0o644)
    defaults = rootfs / 'usr/share/anaconda/interactive-defaults.ks'
    defaults.parent.mkdir(parents=True, exist_ok=True)
    defaults.write_text(render_defaults(text, (repo / 'tools/boot/platform-cmdline').read_text()))
    run('ksvalidator', '-v', 'F44', defaults)
    # Hide the stock live-copy launcher; this medium uses Anaconda's DNF payload.
    overrides = rootfs / 'usr/local/share/applications'
    overrides.mkdir(parents=True, exist_ok=True)
    for name in ('liveinst.desktop', 'org.fedoraproject.AnacondaInstaller.desktop'):
        (overrides / name).write_text('[Desktop Entry]\nType=Application\nHidden=true\n')
    (work / 'kernel-release.txt').write_text(kernel + '\n')
    shutil.copy2(destination / 'rpm-manifest.json', work / 'media-rpm-manifest.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--package-dir', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--fedora', default='44', choices=['44'])
    a = parser.parse_args()
    prepare(a.repo.resolve(), a.package_dir.resolve(), a.work.resolve(), a.rootfs.resolve(), a.fedora)
