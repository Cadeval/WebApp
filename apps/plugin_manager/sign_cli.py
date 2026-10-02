#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["cryptography>=46"]
# ///
"""Local package signer. Private keys and passphrases are never sent to a server."""
import argparse
import base64
import getpass
import hashlib
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import zlib
from zipfile import ZipFile, ZIP_DEFLATED, is_zipfile, BadZipFile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAX_UPLOAD=2*1024*1024
MAX_TAR=12*1024*1024


def decode(value,length):
    data=base64.b64decode(value,validate=True)
    if len(data)!=length: raise ValueError('Invalid key file encoding.')
    return data


def load_private_key(path,passphrase):
    raw=Path(path).read_bytes()
    if len(raw)>16384: raise ValueError('Key file is too large.')
    key=json.loads(raw)
    if key.get('format')!='cadevil-signing-key-v1' or key.get('algorithm')!='Ed25519': raise ValueError('Unsupported key file.')
    encryption=key['encryption']
    if encryption.get('algorithm')!='AES-256-GCM' or encryption.get('kdf')!='PBKDF2-SHA256' or encryption.get('iterations')!=600000: raise ValueError('Unsupported key encryption parameters.')
    wrapping=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=decode(encryption['salt'],16),iterations=600000).derive(passphrase.encode('utf-8'))
    try: private_bytes=AESGCM(wrapping).decrypt(decode(encryption['iv'],12),decode(key['encrypted_private_key'],64),None)
    except InvalidTag as error: raise ValueError('The passphrase is incorrect or the encrypted key file was changed.') from error
    private=serialization.load_der_private_key(private_bytes,password=None)
    if not isinstance(private,Ed25519PrivateKey): raise ValueError('The key must be Ed25519.')
    public=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    if public!=decode(key['public_key'],32) or hashlib.sha256(public).hexdigest()!=key['key_id']: raise ValueError('The private key, public key and key id do not match.')
    return private,key['key_id']


def safe_name(name):
    if not name or len(name)>200 or name.startswith('/') or any(c in name for c in '\\%:') or any(ord(c)<32 for c in name) or any(part in {'','.', '..'} for part in name.split('/')): raise ValueError(f'Unsafe archive filename: {name!r}')


def read_package(path):
    content=Path(path).read_bytes()
    if len(content)>MAX_UPLOAD: raise ValueError('Package exceeds the 2 MiB upload size limit.')
    files={};seen=set();total=0;count=0
    def add(name,data):
        nonlocal total,count
        safe_name(name);count+=1
        if count>32 or name.casefold() in seen: raise ValueError('Too many files or duplicate filenames in package.')
        seen.add(name.casefold());total+=len(data)
        if len(data)>MAX_UPLOAD or total>8*1024*1024: raise ValueError('Package exceeds expanded size limits.')
        if name!='signature.json' and name!='plugin.json' and PurePosixPath(name).suffix.lower() not in {'.js','.mjs','.wasm','.txt','.md'}: raise ValueError('Only browser plugin files and text documentation are allowed.')
        files[name]=data
    if is_zipfile(io.BytesIO(content)):
        with ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist())>32: raise ValueError('Too many archive entries.')
            for member in archive.infolist():
                mode=member.external_attr>>16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))): raise ValueError('Archive links/devices are not allowed.')
                if member.is_dir():safe_name(member.filename.rstrip('/'));continue
                if member.file_size>MAX_UPLOAD or member.flag_bits & 1: raise ValueError('Oversized or encrypted archive member.')
                add(member.filename,archive.read(member))
    else:
        raw=content
        if raw.startswith(b'\x1f\x8b'):
            decoder=zlib.decompressobj(16+zlib.MAX_WBITS);raw=decoder.decompress(raw,MAX_TAR+1)
            if not decoder.eof or decoder.unused_data: raise ValueError('Invalid gzip archive or excessive expanded size.')
        elif raw.startswith(b'\xfd7zXZ\x00'):
            decoder=lzma.LZMADecompressor(memlimit=64*1024*1024);raw=decoder.decompress(raw,max_length=MAX_TAR+1)
            if not decoder.eof or decoder.unused_data: raise ValueError('Invalid xz archive or excessive expanded size.')
        if len(raw)>MAX_TAR: raise ValueError('TAR exceeds expanded size limits.')
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
            for index,member in enumerate(archive):
                if index>=32:raise ValueError('Too many TAR entries.')
                if member.isdir():safe_name(member.name.rstrip('/'));continue
                if not member.isfile() or member.issparse() or member.size>MAX_UPLOAD:raise ValueError('TAR contains a link, sparse or oversized file.')
                add(member.name,archive.extractfile(member).read(MAX_UPLOAD+1))
    if 'plugin.json' not in files: raise ValueError('A root plugin.json is required.')
    manifest=json.loads(files['plugin.json'].decode('utf-8'))
    if not isinstance(manifest,dict) or manifest.get('type') not in {'javascript','wasm'} or manifest.get('entrypoint') not in files: raise ValueError('Invalid browser plugin manifest.')
    files.pop('signature.json',None)
    return files


def sign_package(package_path,key_path,output,passphrase):
    source=Path(package_path);destination=Path(output)
    if source.resolve()==destination.resolve() or destination.exists(): raise ValueError('Choose a new output path. The CLI never overwrites an input or an existing file.')
    private,key_id=load_private_key(key_path,passphrase)
    files=read_package(source)
    payload=json.dumps({'context':'cadevil-plugin-package-v1','files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}},sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
    signature={'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':key_id,'signature':base64.b64encode(private.sign(payload)).decode('ascii')}
    files['signature.json']=json.dumps(signature,sort_keys=True,indent=2).encode('ascii')
    memory=io.BytesIO();name=destination.name.lower()
    if name.endswith('.zip'):
        with ZipFile(memory,'w',compression=ZIP_DEFLATED) as archive:
            for member,data in files.items():archive.writestr(member,data)
    elif name.endswith(('.tar','.tar.gz','.tgz','.tar.xz','.txz')):
        mode='w:gz' if name.endswith(('.tar.gz','.tgz')) else ('w:xz' if name.endswith(('.tar.xz','.txz')) else 'w:')
        with tarfile.open(fileobj=memory,mode=mode) as archive:
            for member,data in files.items():
                entry=tarfile.TarInfo(member);entry.size=len(data);entry.mode=0o644
                archive.addfile(entry,io.BytesIO(data))
    else:raise ValueError('Output must be .zip, .tar, .tar.gz/.tgz or .tar.xz/.txz. OpenZL is not supported by this CLI.')
    content=memory.getvalue()
    if len(content)>MAX_UPLOAD: raise ValueError('Signed package exceeds the 2 MiB upload limit.')
    with destination.open('xb') as file:file.write(content)
    return key_id


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sign=sub.add_parser('sign',help='Sign every file in a browser plugin package')
    sign.add_argument('package');sign.add_argument('--key',required=True);sign.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        fingerprint=sign_package(args.package,args.key,args.output,getpass.getpass('Key encryption passphrase: '))
        print(f'Signed {args.output}\nKey id: {fingerprint}')
    except (ValueError,KeyError,OSError,EOFError,TypeError,tarfile.TarError,BadZipFile,lzma.LZMAError,zlib.error) as error:
        parser.exit(1,f'Cannot sign package: {error}\n')

if __name__=='__main__':main()
