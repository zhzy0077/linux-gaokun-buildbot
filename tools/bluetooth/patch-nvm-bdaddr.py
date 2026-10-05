#!/usr/bin/env python3
"""Prepare WCN6855 Bluetooth NVM addresses before loading hci_uart."""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile

FIRMWARE_DIR = Path('/lib/firmware/qca')
NVM_PATTERNS = ('wcnhpnv21*.bin', 'hpnv21.*', 'hpnv21g.*')
SERIAL_PATH = Path('/sys/class/dmi/id/product_serial')
MACHINE_ID_PATH = Path('/etc/machine-id')
BD_ADDR_TAG_ID = 2
TLV_HEADER_SIZE = 4
ENTRY_HEADER_SIZE = 12
BD_ADDR_LEN = 6


def parse_nvm_find_bdaddr(data):
    """Return the validated BDADDR TLV payload offset."""
    if len(data) < TLV_HEADER_SIZE:
        return None
    header = struct.unpack_from('<I', data)[0]
    kind, length, offset = header & 255, header >> 8, TLV_HEADER_SIZE
    if offset + length > len(data):
        return None
    outer_end = offset + length
    if kind == 4:
        if length < TLV_HEADER_SIZE:
            return None
        header = struct.unpack_from('<I', data, offset)[0]
        kind, length, offset = header & 255, header >> 8, offset + TLV_HEADER_SIZE
    if kind != 2 or offset + length > outer_end:
        return None
    end = offset + length
    found = None
    while offset < end:
        if offset + ENTRY_HEADER_SIZE > end:
            return None
        tag, size = struct.unpack_from('<HH', data, offset)
        payload = offset + ENTRY_HEADER_SIZE
        if payload + size > end:
            return None
        if tag == BD_ADDR_TAG_ID:
            if size != BD_ADDR_LEN or found is not None:
                return None
            found = payload
        offset = payload + size
    return found


def generate_bdaddr(serial):
    """Generate a locally-administered unicast address from the serial."""
    digest = hashlib.md5(serial.encode("utf-8")).digest()
    first = (digest[0] | 0x02) & 0xFE
    return bytes([first, *digest[1:BD_ADDR_LEN]])


def read_serial():
    try:
        serial = SERIAL_PATH.read_text().strip()
    except OSError:
        serial = ''
    if serial and serial.lower() not in {'default string', 'unknown', 'none', 'to be filled by o.e.m.'}:
        return serial
    machine_id = MACHINE_ID_PATH.read_text().strip()
    if not re.fullmatch('[0-9a-f]{32}', machine_id) or machine_id == '0' * 32:
        raise ValueError('A unique hardware serial or initialized machine-id is required')
    return 'machine-id:' + machine_id


def iter_nvm_files():
    root = FIRMWARE_DIR.resolve()
    seen = set()
    for pattern in NVM_PATTERNS:
        for path in sorted(FIRMWARE_DIR.glob(pattern)):
            if path.name.endswith('.orig') or not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if resolved.parent != root:
                raise ValueError('Firmware link escapes qca directory: ' + str(path))
            if resolved not in seen:
                seen.add(resolved)
                yield resolved


def patch_file(path, desired_addr):
    data = bytearray(path.read_bytes())
    offset = parse_nvm_find_bdaddr(data)
    if offset is None:
        raise ValueError('Unsupported or malformed Bluetooth NVM: ' + str(path))
    if len(desired_addr) != BD_ADDR_LEN:
        raise ValueError('Invalid Bluetooth address length')
    if data[offset:offset + BD_ADDR_LEN] == desired_addr[::-1]:
        return False
    backup = path.with_name(path.name + '.orig')
    if backup.is_symlink():
        raise ValueError('Refusing symlink backup: ' + str(backup))
    if not backup.exists():
        with backup.open('xb') as output:
            output.write(data)
        shutil.copystat(path, backup)
    data[offset:offset + BD_ADDR_LEN] = desired_addr[::-1]
    # Replacing the inode avoids changing unrelated hard-linked firmware aliases.
    stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix='.gaokun-nvm-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(path, temporary)
        if os.geteuid() == 0:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    with open('/run/gaokun-bluetooth-nvm.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        files = list(iter_nvm_files())
        if not files:
            raise ValueError('No supported WCN6855 NVM files found')
        # Validate every candidate before modifying any firmware.
        for path in files:
            if parse_nvm_find_bdaddr(path.read_bytes()) is None:
                raise ValueError('Unsupported or malformed Bluetooth NVM: ' + str(path))
        desired = generate_bdaddr(read_serial())
        changed = sum(patch_file(path, desired) for path in files)
        print('Prepared %d NVM files (%d changed), BDADDR %s' %
              (len(files), changed, ':'.join('%02X' % b for b in desired)))


if __name__ == '__main__':
    main()
