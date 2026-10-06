import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('validate_tpm',Path(__file__).with_name('native_tpm_smoke.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ValidationTests(unittest.TestCase):
    def reply(self, response, code=0x17a, payload=b''):
        with patch.object(module.os,'write',side_effect=lambda fd,data:len(data)), \
             patch.object(module.select,'select',return_value=([3],[],[])), \
             patch.object(module.os,'read',return_value=response):
            return module.command(3,code,payload)

    def test_forbidden_commands_do_not_reach_device(self):
        for code in (0x126,0x122,0x137,0x144,0x145,0x182):
            with patch.object(module.os,'write') as write, self.assertRaises(ValueError):
                module.command(3,code,b'')
            write.assert_not_called()

    def test_valid_header(self):
        payload=b'test'
        self.assertEqual(self.reply(struct.pack('>HII',0x8001,14,0)+payload)[0],payload)

    def test_bad_response_headers_rejected(self):
        for data in (b'',b'123',struct.pack('>HII',0x8002,10,0),
                     struct.pack('>HII',0x8001,20,0),struct.pack('>HII',0x8001,10,0x100)):
            with self.assertRaises(RuntimeError):self.reply(data)

    @staticmethod
    def pcr_reply(indices, counter=54):
        bitmap=bytearray(3)
        for i in indices:bitmap[i//8]|=1<<(i%8)
        return (struct.pack('>IIHB',counter,1,11,3)+bitmap+
                struct.pack('>I',len(indices))+
                b''.join(struct.pack('>H',32)+bytes([i])*32 for i in indices))

    def test_partial_pcr_reads_make_progress(self):
        replies=[(self.pcr_reply(list(range(8))),.1),(self.pcr_reply([11]),.1)]
        with patch.object(module,'command',side_effect=replies) as command:
            result=module.read_pcrs(3,11,[*range(8),11])
        self.assertEqual(result['batches'],2)
        self.assertEqual(set(result['values']),{str(i) for i in [*range(8),11]})
        self.assertEqual(command.call_args_list[1].args[2][-3:],b'\x00\x08\x00')

    def test_partial_pcr_reads_reject_no_progress_extra_and_counter_change(self):
        for selection in ([],[12]):
            with patch.object(module,'command',return_value=(self.pcr_reply(selection),.1)), self.assertRaises(RuntimeError):
                module.read_pcrs(3,11,[0])
        with patch.object(module,'command',side_effect=[(self.pcr_reply([0]),.1),(self.pcr_reply([1],55),.1)]),self.assertRaises(RuntimeError):
            module.read_pcrs(3,11,[0,1])

    def test_selects_an_allocated_supported_pcr_bank(self):
        sha1={'algorithm':4,'pcrs':[0,1]}
        sha256={'algorithm':11,'pcrs':[0,11]}
        empty_sha256={'algorithm':11,'pcrs':[]}
        self.assertIs(module.select_pcr_bank([sha1,sha256]),sha256)
        self.assertIs(module.select_pcr_bank([empty_sha256,sha1]),sha1)
        for banks in ([empty_sha256], [{'algorithm':12,'pcrs':[0]}], [{'algorithm':11,'pcrs':[23]}]):
            with self.assertRaises(RuntimeError):module.select_pcr_bank(banks)

    def test_unsupported_hash_is_rejected_before_io(self):
        with patch.object(module,'command') as command, self.assertRaises(ValueError):
            module.read_pcrs(3,12,[0])
        command.assert_not_called()

    def test_capability_header_validated(self):
        data=struct.pack('>BII',0,6,1)+struct.pack('>II',0x105,0x51434f4d)
        with patch.object(module,'command',return_value=(data,0.1)):
            props,count,more,elapsed=module.capability(3,6,0x100,32)
            self.assertEqual(count,1);self.assertFalse(more)
            self.assertEqual(struct.unpack('>II',props),(0x105,0x51434f4d))
        for bad in (b'',struct.pack('>BII',0,5,1),struct.pack('>BII',2,6,1),struct.pack('>BII',0,6,257)):
            with patch.object(module,'command',return_value=(bad,0.1)),self.assertRaises(RuntimeError):
                module.capability(3,6,0x100,32)

if __name__=='__main__':unittest.main()
