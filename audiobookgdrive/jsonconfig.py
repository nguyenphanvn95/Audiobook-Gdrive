# -*- coding: utf-8 -*-
"""
jsonconfig.py
=============

Standalone thay thế cho ``calibre.utils.config.JSONConfig`` (vốn plugin
gốc dùng để lưu prefs/token/state vào thư mục cấu hình của Calibre).
Ứng dụng độc lập này không chạy trong Calibre nên không có sẵn
``config_dir`` của Calibre -- module này tự tạo 1 thư mục cấu hình
riêng cho AudiobookGdrive (theo chuẩn từng hệ điều hành) và cung cấp 1
lớp ``JSONConfig`` có API tương thích (dict-like, có ``.defaults``,
ghi đĩa ngay khi set 1 khoá) để mọi file gốc (``oauth.py``,
``state_store.py``, ...) chỉ cần đổi 1 dòng import là chạy được y hệt.
"""

from __future__ import print_function

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

import json
import os
import sys
from threading import RLock


def _default_config_dir():
    """Thư mục cấu hình gốc, giống tinh thần ``calibre.utils.config.config_dir``:
    Windows -> %APPDATA%\\AudiobookGdrive
    macOS   -> ~/Library/Application Support/AudiobookGdrive
    Linux   -> ~/.config/AudiobookGdrive (tôn trọng $XDG_CONFIG_HOME)
    Có thể ghi đè bằng biến môi trường AUDIOBOOKGDRIVE_CONFIG_DIR (hữu
    ích khi đóng gói .exe / chạy portable).
    """
    override = os.environ.get('AUDIOBOOKGDRIVE_CONFIG_DIR')
    if override:
        return override
    if sys.platform.startswith('win'):
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'AudiobookGdrive')
    if sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'AudiobookGdrive')
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'AudiobookGdrive')


config_dir = _default_config_dir()

_lock = RLock()


class JSONConfig(dict):
    """Dict phẳng, tự lưu vào ``<config_dir>/<rel_path>.json`` ngay khi 1
    khoá được set qua ``__setitem__`` (khớp cách các module gốc dùng nó,
    kiểu ``prefs['x'] = y`` rồi đọc lại ngay ``prefs['x']``). ``defaults``
    là dict fallback dùng khi khoá chưa từng được set."""

    def __init__(self, rel_path):
        dict.__init__(self)
        self.defaults = {}
        self._path = os.path.join(config_dir, rel_path.replace('/', os.sep) + '.json')
        self._defer_save = 0  # >0: gom nhiều lần set, chỉ ghi đĩa khi end_batch()
        self._load()

    def begin_batch(self):
        """Tạm ngưng ghi đĩa -- dùng khi import hàng loạt (tránh ghi N nghìn lần)."""
        self._defer_save += 1

    def end_batch(self):
        if self._defer_save > 0:
            self._defer_save -= 1
        if self._defer_save == 0:
            self._save()

    def _load(self):
        with _lock:
            try:
                with open(self._path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    dict.update(self, data)
            except (OSError, ValueError):
                pass

    def _save(self):
        with _lock:
            d = os.path.dirname(self._path)
            try:
                if d and not os.path.isdir(d):
                    os.makedirs(d)
                tmp_path = self._path + '.tmp'
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(dict(self), f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self._path)
            except OSError:
                pass  # không bao giờ để lỗi ghi đĩa làm crash ứng dụng

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.defaults[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        if not getattr(self, '_defer_save', 0):
            self._save()

    def __delitem__(self, key):
        try:
            dict.__delitem__(self, key)
        except KeyError:
            pass
        if not getattr(self, '_defer_save', 0):
            self._save()
