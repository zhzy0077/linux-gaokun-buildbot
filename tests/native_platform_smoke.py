#!/usr/bin/env python3
"""Read-only checks on Gaokun using an extracted gaokun3-platform RPM.

Run this separately from unit tests, on the target machine:
  python3 tests/native_platform_smoke.py /path/to/extracted-rpms
No controls are changed, no services are restarted, and no kernel is loaded.
"""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import platform
import re


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_ucm_verbs(root, card):
    os.environ['ALSA_CONFIG_UCM2'] = str(root / 'usr/share/gaokun3/ucm2')
    alsa = ctypes.CDLL('libasound.so.2')
    alsa.snd_use_case_mgr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
    alsa.snd_use_case_mgr_open.restype = ctypes.c_int
    alsa.snd_use_case_mgr_close.argtypes = [ctypes.c_void_p]
    alsa.snd_use_case_mgr_close.restype = ctypes.c_int
    alsa.snd_use_case_get_list.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                         ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p))]
    alsa.snd_use_case_get_list.restype = ctypes.c_int
    alsa.snd_use_case_free_list.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_int]
    alsa.snd_use_case_free_list.restype = ctypes.c_int
    handle = ctypes.c_void_p()
    status = alsa.snd_use_case_mgr_open(ctypes.byref(handle), f'hw:{card}'.encode())
    if status < 0:
        raise RuntimeError(f'Gaokun UCM open failed: {status}')
    try:
        values = ctypes.POINTER(ctypes.c_char_p)()
        count = alsa.snd_use_case_get_list(handle, b'_verbs', ctypes.byref(values))
        if count < 0:
            raise RuntimeError(f'Gaokun UCM verb query failed: {count}')
        try:
            return [values[i].decode() for i in range(0, count, 2)]
        finally:
            if count:
                alsa.snd_use_case_free_list(values, count)
    finally:
        alsa.snd_use_case_mgr_close(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    if platform.machine() != 'aarch64':
        parser.error('Run this hardware check on aarch64 Gaokun')
    root = args.root.resolve()
    cards = Path('/proc/asound/cards').read_text()
    matches = re.findall(r'^\s*(\d+)\s+\[[^\]]+\]:\s+sc8280xp - SC8280XP-HUAWEI-GAOKUN3\s*$',
                         cards, re.MULTILINE)
    if len(matches) != 1:
        parser.error('Expected exactly one Gaokun3 sound card')
    report = {'architecture': platform.machine(), 'running_kernel': platform.release()}
    report['ucm_verbs'] = read_ucm_verbs(root, int(matches[0]))
    if 'HiFi' not in report['ucm_verbs']:
        raise RuntimeError('Gaokun UCM has no HiFi verb')

    tuner = load_module('gaokun_tuner', root / 'usr/share/gaokun3/touchscreen-tuner/tune.py')
    report['gtk4_adwaita_import'] = True
    algo = tuner.find_algo_dir()
    report['touchscreen_algorithm_found'] = algo is not None
    if algo is not None:
        report['touchscreen_parameters'] = {
            name: int((algo / name).read_text())
            for name in ('cmf_enabled', 'iir_enabled', 'peak_threshold')
        }

    bluetooth = load_module('gaokun_bluetooth', root / 'usr/libexec/gaokun3/patch-nvm-bdaddr.py')
    firmware = root / 'usr/lib/firmware/qca/wcnhpnv21g.bin'
    offset = bluetooth.parse_nvm_find_bdaddr(firmware.read_bytes())
    if offset is None:
        raise RuntimeError('No Bluetooth address field in the packaged NVM')
    report['bluetooth_nvm_address_field_found'] = True
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
