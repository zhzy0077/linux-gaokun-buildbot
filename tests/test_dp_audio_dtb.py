"""Keep the working audio experiment independent of kernel/EL2 DTB changes."""
import ctypes
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dp_audio_dtb', ROOT / 'tools/audio/dp_audio_dtb.py')
audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio)
DTC = os.environ.get('GAOKUN_DTC') or shutil.which('dtc')
OVERLAY = os.environ.get('GAOKUN_FDTOVERLAY') or shutil.which('fdtoverlay')
try:
    ctypes.CDLL('libfdt.so.1')
    LIBFDT = True
except OSError:
    LIBFDT = False


def cells(*values):
    return struct.pack('>' + 'I' * len(values), *values)


def sample_nodes():
    nodes = {'/': {'compatible': b'huawei,gaokun3\0qcom,sc8280xp\0'},
             '/sound': {'model': audio.ORIGINAL_MODEL, 'audio-routing': b'keep this\0'}}
    for path, handle, count in ((audio.APM, 11, 0), (audio.APM + '/bedais', 12, 1),
                                (audio.DP + 'ae90000', 13, 0), (audio.DP + 'ae98000', 14, 0)):
        nodes[path] = {'phandle': cells(handle), '#sound-dai-cells': cells(count)}
    return nodes


class AudioDtbTests(unittest.TestCase):
    def test_dynamic_phandles_and_both_display_port_dais(self):
        nodes = sample_nodes()
        links = audio.additions(nodes)
        self.assertEqual(len(links), 8)
        for index, dai in enumerate((104, 129)):
            path = f'/sound/displayport-{index}-dai-link'
            self.assertEqual(links[path + '/cpu']['sound-dai'], cells(12, dai))
            self.assertEqual(links[path + '/codec']['sound-dai'], cells(13 + index))
            self.assertEqual(links[path + '/platform']['sound-dai'], cells(11))
        nodes[audio.APM]['phandle'] = cells(200)
        self.assertEqual(audio.additions(nodes)['/sound/displayport-0-dai-link/platform']['sound-dai'], cells(200))

    def test_wrong_board_model_cells_and_existing_links_are_rejected(self):
        for path, key, value in [('/', 'compatible', b'lenovo,thinkpad-x13s\0'),
                                 ('/sound', 'model', audio.MODEL.encode() + b'\0'),
                                 (audio.APM + '/bedais', '#sound-dai-cells', cells(0)),
                                 (audio.APM, 'phandle', cells(0))]:
            nodes = sample_nodes()
            nodes[path][key] = value
            with self.subTest(path=path, key=key), self.assertRaises(ValueError):
                audio.additions(nodes)
        nodes = sample_nodes()
        nodes['/sound/displayport-0-dai-link/cpu'] = {}
        with self.assertRaises(ValueError):
            audio.additions(nodes)

    def test_preserved_ucm_routes_and_real_jack_controls(self):
        root = ROOT / 'tools/audio/dp-audio/ucm2'
        configs = root / 'Qualcomm/gaokun-dp-audio'
        base = (configs / 'HiFi.conf').read_text()
        self.assertEqual(hashlib.sha256(base.encode()).hexdigest(),
                         '28ec1cada16874985a51d734810e273dc25ea3f98cf847a5cbebee9358c8330d')
        marker = '\t\tcset "name=\'MultiMedia3 Mixer TX_CODEC_DMA_TX_3\' 1"'
        self.assertEqual(base.count(marker), 1)
        for index in range(2):
            route = f'\n\t\tcset "name=\'DISPLAY_PORT_RX_{index} Audio Mixer MultiMedia{index+5}\' 1"'
            device = f'''
SectionDevice."HDMI{index}" {{
    Comment "USB-C DisplayPort {index} playback"
    Value {{
        PlaybackPriority 200
        PlaybackPCM "hw:${{CardId}},{index+4}"
        PlaybackChannels 2
        JackControl "DP{index} Jack"
    }}
}}
'''
            self.assertEqual((configs / f'HiFi-DP{index}.conf').read_text(),
                             base.replace(marker, marker + route) + device)
        selectors = root / 'conf.d/sc8280xp'
        self.assertEqual((selectors / (audio.MODEL + '.conf')).resolve(),
                         (configs / 'DP-Audio.conf').resolve())
        selector = (selectors / 'HUAWEI-GK_W7X-M1010-GK_W7X_PCB.conf').read_text()
        self.assertIn('String1 "${CardName}"', selector)
        self.assertIn(f'String2 "{audio.MODEL}"', selector)
        self.assertIn('False.Include.original.File "/Qualcomm/sc8280xp/sc8280xp.conf"', selector)

    @unittest.skipUnless(DTC and OVERLAY and LIBFDT, 'dtc, fdtoverlay and libfdt required')
    def test_real_overlay_preserves_el2_reservations_and_other_properties(self):
        # Deliberately different phandles from the hardware dump.
        dts = '''/dts-v1/;
/memreserve/ 0x90000000 0x10000;
/ {
 compatible = "huawei,gaokun3", "qcom,sc8280xp";
 #address-cells = <2>; #size-cells = <2>;
 sound { model = "SC8280XP-HUAWEI-GAOKUN3"; audio-routing = "unchanged"; };
 reserved-memory {
  #address-cells = <2>; #size-cells = <2>; ranges;
  hyp@90000000 { reg = <0 0x90000000 0 0x10000>; no-map; };
 };
 soc@0 {
  remoteproc@3000000 { glink-edge { gpr { service@1 {
   phandle = <11>; #sound-dai-cells = <0>;
   bedais { phandle = <12>; #sound-dai-cells = <1>; };
  }; }; }; };
  display-subsystem@ae00000 {
   displayport-controller@ae90000 { phandle = <13>; #sound-dai-cells = <0>; };
   displayport-controller@ae98000 { phandle = <14>; #sound-dai-cells = <0>; };
  };
 };
};
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'original.dtb', root / 'audio.dtb'
            (root / 'input.dts').write_text(dts)
            subprocess.run([DTC, '-q', '-I', 'dts', '-O', 'dtb', '-o', str(source),
                            str(root / 'input.dts')], check=True, capture_output=True)
            original = source.read_bytes()
            report = audio.prepare(source, output, DTC, OVERLAY)
            self.assertFalse(report['installed'])
            self.assertEqual(source.read_bytes(), original)
            result = output.read_bytes()
            audio.verify(original, result)
            self.assertEqual(audio.reservations(result), [(0x90000000, 0x10000)])
            nodes = audio.read_nodes(result)
            self.assertIn('/reserved-memory/hyp@90000000', nodes)
            self.assertEqual(nodes['/sound']['model'], audio.MODEL.encode() + b'\0')
            with self.assertRaises(FileExistsError):
                audio.prepare(source, output, DTC, OVERLAY)
            self.assertEqual(output.read_bytes(), result)
            with self.assertRaises(FileExistsError):
                audio.prepare(source, source, DTC, OVERLAY)
            self.assertEqual(source.read_bytes(), original)
            # The verifier catches unrelated DT changes as well as reserved-map changes.
            damaged = result.replace(b'unchanged\0', b'corrupted\0')
            with self.assertRaises(ValueError):
                audio.verify(original, damaged)
            damaged = bytearray(result)
            position = struct.unpack_from('>I', result, 16)[0]
            struct.pack_into('>Q', damaged, position, 0x91000000)
            with self.assertRaises(ValueError):
                audio.verify(original, bytes(damaged))
            with self.assertRaises(ValueError):
                audio.read_nodes(original[:20])


if __name__ == '__main__':
    unittest.main()
