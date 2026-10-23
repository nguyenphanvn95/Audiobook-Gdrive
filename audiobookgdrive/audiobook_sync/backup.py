# -*- coding: utf-8 -*-
"""
audiobook_sync/backup.py
==========================

Bản backup offline (local) của ``metadata_public.json`` -- hỗ trợ nhiều
thư viện: mỗi library name có file backup riêng
(``metadata_public_<sanitized_name>.json``). Tên mặc định ``Audiobooks``
vẫn dùng ``metadata_public.json`` để tương thích ngược.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import os
import re

BACKUP_SUBDIR = 'gdrive_sync_audiobook_backup'
BACKUP_FILE_NAME = 'metadata_public.json'
DEFAULT_LIBRARY_NAME = 'Audiobooks'


def backup_dir():
    from audiobookgdrive.jsonconfig import config_dir
    d = os.path.join(config_dir, 'plugins', BACKUP_SUBDIR)
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except OSError:
            pass
    return d


def _safe_name(library_name):
    name = (library_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    if name == DEFAULT_LIBRARY_NAME:
        return BACKUP_FILE_NAME
    safe = re.sub(r'[^\w\-.]+', '_', name, flags=re.UNICODE).strip('._') or 'lib'
    return 'metadata_public_%s.json' % safe


def backup_path(library_name=None):
    return os.path.join(backup_dir(), _safe_name(library_name))


def save_backup(data_bytes, log_fn=None, library_name=None):
    out_path = backup_path(library_name)
    try:
        if os.path.exists(out_path):
            try:
                os.replace(out_path, out_path + '.bak')
            except OSError:
                pass
        tmp_path = out_path + '.tmp'
        with open(tmp_path, 'wb') as f:
            f.write(data_bytes)
        os.replace(tmp_path, out_path)
    except OSError as e:
        if log_fn:
            log_fn('WARN', 'Không ghi được bản backup offline %s: %s' % (os.path.basename(out_path), e))


def load_backup(library_name=None):
    out_path = backup_path(library_name)
    if not os.path.exists(out_path):
        return None, None
    try:
        with open(out_path, 'rb') as f:
            return f.read(), out_path
    except OSError:
        return None, None
