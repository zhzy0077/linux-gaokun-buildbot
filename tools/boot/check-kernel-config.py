#!/usr/bin/python3
"""Pin vendor baselines, verify board contracts and report Kconfig migration."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_config(text):
    values = {}
    for line in text.splitlines():
        if line.startswith('CONFIG_'):
            key, value = line.split('=', 1)
            values[key] = value
        else:
            match = re.fullmatch(r'# (CONFIG_\w+) is not set', line)
            if match:
                values[match[1]] = 'n'
    return values


def verify(config, fragments):
    expected = {}
    for fragment in fragments:
        expected.update(parse_config(fragment.read_text()))
    actual = parse_config(config.read_text())
    errors = [f'{key}: expected {value}, got {actual.get(key, "absent")}'
              for key, value in expected.items()
              if actual.get(key, 'n') != value]
    if errors:
        raise ValueError('\n'.join(errors))


def baseline(repo, distro):
    directory = repo / 'defconfig/baselines'
    record = json.loads((directory / 'sources.json').read_text())[distro]
    path = directory / record['config']
    if path.parent != directory or digest(path) != record['config_sha256']:
        raise ValueError('Vendor baseline digest/path mismatch')
    return path, record


def fragments(repo, distro, el2):
    paths = [repo / 'defconfig/distro/common.config',
             repo / f'defconfig/distro/{distro}.config',
             repo / 'defconfig/gaokun3-required.config']
    if el2:
        paths.append(repo / 'defconfig/gaokun3-el2.config')
    return paths


def differences(before, after):
    return {key: {'before': before.get(key), 'after': after.get(key)}
            for key in sorted(before.keys() | after.keys())
            if before.get(key) != after.get(key)}


def audit(repo, distro, output, el2):
    raw, origin = baseline(repo, distro)
    paths = fragments(repo, distro, el2)
    config = output / '.config'
    verify(config, paths)
    original = parse_config(raw.read_text())
    normalized = parse_config((output / 'vendor-normalized.config').read_text())
    actual = parse_config(config.read_text())
    requirements = {}
    for path in paths:
        reason = ''
        for line in path.read_text().splitlines():
            if line.startswith('# ') and not line.startswith('# CONFIG_'):
                reason = line[2:]
            for key, value in parse_config(line).items():
                if not reason:
                    raise ValueError('Missing override reason: ' + key)
                requirements[key] = {'value': value, 'reason': reason,
                                     'fragment': str(path.relative_to(repo))}
    report = {'distro': distro, 'variant': 'el2' if el2 else 'standard',
              'baseline': origin, 'config_sha256': digest(config),
              'fragments': {str(p.relative_to(repo)): digest(p) for p in paths},
              'requirements': requirements,
              'baseline_normalization': differences(original, normalized),
              'board_delta': differences(normalized, actual)}
    # Separate explicit overrides from consequences resolved by Kconfig.
    for key, change in report['board_delta'].items():
        change['classification'] = 'explicit' if key in requirements else 'kconfig_dependency'
    (output / 'kernel-config-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{distro}/{report["variant"]}: {len(report["board_delta"])} board/build differences; '
          f'{len(report["baseline_normalization"])} vendor-to-source normalization differences')


def verify_package(repo, distro, output):
    report = json.loads((output / 'kernel-config-report.json').read_text())
    _, origin = baseline(repo, distro)
    if report['distro'] != distro or report['baseline'] != origin:
        raise ValueError('Wrong distribution baseline at packaging')
    if report['config_sha256'] != digest(output / '.config'):
        raise ValueError('Kernel config changed after validation')
    paths = fragments(repo, distro, report['variant'] == 'el2')
    if report['fragments'] != {str(p.relative_to(repo)): digest(p) for p in paths}:
        raise ValueError('Configuration fragments changed after build')
    verify(output / '.config', paths)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', nargs='?', type=Path)
    parser.add_argument('fragments', nargs='*', type=Path)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--distro', choices=['fedora', 'ubuntu'])
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--el2', action='store_true')
    args = parser.parse_args()
    if args.baseline or args.audit or args.package:
        if not args.repo or not args.distro:
            parser.error('--repo and --distro are required')
        if args.baseline:
            print(baseline(args.repo, args.distro)[0])
        elif args.audit:
            audit(args.repo, args.distro, args.audit, args.el2)
        else:
            verify_package(args.repo, args.distro, args.package)
    else:
        if not args.config or not args.fragments:
            parser.error('Provide a config and requirement fragments')
        verify(args.config, args.fragments)
        print('Distribution kernel configuration verified.')
