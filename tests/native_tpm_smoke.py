#!/usr/bin/python3
"""Only GetCapability, PCR_Read and GetRandom; never provisions TPM objects."""
import argparse
import json
import os
from pathlib import Path
import select
import stat
import struct
import time

GET_CAPABILITY=0x17a
GET_RANDOM=0x17b
PCR_READ=0x17e
ALLOWED={GET_CAPABILITY,GET_RANDOM,PCR_READ}
PCR_DIGEST_SIZES={4:20,0xb:32}
PCR_INDICES=(0,1,2,3,4,5,6,7,11)


def command(fd, code, payload):
    if code not in ALLOWED:
        raise ValueError('Command is outside this read-only test')
    request=struct.pack('>HII',0x8001,10+len(payload),code)+payload
    start=time.monotonic()
    if os.write(fd,request)!=len(request):
        raise RuntimeError('Short TPM command write')
    if not select.select([fd],[],[],10)[0]:
        raise TimeoutError('TPM response timeout')
    response=os.read(fd,4096)
    if len(response)<10:
        raise RuntimeError('Short TPM response')
    tag,size,rc=struct.unpack_from('>HII',response)
    if tag!=0x8001 or size!=len(response) or size>4096:
        raise RuntimeError('Invalid TPM response header')
    if rc:
        raise RuntimeError(f'TPM command {code:#x} returned {rc:#x}')
    return response[10:],round(time.monotonic()-start,4)


def capability(fd,cap,first,count):
    data,elapsed=command(fd,GET_CAPABILITY,struct.pack('>III',cap,first,count))
    if len(data)<9:
        raise RuntimeError('Truncated capability response')
    more,returned,n=struct.unpack_from('>BII',data)
    if returned!=cap or more>1 or n>256:
        raise RuntimeError('Invalid capability response')
    return data[9:],n,bool(more),elapsed


def select_pcr_bank(banks):
    for algorithm in (0xb,4):
        for bank in banks:
            if bank['algorithm']==algorithm and any(i in PCR_INDICES for i in bank['pcrs']):
                return bank
    raise RuntimeError('No active SHA256 or SHA1 bank for the requested PCRs')


def read_pcrs(fd, algorithm, indices):
    if algorithm not in PCR_DIGEST_SIZES:
        raise ValueError('Only SHA256 and SHA1 PCR reads are supported')
    pending=set(indices)
    if not pending or any(i<0 or i>=24 for i in pending):
        raise ValueError('Expected PCR indices in 0..23')
    values={}; update_counter=None; elapsed=0; batches=0
    while pending:
        selection=bytearray(3)
        for i in pending:selection[i//8]|=1<<(i%8)
        data,duration=command(fd,PCR_READ,struct.pack('>IHB',1,algorithm,3)+selection)
        elapsed+=duration; batches+=1
        if len(data)<11:raise RuntimeError('Short PCR response')
        counter,selections=struct.unpack_from('>II',data)
        if update_counter is None:update_counter=counter
        if counter!=update_counter:raise RuntimeError('PCRs changed during multi-part read; retry the test')
        if selections!=1:raise RuntimeError('Unexpected PCR response bank count')
        alg,length=struct.unpack_from('>HB',data,8);offset=11
        if not 1<=length<=8 or offset+length+4>len(data):raise RuntimeError('Invalid PCR response selection')
        bitmap=data[offset:offset+length];offset+=length
        returned=[i for i in range(length*8) if bitmap[i//8]&(1<<(i%8))]
        if alg!=algorithm or not returned or not set(returned)<=pending:
            raise RuntimeError('PCR selection mismatch or no progress')
        count=struct.unpack_from('>I',data,offset)[0];offset+=4
        if count!=len(returned):raise RuntimeError('PCR digest count mismatch')
        for i in returned:
            if offset+2>len(data):raise RuntimeError('Truncated PCR digest')
            size=struct.unpack_from('>H',data,offset)[0];offset+=2
            if size!=PCR_DIGEST_SIZES[alg] or offset+size>len(data):raise RuntimeError('Invalid PCR digest size')
            values[str(i)]=data[offset:offset+size].hex();offset+=size
        if offset!=len(data):raise RuntimeError('Unexpected trailing PCR bytes')
        pending.difference_update(returned)
    return {'algorithm':hex(algorithm),'update_counter':update_counter,'values':values,
            'seconds':round(elapsed,4),'batches':batches}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',default='/dev/tpmrm0')
    args=parser.parse_args()
    if args.device not in ('/dev/tpm0','/dev/tpmrm0'):
        parser.error('Only the first real TPM device is supported')
    if not stat.S_ISCHR(os.stat(args.device).st_mode):
        parser.error('Expected TPM character device')
    version=Path('/sys/class/tpm/tpm0/tpm_version_major').read_text().strip()
    if version!='2':
        raise RuntimeError('Expected TPM 2.0')
    fd=os.open(args.device,os.O_RDWR|os.O_CLOEXEC)
    try:
        data,n,more,elapsed=capability(fd,6,0x100,32)
        if len(data)!=n*8:
            raise RuntimeError('Invalid TPM property array')
        props=dict(struct.unpack_from('>II',data,i*8) for i in range(n))
        def string_property(key):
            return props.get(key,0).to_bytes(4,'big').decode('ascii',errors='replace').rstrip('\0')
        report={'kernel':os.uname().release,'device':args.device,'tpm_version':version,
                'family':string_property(0x100),'manufacturer':string_property(0x105),
                'vendor':''.join(string_property(k) for k in range(0x106,0x10a)),
                'firmware_version_1':hex(props.get(0x10b,0)),
                'firmware_version_2':hex(props.get(0x10c,0)),
                'property_query_seconds':elapsed,'properties':{hex(k):v for k,v in props.items()}}
        data,n,_,_=capability(fd,5,0,1)
        banks=[];offset=0
        for _ in range(n):
            if offset+3>len(data):raise RuntimeError('Truncated PCR selection')
            alg,length=struct.unpack_from('>HB',data,offset);offset+=3
            if not 1<=length<=8 or offset+length>len(data):raise RuntimeError('Invalid PCR selection')
            selection=data[offset:offset+length];offset+=length
            banks.append({'algorithm':alg,'pcrs':[i for i in range(length*8) if selection[i//8]&(1<<(i%8))]})
        if offset!=len(data):raise RuntimeError('Trailing PCR capability bytes')
        report['pcr_banks']=banks
        chosen=select_pcr_bank(banks)
        indices=[i for i in PCR_INDICES if i in chosen['pcrs']]
        report['pcr_read']=read_pcrs(fd,chosen['algorithm'],indices)
        lengths=[]
        for _ in range(2):
            data,_=command(fd,GET_RANDOM,struct.pack('>H',16))
            if len(data)<2:raise RuntimeError('Short random response')
            length=struct.unpack_from('>H',data)[0]
            if not 0<length<=16 or len(data)!=length+2:raise RuntimeError('Invalid random response')
            lengths.append(length)
        report['get_random_bytes_returned']=lengths
        report['random_bytes_logged']=False
        report['provisioning_commands_sent']=False
        print(json.dumps(report,indent=2))
    finally:
        os.close(fd)


if __name__=='__main__':main()
