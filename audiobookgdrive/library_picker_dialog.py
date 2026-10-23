# -*- coding: utf-8 -*-
"""
library_picker_dialog.py
========================

Cửa sổ chọn Thư viện (folder trên GDrive) trước khi chạy
Đồng bộ / Kiểm tra / Quản lý / Mở / Thêm vào Voice.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDialogButtonBox,
    QMessageBox, QPushButton, QThread, pyqtSignal, QApplication,
    QWidget, QScrollArea, QGridLayout, QToolButton, Qt, QIcon, QPixmap,
    QSize, QColor, QPainter,
)

from audiobookgdrive.config import get_nonempty_library_names, prefs, update_library_cache_from_folders
from audiobookgdrive.help_utils import maybe_show_help_from_event
from audiobookgdrive.audiobook_sync.state_store import DEFAULT_LIBRARY_NAME


class DriveLibraryLoaderThread(QThread):
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


class LibraryCoverLoaderThread(QThread):
    cover_loaded = pyqtSignal(str, str)  # library_id_or_name, local_path

    def __init__(self, access_token, libraries, parent=None):
        QThread.__init__(self, parent)
        self.access_token = access_token
        self.libraries = list(libraries or [])

    def run(self):
        try:
            from audiobookgdrive.audiobook_sync import manage_ops
            for lib in self.libraries:
                if self.isInterruptionRequested():
                    return
                cover_id = (lib or {}).get('cover_file_id') or ''
                if not cover_id:
                    continue
                key = (lib or {}).get('id') or (lib or {}).get('name') or cover_id
                path = manage_ops.peek_cached_cover_path(cover_id, key='library|%s' % key)
                if not path:
                    try:
                        path = manage_ops.cached_cover_path(
                            self.access_token, 'library|%s' % key, cover_id)
                    except Exception:
                        path = None
                if path:
                    self.cover_loaded.emit(str(key), path)
        except Exception:
            return


def _library_placeholder_icon(size=220):
    pm = QPixmap(size, size)
    pm.fill(QColor('#dfe3e8'))
    painter = QPainter(pm)
    painter.setPen(QColor('#7d8490'))
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, 'No\nCover')
    painter.end()
    return QIcon(pm)


class LibraryPickerDialog(QDialog):
    """Chọn 1 thư viện từ danh sách đã cấu hình.

    Nếu chỉ có đúng 1 thư viện (mặc định Audiobooks) thì có thể bỏ qua
    dialog và trả về luôn -- gọi :meth:`pick` để tiện dùng.
    """

    def __init__(self, parent=None, title='Chọn thư viện', message=None,
                 access_token=None, load_from_drive=False):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        self.resize(460, 170)
        self._selected = None
        self.access_token = access_token
        self.load_from_drive = bool(load_from_drive and access_token)
        self.loader_thread = None
        self.cover_thread = None
        self._libraries = []
        self._items_by_key = {}
        self._selected_key = ''
        self._placeholder_icon = _library_placeholder_icon()

        layout = QVBoxLayout(self)
        if self.load_from_drive:
            self.resize(900, 520)
            title_label = QLabel('<b style="font-size:16pt;">Thư viện của bạn</b>', self)
            title_label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(title_label)
            hint_text = 'Hiện từ cache lần quét trước -- bấm "Tải từ Drive" để làm mới từ Drive.'
        else:
            self.resize(460, 170)
            hint_text = message or 'Chọn thư viện sách nói trên Google Drive:'
        hint = QLabel(hint_text if self.load_from_drive else (message or hint_text))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel('Thư viện:'))
        self.combo = QComboBox(self)
        self.combo.setVisible(not self.load_from_drive)
        libraries = self._cached_libraries(include_configured=not self.load_from_drive)
        active = prefs.get('audiobook_active_library') or DEFAULT_LIBRARY_NAME
        self._populate_combo(libraries, active=active)
        idx = self.combo.findText(active)
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        row.addWidget(self.combo, stretch=1)
        layout.addLayout(row)

        self.library_grid = QScrollArea(self)
        self.library_grid.setVisible(self.load_from_drive)
        self.library_grid.setWidgetResizable(True)
        self.library_grid.setFrameShape(QScrollArea.Shape.StyledPanel)
        self.library_grid_widget = QWidget(self.library_grid)
        self.library_grid_layout = QGridLayout(self.library_grid_widget)
        self.library_grid_layout.setContentsMargins(14, 14, 14, 14)
        self.library_grid_layout.setHorizontalSpacing(22)
        self.library_grid_layout.setVerticalSpacing(18)
        self.library_grid.setWidget(self.library_grid_widget)
        layout.addWidget(self.library_grid, 1)
        if self.load_from_drive:
            self._populate_grid(active=active)

        status_row = QHBoxLayout()
        self.status_label = QLabel('')
        status_row.addWidget(self.status_label, stretch=1)
        self.refresh_btn = QPushButton('Tải từ Drive')
        self.refresh_btn.clicked.connect(self.refresh_from_drive)
        self.refresh_btn.setVisible(self.load_from_drive)
        status_row.addWidget(self.refresh_btn)
        layout.addLayout(status_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.load_from_drive:
            if prefs.get('audiobook_library_cache'):
                self.status_label.setText('Hiện từ cache lần quét trước -- bấm "Tải từ Drive" để làm mới.')
            else:
                self.status_label.setText('Chưa có cache thư viện -- bấm "Tải từ Drive" để tải danh sách.')

    def _configured_libraries(self):
        libraries = get_nonempty_library_names(include_configured=True)
        if not libraries:
            libraries = [DEFAULT_LIBRARY_NAME]
        seen = set()
        out = []
        for name in libraries:
            name = (name or '').strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out or [DEFAULT_LIBRARY_NAME]

    def _cached_libraries(self, include_configured=True):
        items = []
        seen = set()
        for item in prefs.get('audiobook_library_cache') or []:
            if not isinstance(item, dict):
                continue
            name = (item.get('name') or '').strip()
            if not name or name in seen:
                continue
            if item.get('id') and not item.get('has_metadata'):
                continue
            seen.add(name)
            items.append({
                'name': name,
                'id': item.get('id') or '',
                'has_metadata': bool(item.get('has_metadata')),
                'has_book_folder': bool(item.get('has_book_folder')),
                'metadata_file_id': item.get('metadata_file_id') or '',
                'book_count': item.get('book_count') or 0,
                'cover_file_id': item.get('cover_file_id') or '',
            })
        if include_configured:
            for name in self._configured_libraries():
                if name not in seen:
                    seen.add(name)
                    items.append({'name': name, 'id': '', 'has_metadata': False, 'has_book_folder': False})
        return items

    def _populate_combo(self, items, active=None):
        self.combo.clear()
        self._libraries = []
        self._items_by_key = {}
        active = active or prefs.get('audiobook_active_library') or DEFAULT_LIBRARY_NAME
        for item in items:
            if isinstance(item, dict):
                name = (item.get('name') or '').strip()
                data = item
            else:
                name = (item or '').strip()
                data = {'name': name, 'id': '', 'has_metadata': False, 'has_book_folder': False}
            if name:
                self.combo.addItem(name, data)
                if isinstance(data, dict):
                    self._libraries.append(data)
        idx = self.combo.findText(active)
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._populate_grid(active=active)

    def _library_key(self, item):
        return str((item or {}).get('id') or (item or {}).get('name') or '')

    def _icon_for_library(self, item):
        cover_id = (item or {}).get('cover_file_id') or ''
        if cover_id:
            try:
                from audiobookgdrive.audiobook_sync import manage_ops
                key = self._library_key(item)
                path = manage_ops.peek_cached_cover_path(cover_id, key='library|%s' % key)
                if path:
                    pm = QPixmap(path)
                    if not pm.isNull():
                        return QIcon(pm)
            except Exception:
                pass
        return self._placeholder_icon

    def _populate_grid(self, active=None):
        if not getattr(self, 'library_grid', None):
            return
        while self.library_grid_layout.count():
            child = self.library_grid_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self._items_by_key = {}
        self._selected_key = ''
        if not self.load_from_drive:
            return
        active = active or prefs.get('audiobook_active_library') or DEFAULT_LIBRARY_NAME
        first_lib = None
        active_lib = None
        for lib in self._libraries:
            name = lib.get('name') or ''
            if not name:
                continue
            count = int(lib.get('book_count') or 0)
            if count <= 0:
                continue
            if first_lib is None:
                first_lib = lib
            if name == active:
                active_lib = lib
            label = '%s\n%d sách' % (name, count)
            item = QToolButton(self.library_grid_widget)
            item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            item.setIcon(self._icon_for_library(lib))
            item.setIconSize(QSize(190, 260))
            item.setText(label)
            item.setCheckable(True)
            item.setAutoRaise(False)
            item.setFixedSize(QSize(230, 330))
            item.setStyleSheet(
                'QToolButton { text-align: center; padding: 8px; border: 1px solid #c8cdd4; '
                'border-radius: 8px; background: #ffffff; }'
                'QToolButton:hover { border-color: #6aa0e8; background: #f6f9ff; }'
                'QToolButton:checked { border: 2px solid #1a73e8; background: #eef5ff; }'
            )
            key = self._library_key(lib)
            self._items_by_key[key] = item
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            item.clicked.connect(lambda _checked=False, selected=lib: self._choose_library_card(selected))
            pos = len(self._items_by_key) - 1
            self.library_grid_layout.addWidget(item, pos // 5, pos % 5)
        self.library_grid_layout.setRowStretch((len(self._items_by_key) + 4) // 5, 1)
        self.library_grid_layout.setColumnStretch(5, 1)
        self._select_library_card(active_lib or first_lib)
        self.library_grid_widget.adjustSize()
        self._start_cover_loading()

    def _select_library_card(self, lib):
        if not lib:
            return
        key = self._library_key(lib)
        self._selected_key = key
        for item_key, item in self._items_by_key.items():
            item.setChecked(item_key == key)
        name = lib.get('name') or ''
        idx = self.combo.findText(name)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)

    def _choose_library_card(self, lib):
        self._select_library_card(lib)
        self._accept()

    def _start_cover_loading(self):
        if not self.load_from_drive or not self.access_token:
            return
        if self.cover_thread and self.cover_thread.isRunning():
            self.cover_thread.requestInterruption()
            self.cover_thread.wait(100)
        self.cover_thread = LibraryCoverLoaderThread(self.access_token, self._libraries, parent=self)
        self.cover_thread.cover_loaded.connect(self._on_cover_loaded)
        self.cover_thread.start()

    def _on_cover_loaded(self, key, local_path):
        item = self._items_by_key.get(str(key))
        if item is None:
            return
        pm = QPixmap(local_path)
        if not pm.isNull():
            item.setIcon(QIcon(pm))

    def refresh_from_drive(self):
        if not self.access_token:
            return
        if self.loader_thread and self.loader_thread.isRunning():
            return
        self.status_label.setText('Đang tải danh sách thư mục audiobook hợp lệ từ Google Drive...')
        self.refresh_btn.setEnabled(False)
        self.loader_thread = DriveLibraryLoaderThread(self.access_token, parent=self)
        self.loader_thread.finished_ok.connect(self._on_drive_libraries_loaded)
        self.loader_thread.failed.connect(self._on_drive_libraries_failed)
        self.loader_thread.start()

    def _on_drive_libraries_loaded(self, folders):
        active = prefs.get('audiobook_active_library') or DEFAULT_LIBRARY_NAME
        items = update_library_cache_from_folders(folders, merge_existing=True)
        self._populate_combo(items, active=active)
        self.status_label.setText(
            'Đã tải %d thư mục từ Drive và cập nhật cache.' % len(folders)
            if folders else
            'Chưa thấy thư mục mới trên Drive; đang dùng cache/danh sách đã cấu hình.')
        self.refresh_btn.setEnabled(True)

    def _on_drive_libraries_failed(self, message):
        self.status_label.setText('Không tải được từ Drive: %s' % message)
        self.refresh_btn.setEnabled(True)

    def _accept(self):
        if self.load_from_drive and self._selected_key:
            data = {}
            for lib in self._libraries:
                if self._library_key(lib) == self._selected_key:
                    data = lib
                    break
            self._selected = (data.get('name') or '').strip()
        else:
            data = self.combo.currentData() or {}
            self._selected = self.combo.currentText().strip()
        if not self._selected:
            QMessageBox.warning(self, self.windowTitle(), 'Vui lòng chọn thư viện.')
            return
        prefs['audiobook_active_library'] = self._selected
        folder_id = data.get('id') if isinstance(data, dict) else ''
        if folder_id:
            try:
                from audiobookgdrive.audiobook_sync.state_store import AudiobookState
                state = AudiobookState(library_name=self._selected)
                state.set_root_folder_id(folder_id)
                meta_id = data.get('metadata_file_id') if isinstance(data, dict) else ''
                if meta_id:
                    state.set_metadata_json_id(meta_id)
            except Exception:
                pass
        self._detach_loader()
        self._detach_cover_loader()
        self.accept()

    def selected_library(self):
        return self._selected

    @classmethod
    def pick(cls, parent=None, title='Chọn thư viện', message=None, force_show=False,
             access_token=None, load_from_drive=False):
        """Trả về tên thư viện đã chọn, hoặc ``None`` nếu huỷ.

        Nếu chỉ có 1 thư viện và ``force_show=False`` thì không hiện
        dialog, trả về luôn tên đó (và ghi active).
        """
        libraries = list(prefs.get('audiobook_library_folders') or [DEFAULT_LIBRARY_NAME])
        if not libraries:
            libraries = [DEFAULT_LIBRARY_NAME]
        if DEFAULT_LIBRARY_NAME not in libraries:
            libraries = [DEFAULT_LIBRARY_NAME] + libraries

        if len(libraries) == 1 and not force_show and not load_from_drive:
            name = libraries[0]
            prefs['audiobook_active_library'] = name
            return name

        dialog = cls(parent, title=title, message=message,
                     access_token=access_token, load_from_drive=load_from_drive)
        if dialog.exec_() != QDialog.Accepted:
            return None
        return dialog.selected_library()

    def _detach_loader(self):
        t = self.loader_thread
        self.loader_thread = None
        if not t or not t.isRunning():
            return
        try:
            t.finished_ok.disconnect(self._on_drive_libraries_loaded)
        except TypeError:
            pass
        try:
            t.failed.disconnect(self._on_drive_libraries_failed)
        except TypeError:
            pass
        app = QApplication.instance()
        if app is not None:
            t.setParent(app)
        t.finished.connect(t.deleteLater)

    def _detach_cover_loader(self):
        t = self.cover_thread
        self.cover_thread = None
        if not t or not t.isRunning():
            return
        t.requestInterruption()
        try:
            t.cover_loaded.disconnect(self._on_cover_loaded)
        except TypeError:
            pass
        app = QApplication.instance()
        if app is not None:
            t.setParent(app)
        t.finished.connect(t.deleteLater)

    def reject(self):
        self._detach_loader()
        self._detach_cover_loader()
        QDialog.reject(self)

    def closeEvent(self, event):
        self._detach_loader()
        self._detach_cover_loader()
        QDialog.closeEvent(self, event)

    def event(self, event):
        if maybe_show_help_from_event(self, event):
            return True
        return QDialog.event(self, event)
