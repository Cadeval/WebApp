"""Bounded archive readers; never extract uploaded files to the filesystem."""
import io
import lzma
import tarfile
import zlib
from zipfile import ZipFile, is_zipfile
from django.core.exceptions import ValidationError

MAX_RAW_TAR=12*1024*1024


def normalize_archive(content):
    """Normalize TAR containers to ZIP; signatures bind file bytes, not compression."""
    if is_zipfile(io.BytesIO(content)): return content,'zip'
    kind='tar'
    try:
        raw=content
        if content.startswith(b'\x1f\x8b'):
            kind='tar.gz';decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            raw=decoder.decompress(content,MAX_RAW_TAR+1)
            if len(raw)>MAX_RAW_TAR or not decoder.eof or decoder.unused_data:
                raise ValidationError('The gzip archive is truncated, concatenated, or exceeds its expanded size limit.')
        elif content.startswith(b'\xfd7zXZ\x00'):
            kind='tar.xz';decoder=lzma.LZMADecompressor(memlimit=64*1024*1024)
            raw=decoder.decompress(content,max_length=MAX_RAW_TAR+1)
            if len(raw)>MAX_RAW_TAR or not decoder.eof or decoder.unused_data:
                raise ValidationError('The xz archive is truncated, concatenated, or exceeds its expanded size limit.')
        if len(raw)>MAX_RAW_TAR: raise ValidationError('TAR data exceeds its expanded size limit.')
        result=io.BytesIO();total=0;count=0;seen=set()
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive,ZipFile(result,'w') as target:
            for member in archive:
                count+=1
                if count>32: raise ValidationError('Packages may contain at most 32 archive entries.')
                # Imported lazily to share precisely the same path rules as ZIP validation.
                from .packages import safe_path
                safe_path(member.name.rstrip('/') if member.isdir() else member.name)
                if member.name.casefold() in seen: raise ValidationError('Duplicate archive filenames are not allowed.')
                seen.add(member.name.casefold())
                if member.isdir(): continue
                if not member.isfile() or member.issparse(): raise ValidationError('TAR packages must use regular files; links and sparse files are not supported.')
                total+=member.size
                if member.size>2*1024*1024 or total>8*1024*1024: raise ValidationError('Package contents exceed the expanded size limit.')
                source=archive.extractfile(member)
                if source is None: raise ValidationError('Package member is unreadable.')
                target.writestr(member.name,source.read(2*1024*1024+1))
        return result.getvalue(),kind
    except (tarfile.TarError,zlib.error,lzma.LZMAError,EOFError,OSError,ValueError) as error:
        raise ValidationError('Unsupported or corrupt archive. Use ZIP, TAR, tar.gz, or tar.xz. OpenZL decoding is not installed.') from error
