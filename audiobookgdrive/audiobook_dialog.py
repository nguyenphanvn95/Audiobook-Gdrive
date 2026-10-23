# -*- coding: utf-8 -*-
"""
audiobook_dialog.py
=====================

Upload dialog for "Đồng bộ Audiobooks lên Google Drive".

The dialog now includes the setup screen directly, similar to the Chrome
extension: choose/create the Drive library folder, choose a source mode
(one selected book folder or batch parent folders), then start upload and
watch progress/logs in the same window.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import os

from PyQt5.Qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPlainTextEdit,
    QDialogButtonBox, QComboBox, QPushButton, QFileDialog, QListWidget,
    QMessageBox, QInputDialog, QThread, pyqtSignal, QGroupBox, QCheckBox,
    QApplication,
)

from audiobookgdrive import drive_api
from audiobookgdrive.config import (
    DEFAULT_LIBRARY_NAME, get_nonempty_library_names, prefs,
    update_library_cache_from_folders,
)
from audiobookgdrive.help_utils import maybe_show_help_from_event
from audiobookgdrive.audiobook_sync.worker import AudiobookSyncThread


MODE_SINGLE_FOLDER = 'single_folder'
MODE_BATCH = 'batch'


class DriveRootFoldersThread(QThread):
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, access_token, parent=None):
        QThread.__init__(self, parent)
        self.access_token = access_token

    def run(self):
        try:
            from audiobookgdrive.audiobook_sync.library_bind import discover_library_folders
            self.finished_ok.emit(discover_library_folders(self.access_token))
        except Exception as e:
            self.failed.emit(str(e))


class AudiobookSyncProgressDialog(QDialog):
    def __init__(self, gui, access_token, root_folders=None, logger=None, library_name=None):
        QDialog.__init__(self, gui)
        self.access_token = access_token
        self.default_root_folders = [p for p in (root_folders or []) if p]
        self.logger = logger
        self.thread = None
        self.folder_loader = None

        self.setWindowTitle('Upload Audiobook lên Google Drive')
        self.resize(720, 620)

        outer = QVBoxLayout()
        self.setLayout(outer)

        library_box = QGroupBox('1. Thư mục thư viện trên Google Drive', self)
        library_layout = QVBoxLayout(library_box)
        library_row = QHBoxLayout()
        self.library_combo = QComboBox(self)
        library_row.addWidget(self.library_combo, 1)
        self.refresh_libraries_btn = QPushButton('Tải lại', self)
        self.refresh_libraries_btn.clicked.connect(self.refresh_libraries)
        library_row.addWidget(self.refresh_libraries_btn)
        self.new_library_btn = QPushButton('Tạo mới...', self)
        self.new_library_btn.clicked.connect(self.create_library)
        library_row.addWidget(self.new_library_btn)
        library_layout.addLayout(library_row)
        self.library_status = QLabel('Chọn thư mục gốc chứa metadata_public.json, hoặc tạo thư mục mới.')
        self.library_status.setWordWrap(True)
        library_layout.addWidget(self.library_status)
        outer.addWidget(library_box)

        source_box = QGroupBox('2. Nguồn sách nói', self)
        source_layout = QVBoxLayout(source_box)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Kiểu upload:'))
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem('1 cuốn (thư mục)', MODE_SINGLE_FOLDER)
        self.mode_combo.addItem('Hàng loạt (thư mục cha)', MODE_BATCH)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        source_layout.addLayout(mode_row)

        self.source_hint = QLabel('')
        self.source_hint.setWordWrap(True)
        source_layout.addWidget(self.source_hint)

        self.sources_list = QListWidget(self)
        self.sources_list.setMinimumHeight(110)
        source_layout.addWidget(self.sources_list)

        source_btn_row = QHBoxLayout()
        self.add_source_btn = QPushButton('Chọn...', self)
        self.add_source_btn.clicked.connect(self.add_source)
        source_btn_row.addWidget(self.add_source_btn)
        self.add_settings_roots_btn = QPushButton('Dùng thư mục trong Cài đặt', self)
        self.add_settings_roots_btn.clicked.connect(self.add_settings_roots)
        source_btn_row.addWidget(self.add_settings_roots_btn)
        self.remove_source_btn = QPushButton('Xóa khỏi danh sách', self)
        self.remove_source_btn.clicked.connect(self.remove_selected_source)
        source_btn_row.addWidget(self.remove_source_btn)
        source_btn_row.addStretch()
        source_layout.addLayout(source_btn_row)

        self.skip_duplicates = QCheckBox(
            'Bỏ qua file không đổi / sách đã có cache (mặc định của bộ đồng bộ)', self)
        self.skip_duplicates.setChecked(True)
        self.skip_duplicates.setEnabled(False)
        source_layout.addWidget(self.skip_duplicates)
        outer.addWidget(source_box)

        action_row = QHBoxLayout()
        self.upload_btn = QPushButton('Upload lên Google Drive', self)
        self.upload_btn.clicked.connect(self.start_upload)
        action_row.addWidget(self.upload_btn)
        self.cancel_btn = QPushButton('Hủy upload', self)
        self.cancel_btn.clicked.connect(self.cancel_upload)
        self.cancel_btn.setEnabled(False)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch()
        outer.addLayout(action_row)

        self.status_label = QLabel('Sẵn sàng.')
        outer.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(1)
        outer.addWidget(self.progress_bar)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        outer.addWidget(self.log_view, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.close)
        outer.addWidget(self.buttons)

        self._seed_configured_libraries(library_name)
        self._on_mode_changed()
        if prefs.get('audiobook_library_cache'):
            self.library_status.setText('Đang hiện danh sách thư viện từ cache. Bấm "Tải lại" để làm mới từ Drive.')
        else:
            self.library_status.setText('Chưa có cache thư viện. Bấm "Tải lại" để tải danh sách từ Drive.')

    # -- library picker -------------------------------------------------
    def _configured_libraries(self):
        names = get_nonempty_library_names(include_configured=True)
        seen = set()
        out = []
        for name in names:
            name = (name or '').strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out or [DEFAULT_LIBRARY_NAME]

    def _seed_configured_libraries(self, active=None):
        self.library_combo.clear()
        for name in self._configured_libraries():
            self.library_combo.addItem(name)
        active = active or prefs.get('audiobook_active_library') or DEFAULT_LIBRARY_NAME
        idx = self.library_combo.findText(active)
        self.library_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def refresh_libraries(self):
        if self.folder_loader and self.folder_loader.isRunning():
            return
        self.refresh_libraries_btn.setEnabled(False)
        self.library_status.setText('Đang tải danh sách thư mục trên Google Drive...')
        self.folder_loader = DriveRootFoldersThread(self.access_token, parent=self)
        self.folder_loader.finished_ok.connect(self._on_libraries_loaded)
        self.folder_loader.failed.connect(self._on_libraries_failed)
        self.folder_loader.start()

    def _on_libraries_loaded(self, folders):
        active = self.library_combo.currentText().strip() or prefs.get('audiobook_active_library') or DEFAULT_LIBRARY_NAME
        update_library_cache_from_folders(folders)
        names = get_nonempty_library_names()
        names.sort(key=lambda n: n.lower())

        self.library_combo.clear()
        for name in names:
            self.library_combo.addItem(name)
        idx = self.library_combo.findText(active)
        self.library_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.library_status.setText('Đã tải %d thư mục audiobook hợp lệ trên Drive. Có thể chọn thư mục có sẵn hoặc tạo mới.'
                                    % len(names))
        self.refresh_libraries_btn.setEnabled(True)

    def _on_libraries_failed(self, message):
        self.library_status.setText('Không tải được danh sách Drive: %s' % message)
        self.refresh_libraries_btn.setEnabled(True)

    def create_library(self):
        name, ok = QInputDialog.getText(
            self, 'Tạo thư mục thư viện mới',
            'Tên thư mục trên Google Drive:', text=DEFAULT_LIBRARY_NAME)
        if not ok:
            return
        name = (name or '').strip()
        if not name:
            return
        if '/' in name or '\\' in name:
            QMessageBox.warning(self, 'Google Drive Sync', 'Tên thư mục không được chứa / hoặc \\.')
            return
        self.setEnabled(False)
        try:
            drive_api.find_or_create_folder(self.access_token, name, 'root')
            libs = self._configured_libraries()
            if name not in libs:
                libs.append(name)
                prefs['audiobook_library_folders'] = libs
            prefs['audiobook_active_library'] = name
            self._seed_configured_libraries(active=name)
            self.log_view.appendPlainText('[INFO] Đã tạo/tìm thư mục thư viện "%s" trên Drive.' % name)
            self.refresh_libraries()
        except Exception as e:
            QMessageBox.warning(self, 'Google Drive Sync', 'Không tạo được thư mục: %s' % e)
        finally:
            self.setEnabled(True)

    # -- source selection -----------------------------------------------
    def _on_mode_changed(self, *_args):
        mode = self.mode_combo.currentData()
        self.sources_list.clear()
        if mode == MODE_SINGLE_FOLDER:
            self.source_hint.setText(
                'Chọn đúng một thư mục sách nói trên máy. Thư mục này cần có metadata.opf; '
                'cover và các file audio nằm trực tiếp bên trong thư mục.')
            self.add_settings_roots_btn.setVisible(False)
        else:
            self.source_hint.setText(
                'Chọn một hoặc nhiều thư mục cha. Mỗi thư mục con trực tiếp có metadata.opf '
                'sẽ được coi là một cuốn và upload hàng loạt.')
            self.add_settings_roots_btn.setVisible(True)
        self._update_upload_enabled()

    def add_source(self):
        mode = self.mode_combo.currentData()
        if mode == MODE_SINGLE_FOLDER:
            d = QFileDialog.getExistingDirectory(self, 'Chọn thư mục của 1 cuốn audiobook')
            if d:
                self.sources_list.clear()
                self.sources_list.addItem(d)
        else:
            d = QFileDialog.getExistingDirectory(self, 'Chọn thư mục cha chứa nhiều audiobook')
            if d:
                existing = self._source_paths()
                if d not in existing:
                    self.sources_list.addItem(d)
        self._update_upload_enabled()

    def add_settings_roots(self):
        if self.mode_combo.currentData() != MODE_BATCH:
            return
        existing = set(self._source_paths())
        for p in self.default_root_folders:
            if p and p not in existing:
                self.sources_list.addItem(p)
                existing.add(p)
        self._update_upload_enabled()

    def remove_selected_source(self):
        for item in self.sources_list.selectedItems():
            self.sources_list.takeItem(self.sources_list.row(item))
        self._update_upload_enabled()

    def _source_paths(self):
        return [self.sources_list.item(i).text() for i in range(self.sources_list.count())]

    def _update_upload_enabled(self):
        self.upload_btn.setEnabled(bool(self._source_paths()) and not self._is_uploading())

    # -- upload ----------------------------------------------------------
    def _is_uploading(self):
        return bool(self.thread and self.thread.isRunning())

    def start_upload(self):
        if self._is_uploading():
            return
        library_name = self.library_combo.currentText().strip() or DEFAULT_LIBRARY_NAME
        sources = self._source_paths()
        if not sources:
            QMessageBox.information(self, 'Google Drive Sync', 'Hãy chọn nguồn sách nói trước.')
            return
        missing = [p for p in sources if not os.path.isdir(p)]
        if missing:
            QMessageBox.warning(self, 'Google Drive Sync', 'Thư mục không tồn tại:\n%s' % '\n'.join(missing[:5]))
            return

        prefs['audiobook_active_library'] = library_name
        mode = self.mode_combo.currentData()
        root_folders = sources if mode == MODE_BATCH else []
        book_folders = sources if mode == MODE_SINGLE_FOLDER else []

        self.log_view.clear()
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.status_label.setText('Đang chuẩn bị upload...')

        self.thread = AudiobookSyncThread(
            self.access_token, root_folders, parent=self, logger=self.logger,
            library_name=library_name, book_folders=book_folders)
        self.thread.progress.connect(self._on_progress)
        self.thread.log.connect(self._on_log)
        self.thread.finished_ok.connect(self._on_finished)
        self.thread.failed.connect(self._on_failed)
        self.thread.start()

        self._set_busy(True)

    def cancel_upload(self):
        if self.thread and self.thread.isRunning():
            self.thread.request_abort()
            self.status_label.setText('Đang hủy sau khi file hiện tại kết thúc...')

    def _set_busy(self, busy):
        for w in (
            self.library_combo, self.refresh_libraries_btn, self.new_library_btn,
            self.mode_combo, self.sources_list, self.add_source_btn,
            self.add_settings_roots_btn, self.remove_source_btn,
        ):
            w.setEnabled(not busy)
        self.upload_btn.setEnabled(not busy and bool(self._source_paths()))
        self.cancel_btn.setEnabled(busy)
        self.buttons.button(QDialogButtonBox.Close).setEnabled(not busy)

    def _on_progress(self, done, total):
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(done)
        self.status_label.setText('Đang upload... (%d/%d cuốn)' % (done, total))

    def _on_log(self, message):
        self.log_view.appendPlainText(message)

    def _on_finished(self, stats):
        self.status_label.setText(
            'Hoàn tất. Đã đồng bộ: %d, bỏ qua (không đổi): %d, lỗi: %d.' % (
                stats.get('books_synced', 0), stats.get('books_skipped', 0), stats.get('books_failed', 0)))
        self._set_busy(False)

    def _on_failed(self, message):
        self.status_label.setText('Thất bại: %s' % message)
        self.log_view.appendPlainText('LỖI: %s' % message)
        self._set_busy(False)

    def reject(self):
        if self._is_uploading():
            QMessageBox.information(self, 'Google Drive Sync', 'Đang upload, hãy bấm Hủy upload trước.')
            return
        self._detach_folder_loader()
        QDialog.reject(self)

    def closeEvent(self, event):
        if self._is_uploading():
            QMessageBox.information(self, 'Google Drive Sync', 'Đang upload, hãy bấm Hủy upload trước.')
            event.ignore()
            return
        self._detach_folder_loader()
        QDialog.closeEvent(self, event)

    def _detach_folder_loader(self):
        t = self.folder_loader
        self.folder_loader = None
        if not t or not t.isRunning():
            return
        t.requestInterruption()
        try:
            t.finished_ok.disconnect(self._on_libraries_loaded)
        except TypeError:
            pass
        try:
            t.failed.disconnect(self._on_libraries_failed)
        except TypeError:
            pass
        app = QApplication.instance()
        if app is not None:
            t.setParent(app)
        t.finished.connect(t.deleteLater)

    def event(self, event):
        if maybe_show_help_from_event(self, event):
            return True
        return QDialog.event(self, event)

    def exec_(self, run_in_background=False):
        return QDialog.exec_(self)
