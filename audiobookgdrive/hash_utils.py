# -*- coding: utf-8 -*-
"""
hash_utils.py
=============

Change-detection helpers (requirement 5: "Hash, filesize, modified time" -
"do not re-upload a file that has not changed"). The digest algorithm is
the same streaming SHA-256 approach BookFusion's ``upload_worker.py``
uses for its file digest, extracted here so it can be shared by both the
upload path and the cheap pre-check (size+mtime) that avoids hashing
files that obviously have not changed.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from hashlib import sha256
from os import path, stat


def file_digest(file_path, block_size=65536):
    h = sha256()
    h.update(bytes(path.getsize(file_path)))
    h.update(b'\0')
    with open(file_path, 'rb') as f:
        block = f.read(block_size)
        while len(block) > 0:
            h.update(block)
            block = f.read(block_size)
    return h.hexdigest()


def file_fingerprint(file_path):
    """Cheap fingerprint (size + mtime) used to decide whether it is even
    worth recomputing the (more expensive) SHA-256 digest."""
    st = stat(file_path)
    return {'size': st.st_size, 'mtime': int(st.st_mtime)}


def metadata_digest(text_parts):
    """SHA-256 over an ordered list of metadata strings, used to detect
    metadata-only changes (same approach as BookFusion's
    ``get_metadata_digest``)."""
    h = sha256()
    for part in text_parts:
        if part is None:
            continue
        h.update(str(part).encode('utf-8'))
        h.update(b'\x1f')
    return h.hexdigest()


def unchanged(cached_entry, file_path):
    """Return True if ``cached_entry`` (a dict previously stored in the
    state store) already matches ``file_path`` without needing a fresh
    hash: same size and mtime is treated as unchanged (matches
    requirement 5's "hash, filesize, modified time" trio) -- an SHA-256
    digest is only recomputed by the caller when this quick check fails
    or is inconclusive."""
    if not cached_entry:
        return False
    try:
        fp = file_fingerprint(file_path)
    except OSError:
        return False
    return (
        cached_entry.get('size') == fp['size']
        and cached_entry.get('mtime') == fp['mtime']
    )
