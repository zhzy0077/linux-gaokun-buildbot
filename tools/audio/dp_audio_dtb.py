#!/usr/bin/env python3
"""Prepare an additive Gaokun DP-audio DTB; never install or select a boot entry.

Requires libfdt.so.1, dtc and fdtoverlay. Use the new kernel's matching DTB as
input. Existing outputs are refused. The separate verified DPAUDIO topology
and UCM configuration must also be present when booting this sound model.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

MODEL = 'SC8280XP-HUAWEI-GAOKUN3-DPAUDIO'
ORIGINAL_MODEL = b'SC8280XP-HUAWEI-GAOKUN3\0'
APM = '/soc@0/remoteproc@3000000/glink-edge/gpr/service@1'
DP = '/soc@0/display-subsystem@ae00000/displayport-controller@'
DAIS = (104, 129)


def read_nodes(blob):
    lib = C.CDLL('libfdt.so.1')
    pointer = C.c_void_p
    for name, args, result in [
        ('fdt_check_full', [pointer, C.c_size_t], C.c_int),
        ('fdt_next_node', [pointer, C.c_int, C.POINTER(C.c_int)], C.c_int),
        ('fdt_get_path', [pointer, C.c_int, pointer, C.c_int], C.c_int),
        ('fdt_first_property_offset', [pointer, C.c_int], C.c_int),
        ('fdt_next_property_offset', [pointer, C.c_int], C.c_int),
        ('fdt_getprop_by_offset', [pointer, C.c_int, C.POINTER(C.c_char_p),
                                 C.POINTER(C.c_int)], pointer),
    ]:
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    buffer = C.create_string_buffer(blob)
    if lib.fdt_check_full(buffer, len(blob)):
        raise ValueError('Invalid or truncated DTB')
    nodes = {}
    offset = -1
    while True:
        offset = lib.fdt_next_node(buffer, offset, None)
        if offset == -1:  # FDT_ERR_NOTFOUND
            break
        if offset < 0:
            raise ValueError(f'Cannot enumerate DTB nodes: {offset}')
        path = C.create_string_buffer(4096)
        if lib.fdt_get_path(buffer, offset, path, len(path)):
            raise ValueError('Cannot read DTB node path')
        properties = {}
        prop = lib.fdt_first_property_offset(buffer, offset)
        while prop >= 0:
            name, size = C.c_char_p(), C.c_int()
            value = lib.fdt_getprop_by_offset(buffer, prop, C.byref(name), C.byref(size))
            if size.value < 0 or not name.value:
                raise ValueError('Cannot read DTB property')
            properties[name.value.decode()] = C.string_at(value, size.value)
            prop = lib.fdt_next_property_offset(buffer, prop)
        if prop != -1:
            raise ValueError('Cannot enumerate DTB properties')
        nodes[path.value.decode()] = properties
    return nodes


def reservations(blob):
    # Call only after read_nodes() has validated the entire blob.
    position = struct.unpack_from('>I', blob, 16)[0]
    result = []
    while position + 16 <= len(blob):
        pair = struct.unpack_from('>QQ', blob, position)
        if pair == (0, 0):
            return result
        result.append(pair)
        position += 16
    raise ValueError('Unterminated DTB memory reservation map')


def additions(nodes):
    if b'huawei,gaokun3' not in nodes['/'].get('compatible', b'').split(b'\0'):
        raise ValueError('Input must be a Gaokun3 DTB')
    if nodes['/sound'].get('model') != ORIGINAL_MODEL:
        raise ValueError('Input must use the original Gaokun3 sound model')

    def phandle(path, cells):
        props = nodes[path]
        if props.get('#sound-dai-cells') != struct.pack('>I', cells):
            raise ValueError(f'Unexpected DAI argument count: {path}')
        value = props.get('phandle', b'')
        if len(value) != 4 or value in (bytes(4), b'\xff' * 4):
            raise ValueError(f'Missing/invalid phandle: {path}')
        return value

    platform, cpu = phandle(APM, 0), phandle(APM + '/bedais', 1)
    result = {}
    for index, address in enumerate(('ae90000', 'ae98000')):
        path = f'/sound/displayport-{index}-dai-link'
        result[path] = {'link-name': f'DisplayPort{index} Playback\0'.encode()}
        result[path + '/cpu'] = {'sound-dai': cpu + struct.pack('>I', DAIS[index])}
        result[path + '/codec'] = {'sound-dai': phandle(DP + address, 0)}
        result[path + '/platform'] = {'sound-dai': platform}
    if set(result) & nodes.keys():
        raise ValueError('DP audio nodes already exist in the input DTB')
    return result


def overlay_source(nodes):
    links = additions(nodes)
    lines = ['/dts-v1/;', '/plugin/;', '/ {', ' fragment@0 {',
             '  target-path = "/sound";', '  __overlay__ {', f'   model = "{MODEL}";']
    for index in range(2):
        path = f'/sound/displayport-{index}-dai-link'
        lines += [f'   displayport-{index}-dai-link {{',
                  f'    link-name = "DisplayPort{index} Playback";']
        for child in ('cpu', 'codec', 'platform'):
            raw = links[path + '/' + child]['sound-dai']
            cells = ' '.join(f'0x{item[0]:x}' for item in struct.iter_unpack('>I', raw))
            lines.append(f'    {child} {{ sound-dai = <{cells}>; }};')
        lines.append('   };')
    lines += ['  };', ' };', '};']
    return '\n'.join(lines) + '\n'


def verify(before_blob, after_blob):
    before, after = read_nodes(before_blob), read_nodes(after_blob)
    expected = {path: dict(props) for path, props in before.items()}
    expected.update(additions(before))
    expected['/sound']['model'] = MODEL.encode() + b'\0'
    if after != expected:
        raise ValueError('Output changed properties beyond the sound model and eight DP nodes')
    if reservations(before_blob) != reservations(after_blob):
        raise ValueError('Output changed the DTB memory reservation map')


def prepare(source, output, dtc='dtc', fdtoverlay='fdtoverlay'):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite {output}')
    original = source.read_bytes()
    overlay = overlay_source(read_nodes(original))
    with tempfile.TemporaryDirectory(prefix='gaokun-audio-dtb-') as directory:
        base = Path(directory)
        (base / 'original.dtb').write_bytes(original)
        (base / 'audio.dts').write_text(overlay)
        subprocess.run([dtc, '-I', 'dts', '-O', 'dtb', '-o', str(base / 'audio.dtbo'),
                        str(base / 'audio.dts')], check=True, capture_output=True)
        subprocess.run([fdtoverlay, '-i', str(base / 'original.dtb'),
                        '-o', str(base / 'result.dtb'), str(base / 'audio.dtbo')],
                       check=True, capture_output=True)
        result = (base / 'result.dtb').read_bytes()
        verify(original, result)
    with output.open('xb') as stream:
        stream.write(result)
    return {'source': str(source), 'output': str(output), 'sound_model': MODEL,
            'source_sha256': hashlib.sha256(original).hexdigest(),
            'output_sha256': hashlib.sha256(result).hexdigest(),
            'existing_properties_preserved': True, 'reservations_preserved': True,
            'installed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dtc', default='dtc')
    parser.add_argument('--fdtoverlay', default='fdtoverlay')
    args = parser.parse_args()
    print(json.dumps(prepare(args.dtb, args.output, args.dtc, args.fdtoverlay), indent=2))
