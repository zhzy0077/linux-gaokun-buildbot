#!/usr/bin/python3
"""Reject distribution fragment options silently dropped by Kconfig."""
import argparse
from pathlib import Path
import re


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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('fragments', nargs='+', type=Path)
    args = parser.parse_args()
    verify(args.config, args.fragments)
    print('Distribution kernel configuration verified.')
