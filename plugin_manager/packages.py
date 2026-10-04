"""Validate browser-plugin archives without extracting or executing their contents."""
import hashlib
import io
import json
import re
import stat
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError
from .manifest import is_api_version_compatible

ID_PATTERN=re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
MAX_FILES=32
MAX_EXPANDED_BYTES=8*1024*1024
MAX_MEMBER_BYTES=2*1024*1024


def safe_path(name):
    if not isinstance(name,str) or not name or len(name)>200 or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',name):
        raise ValidationError('Package filenames must use ASCII letters, numbers, dots, dashes or underscores in relative paths.')
    path=PurePosixPath(name)
    if path.is_absolute() or any(part in {'','.', '..'} for part in name.split('/')):
        raise ValidationError('Package files may not use absolute paths or parent traversal.')
    return name


def javascript(content):
    try: source=content.decode('utf-8')
    except UnicodeDecodeError as error: raise ValidationError('JavaScript package files must be UTF-8 text.') from error
    if not source.strip() or '\x00' in source or source.lstrip().lower().startswith(('<!doctype','<html','<script')):
        raise ValidationError('Package JavaScript must be a worker module, not HTML or empty text.')


def validate_package(content):
    from .archives import normalize_archive
    content,archive_format=normalize_archive(content)
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            members=archive.infolist()
            if not members or len(members)>MAX_FILES:
                raise ValidationError(f'Packages must contain between 1 and {MAX_FILES} entries.')
            files={};seen=set();total=0
            for member in members:
                name=member.filename
                mode=member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    raise ValidationError('Package entries must be regular files or directories, not links or devices.')
                if member.is_dir():
                    safe_path(name[:-1])
                    continue
                safe_path(name)
                if name.casefold() in seen: raise ValidationError('Duplicate package filenames are not allowed.')
                seen.add(name.casefold())
                mode=member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
                    raise ValidationError('Package entries must be regular files, not symbolic links or device files.')
                if member.flag_bits & 1: raise ValidationError('Encrypted ZIP files are not supported.')
                total+=member.file_size
                if member.file_size>MAX_MEMBER_BYTES or total>MAX_EXPANDED_BYTES:
                    raise ValidationError('Package contents exceed the permitted expanded size (8 MiB total, 2 MiB per file).')
                if member.file_size>max(1024*1024, member.compress_size*200):
                    raise ValidationError('Package compression ratio is too high.')
                suffix=PurePosixPath(name).suffix.lower()
                if name in {'plugin.json','signature.json'}:
                    if member.file_size>16384: raise ValidationError('plugin.json must be at most 16 KiB.')
                elif name == 'sbom.cdx.json':
                    from .sbom import MAX_SBOM_BYTES
                    if member.file_size > MAX_SBOM_BYTES:
                        raise ValidationError('The plugin SBOM must be at most 256 KiB.')
                elif suffix not in {'.js','.mjs','.wasm','.md','.txt'}:
                    raise ValidationError('Packages may contain only plugin.json, sbom.cdx.json, JavaScript, WASM and text documentation. Server Python code is not installed from uploads.')
                data=archive.read(member)
                if suffix in {'.js','.mjs'}: javascript(data)
                elif suffix=='.wasm' and not data.startswith(b'\x00asm\x01\x00\x00\x00'):
                    raise ValidationError(f'{name} is not a WebAssembly version 1 module.')
                files[name]=data
            if 'sbom.cdx.json' in files:
                from .sbom import parse_inventory
                parse_inventory(files['sbom.cdx.json'])
            if 'plugin.json' not in files: raise ValidationError('The ZIP must contain plugin.json at its root.')
            try: manifest=json.loads(files['plugin.json'].decode('utf-8'))
            except (ValueError,UnicodeDecodeError) as error: raise ValidationError('plugin.json must contain valid UTF-8 JSON.') from error
            if not isinstance(manifest,dict): raise ValidationError('plugin.json must be a JSON object.')
            allowed={'id','name','version','api_version','description','type','entrypoint','compatibility'}
            if set(manifest)-allowed: raise ValidationError('plugin.json contains unsupported fields.')
            for field,limit in [('id',180),('name',255),('version',50),('api_version',20),('description',2000),('type',16),('entrypoint',200)]:
                value=manifest.get(field, '' if field=='description' else None)
                if not isinstance(value,str) or len(value)>limit or (field!='description' and not value.strip()):
                    raise ValidationError(f'plugin.json requires a valid {field}.')
            if not ID_PATTERN.fullmatch(manifest['id']): raise ValidationError('The package id must use lowercase letters and numbers separated by dots, dashes or underscores.')
            if not is_api_version_compatible(manifest['api_version']): raise ValidationError('This package API version is incompatible with the host.')
            compatibility = manifest.get('compatibility', 'both')
            if not isinstance(compatibility, str) or compatibility not in {'debug','production','both'}:
                raise ValidationError('Package compatibility must be debug, production or both.')
            kind=manifest['type'];entry=safe_path(manifest['entrypoint'])
            if kind not in {'javascript','wasm'}: raise ValidationError('Package type must be javascript or wasm.')
            suffix=PurePosixPath(entry).suffix.lower()
            if entry not in files or (kind=='javascript' and suffix not in {'.js','.mjs'}) or (kind=='wasm' and suffix!='.wasm'):
                raise ValidationError('The package entrypoint must reference an included file of the declared type.')
            signature=None
            if 'signature.json' in files:
                try: signature=json.loads(files['signature.json'].decode('utf-8'))
                except (ValueError,UnicodeDecodeError) as error: raise ValidationError('signature.json must be valid UTF-8 JSON.') from error
            return {**manifest,'description':manifest.get('description',''),'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items() if name!='signature.json'},'signature':signature}
    except (BadZipFile,RuntimeError,NotImplementedError,EOFError,OSError) as error:
        raise ValidationError('The ZIP is corrupt or uses an unsupported archive format.') from error
