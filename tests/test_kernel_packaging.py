"""Bound package staging size while retaining external-module build inputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / 'scripts/ci/lib/kernel_package.sh'


def put(path, text='fixture\n', executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)
    return path


def kernel_trees(directory, distro='ubuntu', el2=False):
    suffix = '-el2' if el2 else ''
    krel = f'7.2.9-gaokun3{suffix}+'
    src = directory / f'source{suffix}'
    out = directory / f'kernel-out{suffix}'
    put(src / 'Makefile', '# standalone source Makefile\n')
    put(src / 'include/linux/module.h')
    put(src / 'arch/arm64/include/asm/module.h')
    put(src / '.git/HEAD', 'private checkout metadata\n')
    put(src / 'drivers/test/source.o')
    for name in ('.config', 'Module.symvers', 'System.map', 'vmlinux',
                 'include/config/auto.conf', 'include/generated/autoconf.h',
                 'arch/arm64/include/generated/asm/types.h',
                 'rust/libkernel.rmeta', 'rust/libmacros.so'):
        put(out / name)
    put(out / 'Makefile', f'include {src}/Makefile\n')
    put(out / 'include/config/kernel.release', krel + '\n')
    put(out / 'gaokun-distro', distro + '\n')
    put(out / 'kernel-config-report.json', '{}\n')
    put(out / 'arch/arm64/boot/Image')
    put(out / 'arch/arm64/boot/vmlinuz.efi')
    put(out / f'arch/arm64/boot/dts/qcom/sc8280xp-huawei-gaokun3{suffix}.dtb')
    for name in ('scripts/mod/modpost', 'scripts/basic/fixdep',
                 'tools/bpf/resolve_btfids/resolve_btfids'):
        put(out / name, executable=True)
    for name in ('drivers/test/driver.o', 'drivers/test/driver.ko',
                 'drivers/test/built-in.a', 'drivers/test/.driver.o.cmd',
                 'drivers/test/driver.mod', 'drivers/test/driver.mod.c',
                 '.tmp_vmlinux1', 'vmlinux.unstripped'):
        put(out / name)
    (out / 'source').symlink_to(src, target_is_directory=True)
    (out / 'build').symlink_to(out, target_is_directory=True)
    return src, out, krel


# Exercise the real packaging scripts, templates, rsync, tar and filesystem
# lifecycle. Only compilation/config validation and the package encoders are
# mocked; these fixtures require neither a kernel build nor root/container tools.
FAKE_TOOLS = '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
work = Path(os.environ['WORKDIR'])
event = {'tool': tool, 'args': args}

def put(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('packaged fixture')

if tool == 'make':
    assert 'INSTALL_MOD_STRIP=1' in args, args
    assert 'modules_install' in args, args
    values = dict(arg.split('=', 1) for arg in args if '=' in arg)
    out = Path(values['O'])
    krel = (out / 'include/config/kernel.release').read_text().strip()
    event['krel'] = krel
    event['staging'] = sorted(p.name for p in (work / 'package-buildroots').iterdir())
    event['archives'] = sorted(p.name for p in (work / 'rpmbuild/SOURCES').glob('*'))
    modules = Path(values['INSTALL_MOD_PATH']) / 'lib/modules' / krel
    put(modules / 'kernel/test.ko')
    (modules / 'build').symlink_to(out, target_is_directory=True)
elif tool == 'dpkg-deb':
    if os.environ.get('FAKE_PACKAGER_FAIL'):
        sys.exit(37)
    stage = Path(args[-2])
    event['stage'] = stage.name
    event['control'] = (stage / 'DEBIAN/control').read_text()
    event['files'] = sorted(str(p.relative_to(stage)) for p in stage.rglob('*'))
    put(Path(args[-1]))
elif tool == 'rpmbuild':
    spec = Path(args[-1]).read_text()
    name = re.search(r'^Name:\\s+(\\S+)', spec, re.M)[1]
    source = re.search(r'^Source0:\\s+(\\S+)', spec, re.M)[1]
    top = Path(next(arg.removeprefix('_topdir ') for arg in args if arg.startswith('_topdir ')))
    archive = top / 'SOURCES' / source
    assert not (work / 'package-buildroots' / name).exists(), name
    if os.environ.get('FAKE_PACKAGER_FAIL'):
        sys.exit(37)
    with tarfile.open(archive) as tar:
        event['files'] = sorted(n.removeprefix('./') for n in tar.getnames())
    event['stage'] = name
    put(top / 'RPMS/aarch64' / (name + '-fixture.rpm'))
    put(top / 'BUILD' / name / 'extracted')
    if '--clean' in args:
        shutil.rmtree(top / 'BUILD' / name)
    if '--rmsource' in args:
        archive.unlink()
elif tool != 'depmod':
    raise AssertionError(tool)

with Path(os.environ['FAKE_PACKAGING_LOG']).open('a') as log:
    log.write(json.dumps(event) + '\\n')
'''


class KernelTestSuffixTests(unittest.TestCase):
    def set_suffix(self, source, suffix):
        return subprocess.run(['bash', '-euo', 'pipefail', '-c',
                               'source "$1"; set_kernel_test_suffix "$2" "$3"',
                               'test', str(HELPERS), str(source), suffix],
                              capture_output=True, text=True)

    def test_opt_in_and_validated_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            localversion = source / 'localversion.gaokun-test'
            self.assertEqual(self.set_suffix(source, '').returncode, 0)
            self.assertFalse(localversion.exists())
            for suffix in ('dp12', '-', '--dp12', '-DP12', '-../x', '-a/b',
                           '-a\nb', '-a b', '-' + 'a' * 25):
                self.assertNotEqual(self.set_suffix(source, suffix).returncode, 0, suffix)
                self.assertFalse(localversion.exists())
            self.assertEqual(self.set_suffix(source, '-dp12').returncode, 0)
            self.assertEqual(localversion.read_text(), '-dp12\n')

    def test_real_kbuild_release_has_separate_module_namespace(self):
        kernel = os.environ.get('GAOKUN_KERNEL_SRC')
        if not kernel or not (Path(kernel) / 'scripts/setlocalversion').is_file():
            self.skipTest('set GAOKUN_KERNEL_SRC for the real Kbuild release script')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            output = root / 'output'
            for localversion in ('-gaokun3', '-gaokun3-el2'):
                put(output / 'include/config/auto.conf',
                    'CONFIG_LOCALVERSION=' + localversion + '\n')
                env = dict(os.environ, KERNELVERSION='7.2.9', LOCALVERSION='')
                command = ['sh', str(Path(kernel) / 'scripts/setlocalversion'), str(source)]
                before = subprocess.check_output(command, cwd=output, env=env, text=True).strip()
                self.assertEqual(before, '7.2.9' + localversion)
                self.assertEqual(self.set_suffix(source, '-dp12').returncode, 0)
                after = subprocess.check_output(command, cwd=output, env=env, text=True).strip()
                self.assertEqual(after, '7.2.9-dp12' + localversion)
                self.assertNotEqual(before, after)
                (source / 'localversion.gaokun-test').unlink()


@unittest.skipUnless(shutil.which('rsync'), 'rsync required')
class KernelPackagingTests(unittest.TestCase):
    def stage(self, src, out, dest):
        return subprocess.run(
            ['bash', '-euo', 'pipefail', '-c',
             'source "$1"; rsync() { command rsync --itemize-changes "$@"; }; '
             'stage_kernel_devel "$2" "$3" "$4"',
             'test', str(HELPERS), str(src), str(out), str(dest)],
            check=True, text=True, capture_output=True)

    def test_intermediates_are_filtered_before_copying(self):
        with tempfile.TemporaryDirectory(prefix='gaokun packaging ') as directory:
            root = Path(directory)
            src, out, _ = kernel_trees(root)
            dest = root / 'devel'
            result = self.stage(src, out, dest)
            excluded = (
                '.git/HEAD', 'drivers/test/source.o', 'drivers/test/driver.o',
                'drivers/test/driver.ko', 'drivers/test/built-in.a',
                'drivers/test/.driver.o.cmd', 'drivers/test/driver.mod',
                'drivers/test/driver.mod.c', '.tmp_vmlinux1', 'vmlinux.unstripped',
                'arch/arm64/boot/Image',
                'arch/arm64/boot/vmlinuz.efi',
                'arch/arm64/boot/dts/qcom/sc8280xp-huawei-gaokun3.dtb')
            for name in excluded:
                with self.subTest(name=name):
                    self.assertFalse((dest / name).exists())
                    # A copy-then-delete implementation would still log these.
                    self.assertNotIn(name, result.stdout)
                    self.assertTrue((src / name).exists() or (out / name).exists())
            for name in ('.config', 'Module.symvers', 'vmlinux',
                         'include/config/auto.conf', 'include/generated/autoconf.h',
                         'arch/arm64/include/generated/asm/types.h',
                         'rust/libkernel.rmeta', 'rust/libmacros.so',
                         'scripts/mod/modpost', 'scripts/basic/fixdep',
                         'tools/bpf/resolve_btfids/resolve_btfids'):
                self.assertEqual((dest / name).read_bytes(), (out / name).read_bytes())
            self.assertEqual((dest / 'Makefile').read_bytes(), (src / 'Makefile').read_bytes())
            self.assertTrue(os.access(dest / 'scripts/mod/modpost', os.X_OK))
            self.assertTrue((dest / 'include/linux/module.h').is_file())
            self.assertFalse((dest / 'source').is_symlink())
            self.assertFalse((dest / 'build').is_symlink())

    def test_retry_removes_stale_excluded_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, out, _ = kernel_trees(root)
            dest = root / 'devel'
            self.stage(src, out, dest)
            put(dest / 'drivers/test/stale.o')
            put(dest / '.git/HEAD')
            self.stage(src, out, dest)
            self.assertFalse((dest / 'drivers/test/stale.o').exists())
            self.assertFalse((dest / '.git').exists())

    def package_fixture(self, directory, kind):
        root = Path(directory)
        repo = root / 'repo'
        work = root / 'work'
        put(repo / 'tools/boot/check-kernel-config.py', '# Config validation has its own tests.\n')
        for vendor in ('ath11k', 'qca', 'qcom'):
            put(repo / 'firmware' / vendor / 'test.bin')
        for name in ('wcnhpnv21.bin', 'LICENSE.QualcommAtheros_ath10k', 'NOTICE.txt'):
            shutil.copy2(ROOT / 'firmware/qca' / name, repo / 'firmware/qca' / name)
        (repo / 'packaging').symlink_to(ROOT / 'packaging', target_is_directory=True)
        put(repo / 'scripts/ci/71_build_platform_rpm.sh',
            'set -eu\n'
            'touch "$ARTIFACT_DIR/gaokun3-platform-fixture.rpm"\n'
            'echo gaokun3-platform-fixture.rpm > "$WORKDIR/platform-rpm-name.txt"\n')
        (repo / 'scripts/ci/lib').symlink_to(HELPERS.parent, target_is_directory=True)
        distro = 'fedora' if kind == 'rpms' else 'ubuntu'
        src, out, krel = kernel_trees(work, distro)
        src_el2, out_el2, krel_el2 = kernel_trees(work, distro, el2=True)
        put(work / 'kernel-release.txt', krel + '\n')
        put(work / 'kernel-release-el2.txt', krel_el2 + '\n')
        tool = put(root / 'bin/package-tool', FAKE_TOOLS, executable=True)
        for name in ('make', 'depmod', 'dpkg-deb', 'rpmbuild'):
            (tool.parent / name).symlink_to(tool)
        env = dict(os.environ, GAOKUN_DIR=str(repo), WORKDIR=str(work),
                   ARTIFACT_DIR=str(work / 'artifacts'), BUILD_EL2='true',
                   KERNEL_TAG='v7.2.9', PACKAGE_RELEASE_TAG='fixture-release',
                   KERN_SRC_BASE=str(src), KERN_OUT=str(out),
                   KERN_SRC_EL2=str(src_el2), KERN_OUT_EL2=str(out_el2),
                   FAKE_PACKAGING_LOG=str(root / 'events.jsonl'),
                   PATH=str(tool.parent) + os.pathsep + os.environ['PATH'])
        return work, env

    def run_package(self, kind, env):
        return subprocess.run(['bash', str(ROOT / f'scripts/ci/70_build_package_{kind}.sh')],
                              env=env, text=True, capture_output=True)

    def test_both_formats_release_staging_between_variants(self):
        for kind in ('debs', 'rpms'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                work, env = self.package_fixture(directory, kind)
                result = self.run_package(kind, env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Disk usage:', result.stdout)
                events = [json.loads(line) for line in Path(env['FAKE_PACKAGING_LOG']).read_text().splitlines()]
                installs = [event for event in events if event['tool'] == 'make']
                self.assertEqual(len(installs), 2)
                self.assertTrue(all('-el2' in name for name in installs[1]['staging']))
                self.assertEqual(installs[1]['archives'], [])
                devel = [event for event in events
                         if event.get('stage', '').startswith(('linux-headers-', 'kernel-devel-'))]
                self.assertEqual(len(devel), 2)
                for event in devel:
                    for name in ('/.config', '/Module.symvers', '/vmlinux'):
                        self.assertTrue(any(path.endswith(name) for path in event['files']), name)
                    self.assertFalse(any(path.endswith(('.o', '.ko', '.a', '.cmd', '.mod', '.mod.c'))
                                         for path in event['files']))
                firmware = next(event for event in events
                                if event.get('stage') == 'linux-firmware-gaokun3')
                prefix = 'lib' if kind == 'debs' else 'usr/lib'
                for name in ('wcnhpnv21.bin', 'LICENSE.QualcommAtheros_ath10k', 'NOTICE.txt'):
                    self.assertIn(f'{prefix}/firmware/qca/{name}', firmware['files'])
                self.assertEqual(list((work / 'package-buildroots').iterdir()), [])
                if kind == 'debs':
                    self.assertEqual(list((work / 'debbuild').iterdir()), [])
                    description = ROOT / 'packaging/deb/linux-firmware-gaokun3/descriptions/package.in'
                    self.assertIn(description.read_text().strip(), firmware['control'])
                else:
                    images = list((work / 'artifacts').glob('vmlinuz-*.efi'))
                    self.assertEqual(len(images), 2)
                    for image in images:
                        subprocess.run(['sha256sum', '-c', image.name + '.sha256'],
                                       cwd=image.parent, check=True, capture_output=True)
                    for folder in ('SOURCES', 'BUILD', 'BUILDROOT'):
                        self.assertEqual(list((work / 'rpmbuild' / folder).iterdir()), [])
                    self.assertEqual(list((work / 'rpmbuild/RPMS').rglob('*.rpm')), [])
                manifest = json.loads((work / 'artifacts/package-manifest.json').read_text())
                for variant in ('standard', 'el2'):
                    for name in manifest['kernels'][variant]['packages'].values():
                        self.assertTrue((work / 'artifacts' / name).is_file(), name)
                    for suffix in ('config', 'json'):
                        self.assertTrue((work / f'artifacts/kernel-config-{variant}.{suffix}').is_file())
                for name in manifest['packages'].values():
                    self.assertTrue((work / 'artifacts' / name).is_file(), name)
                for out in ('kernel-out', 'kernel-out-el2'):
                    self.assertTrue((work / out / 'drivers/test/driver.o').is_file())

    def test_failed_packager_retains_retry_inputs_and_exit_code(self):
        for kind in ('debs', 'rpms'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                work, env = self.package_fixture(directory, kind)
                env['FAKE_PACKAGER_FAIL'] = '1'
                result = self.run_package(kind, env)
                self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
                self.assertIn('packaging exit', result.stdout)
                retry_input = ('package-buildroots/linux-image-gaokun3/boot' if kind == 'debs'
                               else 'rpmbuild/SOURCES/kernel-gaokun3.tar.gz')
                self.assertTrue((work / retry_input).exists())

    def test_failed_archive_retains_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            work, env = self.package_fixture(directory, 'rpms')
            put(Path(directory) / 'bin/tar', '#!/bin/sh\nexit 19\n', executable=True)
            result = self.run_package('rpms', env)
            self.assertEqual(result.returncode, 19, result.stdout + result.stderr)
            self.assertTrue((work / 'package-buildroots/kernel-gaokun3/boot').is_dir())


if __name__ == '__main__':
    unittest.main()
