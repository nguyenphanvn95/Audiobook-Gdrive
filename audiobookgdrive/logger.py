# -*- coding: utf-8 -*-
"""
logger.py
=========

File logger reused from BookFusion's ``logger.py`` (same minimal design:
timestamped lines appended to a log file inside the library folder, gated
by the ``debug`` preference). Extended with log levels so the "Logs"
toolbar action / dialog can show upload/delete/retry/OAuth/queue/thread
/API events with a severity tag, per requirement 14.
"""

from __future__ import print_function

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from datetime import datetime
from threading import Lock

from os import path as _ospath

from audiobookgdrive.config import prefs
from audiobookgdrive.jsonconfig import config_dir

# Đường dẫn log cố định (không phụ thuộc "thư viện" như bản plugin gốc,
# vì ứng dụng độc lập này không có khái niệm thư viện Calibre).
LOG_PATH = _ospath.join(config_dir, 'audiobookgdrive.log')

LEVEL_INFO = 'INFO'
LEVEL_WARN = 'WARN'
LEVEL_ERROR = 'ERROR'
LEVEL_DEBUG = 'DEBUG'


class Logger:
    """Thread-safe append-only logger.

    Multiple sync worker threads log concurrently, so writes are
    serialized with a lock (BookFusion's original logger assumed a single
    network-reply-driven thread and did not need this).
    """

    def __init__(self, path, max_bytes=5 * 1024 * 1024):
        self.path = path
        self.max_bytes = max_bytes
        self._lock = Lock()
        self._ring = []
        self._ring_limit = 2000

    def _write(self, level, msg):
        if not prefs['debug'] and level == LEVEL_DEBUG:
            return
        line = '%s [%s] %s' % (datetime.now().isoformat(sep=' ', timespec='seconds'), level, msg)
        with self._lock:
            self._ring.append(line)
            if len(self._ring) > self._ring_limit:
                self._ring.pop(0)
            try:
                self._rotate_if_needed()
                with open(self.path, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except OSError:
                # Logging must never crash a sync job.
                pass

    def _rotate_if_needed(self):
        try:
            from os import path as ospath, replace
            if ospath.exists(self.path) and ospath.getsize(self.path) > self.max_bytes:
                replace(self.path, self.path + '.1')
        except OSError:
            pass

    def info(self, msg):
        self._write(LEVEL_INFO, msg)

    def warn(self, msg):
        self._write(LEVEL_WARN, msg)

    def error(self, msg):
        self._write(LEVEL_ERROR, msg)

    def debug(self, msg):
        self._write(LEVEL_DEBUG, msg)

    def recent(self, n=500):
        """Return the last ``n`` log lines kept in memory (for the Logs dialog)."""
        with self._lock:
            return list(self._ring[-n:])


_default_logger = None


def get_logger():
    """Trả về 1 instance :class:`Logger` dùng chung cho cả ứng dụng, ghi
    vào ``LOG_PATH`` -- tạo lười (lazy) ở lần gọi đầu tiên."""
    global _default_logger
    if _default_logger is None:
        d = _ospath.dirname(LOG_PATH)
        try:
            if d and not _ospath.isdir(d):
                import os
                os.makedirs(d)
        except OSError:
            pass
        _default_logger = Logger(LOG_PATH)
    return _default_logger
