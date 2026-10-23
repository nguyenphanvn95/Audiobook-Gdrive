# -*- coding: utf-8 -*-
"""
audiobook_sync/worker.py
==========================

``QThread`` wrapper quanh ``uploader.run_upload_sync`` / ``checker.run_check``.
Hỗ trợ ``library_name`` để đồng bộ/kiểm tra đúng thư viện trên Drive.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import QThread, pyqtSignal

from . import checker, uploader


class AudiobookSyncThread(QThread):
    progress = pyqtSignal(int, int)  # done, total (số cuốn sách)
    log = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)  # stats
    failed = pyqtSignal(str)

    def __init__(self, access_token, root_folders, parent=None, logger=None, library_name=None,
                 book_folders=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.root_folders = root_folders
        self.book_folders = book_folders or []
        self.logger = logger
        self.library_name = library_name
        self._abort = False

    def request_abort(self):
        self._abort = True

    def _check_abort(self):
        return self._abort

    def _log_line(self, level, msg):
        self.log.emit('[%s] %s' % (level, msg))
        if self.logger is not None:
            if level == 'ERROR':
                self.logger.error('[audiobook_sync] %s' % msg)
            elif level == 'WARN':
                self.logger.warn('[audiobook_sync] %s' % msg)
            else:
                self.logger.info('[audiobook_sync] %s' % msg)

    def run(self):
        try:
            stats = uploader.run_upload_sync(
                self.access_token, self.root_folders,
                log_fn=self._log_line,
                progress_fn=lambda done, total: self.progress.emit(done, total),
                check_abort=self._check_abort,
                library_name=self.library_name,
                book_paths=self.book_folders,
            )
            self.finished_ok.emit(stats)
        except uploader.Cancelled:
            self.failed.emit('Đã huỷ.')
        except Exception as e:
            if self.logger is not None:
                self.logger.error('[audiobook_sync] Audiobook Sync thất bại: %s' % e)
            self.failed.emit(str(e))


class AudiobookCheckThread(QThread):
    """``QThread`` wrapper quanh ``checker.run_check``."""

    progress = pyqtSignal(int, int)
    scan_progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, access_token, parent=None, logger=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.logger = logger
        self.library_name = library_name
        self._abort = False

    def request_abort(self):
        self._abort = True

    def _check_abort(self):
        return self._abort

    def _log_line(self, level, msg):
        self.log.emit('[%s] %s' % (level, msg))
        if self.logger is not None:
            if level == 'ERROR':
                self.logger.error('[audiobook_check] %s' % msg)
            elif level == 'WARN':
                self.logger.warn('[audiobook_check] %s' % msg)
            else:
                self.logger.info('[audiobook_check] %s' % msg)

    def run(self):
        try:
            stats = checker.run_check(
                self.access_token,
                log_fn=self._log_line,
                progress_fn=lambda done, total: self.progress.emit(done, total),
                check_abort=self._check_abort,
                scan_progress_fn=lambda done, total, name: self.scan_progress.emit(done, total, name),
                library_name=self.library_name,
            )
            self.finished_ok.emit(stats)
        except checker.CheckCancelled:
            self.failed.emit('Đã huỷ.')
        except Exception as e:
            if self.logger is not None:
                self.logger.error('[audiobook_check] Kiểm tra Audiobooks thất bại: %s' % e)
            self.failed.emit(str(e))
