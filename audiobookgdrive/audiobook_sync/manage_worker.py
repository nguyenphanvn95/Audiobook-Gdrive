# -*- coding: utf-8 -*-
"""
audiobook_sync/manage_worker.py
=================================

``QThread`` wrappers quanh ``manage_ops.py`` -- hỗ trợ library_name.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import QThread, pyqtSignal

from . import manage_ops


class ListAudiobooksThread(QThread):
    finished_ok = pyqtSignal(str, list, str)  # root_id, list[ManagedAudiobook], source
    failed = pyqtSignal(str)

    def __init__(self, access_token, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.library_name = library_name

    def run(self):
        try:
            root_id, books, source = manage_ops.list_audiobooks(
                self.access_token, library_name=self.library_name)
            self.finished_ok.emit(root_id, books, source or '')
        except Exception as e:
            self.failed.emit(str(e))


class CoverLoaderThread(QThread):
    cover_loaded = pyqtSignal(str, str)  # key, local_cache_path
    finished_ok = pyqtSignal()

    def __init__(self, access_token, books, parent=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.books = list(books)

    def run(self):
        # 1) Hiện ngay mọi cover đã cache (không mạng)
        need_download = []
        for book in self.books:
            if self.isInterruptionRequested():
                return
            if not book.cover_file_id:
                continue
            hit = manage_ops.peek_cached_cover_path(book.cover_file_id, key=book.key)
            if hit:
                self.cover_loaded.emit(book.key, hit)
            else:
                need_download.append(book)
        # 2) Chỉ tải những bìa chưa có trong cache
        for book in need_download:
            if self.isInterruptionRequested():
                return
            try:
                path = manage_ops.cached_cover_path(
                    self.access_token, book.key, book.cover_file_id)
            except Exception:
                path = None
            if path:
                self.cover_loaded.emit(book.key, path)
        self.finished_ok.emit()


class EditMetadataThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, key, title=None, creators_text=None,
                 publisher=None, language=None, description=None, parent=None,
                 library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.key = key
        self.title = title
        self.creators_text = creators_text
        self.publisher = publisher
        self.language = language
        self.description = description
        self.library_name = library_name

    def run(self):
        try:
            manage_ops.edit_metadata(
                self.access_token, self.key,
                title=self.title, creators_text=self.creators_text,
                publisher=self.publisher, language=self.language,
                description=self.description,
                log_fn=lambda msg: self.log.emit(msg),
                library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class ResyncBookThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, key, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.key = key
        self.library_name = library_name

    def run(self):
        try:
            manage_ops.resync_book_from_drive(
                self.access_token, self.key,
                log_fn=lambda msg: self.log.emit(msg),
                library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class DeleteAudiobooksThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, keys, purge_files, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.keys = list(keys)
        self.purge_files = purge_files
        self.library_name = library_name

    def run(self):
        try:
            manage_ops.delete_audiobooks(
                self.access_token, self.keys, self.purge_files,
                log_fn=lambda msg: self.log.emit(msg),
                library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class MoveAudiobookThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, key, target_library_name, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.key = key
        self.target_library_name = target_library_name
        self.library_name = library_name

    def run(self):
        try:
            manage_ops.move_audiobook_to_library(
                self.access_token, self.key, self.target_library_name,
                log_fn=lambda msg: self.log.emit(msg),
                library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class MoveAudiobooksThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, keys, target_library_name, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.keys = list(keys or [])
        self.target_library_name = target_library_name
        self.library_name = library_name

    def run(self):
        try:
            total = len(self.keys)
            for i, key in enumerate(self.keys, 1):
                self.log.emit('Đang di chuyển audiobook %d/%d...' % (i, total))
                manage_ops.move_audiobook_to_library(
                    self.access_token, key, self.target_library_name,
                    log_fn=lambda msg: self.log.emit(msg),
                    library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class RenameLibraryThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, access_token, old_library_name, new_library_name, parent=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.old_library_name = old_library_name
        self.new_library_name = new_library_name

    def run(self):
        try:
            name = manage_ops.rename_library_folder(
                self.access_token, self.old_library_name, self.new_library_name,
                log_fn=lambda msg: self.log.emit(msg))
            self.finished_ok.emit(name)
        except Exception as e:
            self.failed.emit(str(e))


class ReplaceCoverThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, key, local_image_path, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.key = key
        self.local_image_path = local_image_path
        self.library_name = library_name

    def run(self):
        try:
            manage_ops.replace_cover_image(
                self.access_token, self.key, self.local_image_path,
                log_fn=lambda msg: self.log.emit(msg),
                library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class ReplaceOpfThread(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, access_token, key, local_opf_path, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.key = key
        self.local_opf_path = local_opf_path
        self.library_name = library_name

    def run(self):
        try:
            manage_ops.replace_metadata_opf(
                self.access_token, self.key, self.local_opf_path,
                log_fn=lambda msg: self.log.emit(msg),
                library_name=self.library_name)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


class DownloadAudiobookThread(QThread):
    progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, access_token, key, target_parent_dir, parent=None, library_name=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.key = key
        self.target_parent_dir = target_parent_dir
        self.library_name = library_name
        self._abort = False

    def request_abort(self):
        self._abort = True

    def _check_abort(self):
        return self._abort

    def run(self):
        try:
            local_dir = manage_ops.download_audiobook_folder(
                self.access_token, self.key, self.target_parent_dir,
                log_fn=lambda msg: self.log.emit(msg),
                progress_fn=lambda done, total, name: self.progress.emit(done, total, name),
                check_abort=self._check_abort,
                library_name=self.library_name)
            self.finished_ok.emit(local_dir)
        except Exception as e:
            self.failed.emit(str(e))
