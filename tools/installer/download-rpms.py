#!/usr/bin/python3
"""Download a pinned GitHub Gaokun release and verify API asset digests."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def download(repository, tag, output, kernel_tag=None):
    output.mkdir(parents=True, exist_ok=True)
    release = json.loads(subprocess.check_output(
        ['gh', 'api', f'repos/{repository}/releases/tags/{tag}'], text=True))
    assets = {a['name']: a for a in release['assets']}

    def asset(name):
        if Path(name).name != name:
            raise ValueError('Invalid release asset name')
        metadata = assets[name]
        expected = metadata.get('digest', '')
        if not expected.startswith('sha256:'):
            raise ValueError('Release asset has no SHA-256: ' + name)
        subprocess.run(['gh', 'release', 'download', tag, '--repo', repository,
                        '--dir', str(output), '--pattern', name], check=True)
        path = output / name
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected != 'sha256:' + actual:
            raise ValueError('Release asset digest mismatch: ' + name)
        return actual

    sums = {'package-manifest.json': asset('package-manifest.json')}
    data = json.loads((output / 'package-manifest.json').read_text())
    if data['package_release_tag'] != tag or data['build_el2'] is not True:
        raise ValueError('Expected the requested EL2 package release')
    if kernel_tag is not None and data['kernel_tag'] != kernel_tag:
        raise ValueError('Kernel tag differs from the requested image version')
    names = [data['kernels']['el2']['packages'][k] for k in ('kernel', 'kernel_modules')]
    names += [data['packages'][k] for k in ('firmware', 'platform')]
    for name in names:
        sums[name] = asset(name)
    (output / 'download-sha256.json').write_text(json.dumps(sums, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kernel-tag')
    args = parser.parse_args()
    download(args.repository, args.tag, args.output, args.kernel_tag)
