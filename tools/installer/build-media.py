#!/usr/bin/python3
"""Compose a network-install root tree with a local Gaokun RPM source (native aarch64)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

# The live session only runs GNOME for Wi-Fi setup, Anaconda Web UI and the Gaokun hardware
# stack. Anaconda downloads the Workstation target from Fedora's mirrors during installation.
LIVE_PACKAGES = [
    'kernel-gaokun3-el2', 'kernel-modules-gaokun3-el2', 'linux-firmware-gaokun3', 'gaokun3-platform',
    'fedora-release-workstation', 'selinux-policy-targeted', 'sudo', 'langpacks-en', 'langpacks-zh_CN',
    # Required for the complete PAM -> user manager -> GNOME login path.
    'gdm', 'gnome-shell', 'gnome-session-wayland-session', 'gnome-control-center',
    'systemd-pam', 'dbus-daemon', 'cracklib-dicts', 'NetworkManager-wifi', 'wpa_supplicant',
    # Storage and boot tools used by Anaconda and the image builder.
    'systemd-boot-unsigned', 'sdubby', 'efibootmgr', 'dracut', 'cryptsetup', 'systemd-udev',
    'btrfs-progs', 'e2fsprogs', 'dosfstools', 'lvm2', 'mdadm',
    'anaconda-live', 'anaconda-webui', 'anaconda-install-env-deps', 'cockpit-storaged',
    'udisks2-btrfs', 'firefox', 'polkit',
]


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
    if rootfs.exists() or (work / 'gaokun-repo').exists():
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
    common = ['dnf', '-y', '--releasever=' + version, '--use-host-config', '--forcearch=aarch64',
              '--setopt=install_weak_deps=True', '--exclude=' + ','.join(excludes),
              '--repofrompath=gaokun-build,file://' + str(source),
              '--setopt=gaokun-build.gpgcheck=0']
    # Anaconda resolves the target against the mirrors at install time; prove the selection now.
    cache = work / 'target-cache'
    run(*common, '--installroot=' + str(work / 'target-solver'), '--setopt=cachedir=' + str(cache),
        'install', '--downloadonly', *packages)
    shutil.rmtree(cache)
    run(*common, '--installroot=' + str(rootfs), 'install', *LIVE_PACKAGES)
    # Assert that the selected Gaokun kernel actually belongs to this compose.
    if not (rootfs / 'usr/lib/modules' / kernel).is_dir():
        raise ValueError('Unexpected installer kernel')
    run('chroot', rootfs, 'rpm', '-q', '--whatprovides', 'gaokun3-boot-integration')
    destination = rootfs / 'opt/installer'
    destination.mkdir(parents=True)
    shutil.copytree(source, destination / 'repo')
    inventory = {'kernel_release': kernel,
                 'sha256': {p.name: sha(p) for p in sorted(source.glob('*.rpm'))}}
    (destination / 'rpm-manifest.json').write_text(json.dumps(inventory, indent=2) + '\n')
    marker = rootfs / 'etc/gaokun-installer-media.json'
    marker.write_text(json.dumps({'format': 1, 'fedora_release': version,
                                 'manifest_sha256': sha(destination / 'rpm-manifest.json')}, indent=2) + '\n')
    assets = {
        'gaokun_installer.py': '/opt/installer/gaokun_installer.py',
        'gaokun-install': '/usr/local/bin/gaokun-install',
        'gaokun-install-backend': '/usr/local/libexec/gaokun-install-backend',
        'gaokun-install-browser': '/usr/local/libexec/gaokun-install-browser',
        'gaokun-install.desktop': '/usr/share/applications/gaokun-install.desktop',
        'org.gaokun.installer.policy': '/usr/share/polkit-1/actions/org.gaokun.installer.policy',
        '90-gaokun.conf': '/etc/anaconda/conf.d/90-gaokun.conf',
    }
    for name, relative in assets.items():
        target = rootfs / relative.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / 'tools/installer' / name, target)
        target.chmod(0o755 if name in ('gaokun-install', 'gaokun-install-backend',
                                     'gaokun-install-browser') else 0o644)
    defaults = rootfs / 'usr/share/anaconda/interactive-defaults.ks'
    defaults.parent.mkdir(parents=True, exist_ok=True)
    defaults.write_text(render_defaults(text, (rootfs / 'usr/share/gaokun3/platform-cmdline').read_text()))
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
