# -*- coding: utf-8 -*-
"""
audiobook_manage_dialog.py
=============================

"Quản lý Audiobooks trên Drive..." (nâng cấp 2.6.0) -- duyệt/quản lý
các audiobook đã có trong ``Audiobooks/metadata_public.json`` trên
Drive, cùng bố cục/tinh thần với ``book_manage_dialog.DeviceLibraryManageDialog``
("Quản lý sách trên Device Library"): tìm kiếm + lọc + sắp xếp, chế độ
Danh sách/Lưới, đa chọn + xoá hàng loạt, dialog "Chi tiết" khi bấm đúp.

Khác với Device Library, dữ liệu nguồn ở đây là 1 file JSON duy nhất
(``metadata_public.json``, không phải manifest có tombstone) -- xem
``audiobook_sync/manage_ops.py`` cho toàn bộ nghiệp vụ đọc/sửa/xoá.

Mọi thao tác mạng chạy trên background QThread
(``audiobook_sync/manage_worker.py``) -- không bao giờ gọi Drive API
trên GUI thread.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import html
import os
import time

from PyQt5.Qt import (
    Qt, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QSpinBox, QPushButton, QCheckBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QProgressBar, QPlainTextEdit, QMessageBox, QScrollArea, QFrame, QSizePolicy,
    QFileDialog, QIcon, QPixmap, QSize, QPainter, QColor, QFrame,
    QUrl, pyqtSignal, QTimer, QApplication, QInputDialog,
)
from PyQt5.QtGui import QDesktopServices

from audiobookgdrive.config import get_nonempty_library_names, prefs
from audiobookgdrive.help_utils import maybe_show_help_from_event
from audiobookgdrive.audiobook_sync import manage_worker

VIEW_LIST = 'list'
VIEW_GRID = 'grid'

# Cùng mặc định với Device Library Manage (Lưới, 5 cuốn/hàng).
DEFAULT_VIEW = VIEW_GRID
DEFAULT_GRID_COLUMNS = 5

# (nhãn hiển thị, khoá dữ liệu combo) cho sắp xếp -- hàm khoá sort thật
# nằm trong SORT_FUNCS bên dưới (không lưu lambda làm userData của
# QComboBox, để tránh phụ thuộc vào việc PyQt5 có wrap được object Python
# tuỳ ý làm QVariant hay không).
SORT_OPTIONS = [
    ('Tên (A-Z)', 'title'),
    ('Mới thêm trước', 'added'),
    ('Kích thước lớn nhất trước', 'size'),
    ('Số chương nhiều nhất trước', 'chapters'),
]
SORT_FUNCS = {
    'title': lambda b: b.title.lower(),
    'added': lambda b: -b.added_at,
    'size': lambda b: -b.size_bytes,
    'chapters': lambda b: -b.chapter_count,
}

FILTER_OPTIONS = [
    ('Tất cả', 'all'),
    ('Có ảnh bìa', 'has_cover'),
    ('Không có ảnh bìa', 'no_cover'),
    ('Có chương', 'has_chapters'),
    ('Không có chương', 'no_chapters'),
    ('Có audio', 'has_audio'),
    ('Không có audio', 'no_audio'),
    ('Trùng title + tác giả', 'duplicates'),
]


def _placeholder_icon(size=96):
    pm = QPixmap(size, size)
    pm.fill(QColor('#dfe3e8'))
    painter = QPainter(pm)
    painter.setPen(QColor('#8a8f98'))
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, 'No\nCover')
    painter.end()
    return QIcon(pm)


def _format_size(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.0f %s' % (n, unit) if unit == 'B' else '%.1f %s' % (n, unit)
        n /= 1024.0
    return '%.1f GB' % n


def _format_datetime(ms):
    if not ms:
        return 'Không rõ'
    try:
        return time.strftime('%d/%m/%Y %H:%M', time.localtime(ms / 1000.0))
    except (ValueError, OSError, OverflowError):
        return 'Không rõ'


def _matches_filter(book, filter_key):
    if filter_key == 'has_cover':
        return book.has_cover
    if filter_key == 'no_cover':
        return not book.has_cover
    if filter_key == 'has_chapters':
        return book.chapter_count > 0
    if filter_key == 'no_chapters':
        return book.chapter_count == 0
    if filter_key == 'has_audio':
        return book.has_audio
    if filter_key == 'no_audio':
        return not book.has_audio
    return True


class AudiobookDetailDialog(QDialog):
    """"Chi tiết audiobook" -- mở khi bấm đúp vào ảnh bìa/dòng của 1
    cuốn, cùng bố cục với ``book_manage_dialog.BookDetailDialog``. Bản
    thân dialog này KHÔNG gọi mạng -- chỉ hiển thị dữ liệu đã có sẵn
    trong bộ nhớ và phát tín hiệu khi người dùng bấm nút hành động;
    dialog cha (``AudiobookLibraryManageDialog``) là nơi thực sự chạy
    QThread tương ứng."""

    edit_requested = pyqtSignal(str)              # key
    delete_requested = pyqtSignal(str)            # key
    resync_requested = pyqtSignal(str)            # key
    replace_cover_requested = pyqtSignal(str, str)  # key, local_image_path
    replace_opf_requested = pyqtSignal(str, str)    # key, local_opf_path
    download_requested = pyqtSignal(str, str)       # key, target_parent_dir
    save_metadata_requested = pyqtSignal(str, str, str, str, str, str)  # key, title, creators, publisher, language, desc
    move_requested = pyqtSignal(str, str)           # key, target_library_name

    def __init__(self, parent_dialog, book, cover_pixmap):
        QDialog.__init__(self, parent_dialog)
        self.book = book
        self.setWindowTitle('Chi tiết Audiobook')
        self.resize(640, 760)

        outer = QVBoxLayout()
        self.setLayout(outer)

        cover_label = QLabel(self)
        cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if cover_pixmap and not cover_pixmap.isNull():
            cover_label.setPixmap(cover_pixmap.scaledToHeight(
                220, Qt.TransformationMode.SmoothTransformation))
        else:
            cover_label.setPixmap(_placeholder_icon(180).pixmap(180, 180))
        outer.addWidget(cover_label)

        title_label = QLabel(
            '<div style="font-size:14pt; font-weight:bold;">%s</div>'
            % html.escape(book.title), self)
        title_label.setTextFormat(Qt.TextFormat.RichText)
        title_label.setWordWrap(True)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title_label)

        action_row = QHBoxLayout()
        self.edit_btn = QPushButton('✎  Sửa metadata', self)
        self.edit_btn.setToolTip(
            'Sửa tiêu đề/tác giả/NXB/ngôn ngữ/mô tả của cuốn này trong '
            'metadata_public.json (offline + trên Drive). Không đụng tới '
            'file metadata.opf hay file audio thật trên Drive.')
        self.edit_btn.clicked.connect(self._on_edit)
        action_row.addWidget(self.edit_btn)

        if book.drive_folder_id:
            open_btn = QPushButton('↗  Mở trên Drive', self)
            open_btn.clicked.connect(self._on_open_drive)
            action_row.addWidget(open_btn)

        self.delete_btn = QPushButton('🗑  Xóa', self)
        self.delete_btn.setToolTip('Xóa cuốn này khỏi metadata_public.json.')
        self.delete_btn.clicked.connect(self._on_delete)
        action_row.addWidget(self.delete_btn)

        if book.drive_folder_id:
            self.resync_btn = QPushButton('⟲  Đồng bộ từ Drive', self)
            self.resync_btn.setToolTip(
                'Đọc lại metadata.opf + ảnh bìa THẬT từ đúng thư mục của cuốn này trên '
                'Google Drive rồi ghi đè lại metadata_public.json (offline + Drive) -- '
                'khắc phục trường hợp "Không có ảnh bìa" do 1 lượt upload trước lỗi giữa '
                'chừng để lại nhiều file cover.jpg/metadata.opf trùng tên trong cùng thư '
                'mục (bản tạo sớm nhất được coi là bản chuẩn).')
            self.resync_btn.clicked.connect(self._on_resync)
            action_row.addWidget(self.resync_btn)
        outer.addLayout(action_row)

        # Hàng nút thay ảnh bìa / metadata.opf (tránh làm action_row quá chật)
        cover_row = QHBoxLayout()
        self.replace_cover_btn = QPushButton('🖼  Chọn ảnh thay thế', self)
        self.replace_cover_btn.setToolTip(
            'Chọn file ảnh trên máy rồi upload lên Google Drive thay thế '
            'ảnh bìa hiện tại. Nếu cuốn đã có ảnh bìa, giữ nguyên file_id '
            'trên Drive (không cần cập nhật metadata_public.json).')
        self.replace_cover_btn.clicked.connect(self._on_replace_cover)
        if not book.drive_folder_id and not book.cover_file_id:
            self.replace_cover_btn.setEnabled(False)
            self.replace_cover_btn.setToolTip(
                'Cuốn này chưa có thư mục trên Google Drive -- '
                'hãy chạy Đồng bộ trước khi thêm ảnh bìa.')
        cover_row.addWidget(self.replace_cover_btn)

        self.replace_opf_btn = QPushButton('📄  Chọn metadata.opf thay thế', self)
        self.replace_opf_btn.setToolTip(
            'Chọn file .opf trên máy rồi upload lên Google Drive thay thế '
            'metadata.opf hiện tại (giữ nguyên file_id nếu đã có). Sau đó '
            'cập nhật title/tác giả/NXB/mô tả/chương... vào metadata_public.json '
            '(offline + Drive).')
        self.replace_opf_btn.clicked.connect(self._on_replace_opf)
        if not book.drive_folder_id:
            self.replace_opf_btn.setEnabled(False)
            self.replace_opf_btn.setToolTip(
                'Cuốn này chưa có thư mục trên Google Drive -- '
                'hãy chạy Đồng bộ trước khi thay metadata.opf.')
        cover_row.addWidget(self.replace_opf_btn)

        self.download_btn = QPushButton('⬇  Tải xuống cuốn này...', self)
        self.download_btn.setToolTip(
            'Tải toàn bộ thư mục sách nói về máy để nghe offline, gồm audio, cover và metadata.opf.')
        self.download_btn.clicked.connect(self._on_download)
        if not book.drive_folder_id:
            self.download_btn.setEnabled(False)
            self.download_btn.setToolTip('Cuốn này chưa có thư mục trên Google Drive để tải xuống.')
        cover_row.addWidget(self.download_btn)
        cover_row.addStretch()
        outer.addLayout(cover_row)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line)

        outer.addWidget(QLabel('<b>Sửa metadata</b>', self))
        outer.addWidget(QLabel('Tiêu đề:', self))
        self.title_edit = QLineEdit(book.title, self)
        outer.addWidget(self.title_edit)
        outer.addWidget(QLabel('Tác giả (phân tách bằng dấu phẩy):', self))
        self.creators_edit = QLineEdit(', '.join(book.creators), self)
        outer.addWidget(self.creators_edit)
        meta_row = QHBoxLayout()
        left_meta = QVBoxLayout()
        left_meta.addWidget(QLabel('NXB:', self))
        self.publisher_edit = QLineEdit(book.publisher, self)
        left_meta.addWidget(self.publisher_edit)
        meta_row.addLayout(left_meta, 1)
        right_meta = QVBoxLayout()
        right_meta.addWidget(QLabel('Ngôn ngữ:', self))
        self.language_edit = QLineEdit(book.language, self)
        right_meta.addWidget(self.language_edit)
        meta_row.addLayout(right_meta, 1)
        outer.addLayout(meta_row)
        outer.addWidget(QLabel('Mô tả:', self))
        self.description_edit = QPlainTextEdit(book.description, self)
        self.description_edit.setMaximumHeight(100)
        outer.addWidget(self.description_edit)

        save_row = QHBoxLayout()
        self.save_btn = QPushButton('Lưu metadata', self)
        self.save_btn.clicked.connect(self._on_save_metadata)
        save_row.addWidget(self.save_btn)
        save_row.addStretch()
        outer.addLayout(save_row)

        outer.addWidget(QLabel('<b>Thông tin</b>', self))
        audio_cell = '%d' % book.audio_file_count
        if not book.has_audio:
            audio_cell = '<span style="color:#c0392b;"><b>0 -- ⚠ không có audio!</b></span>'
        info_html = (
            '<table cellspacing="6">'
            '<tr><td><b>Tác giả</b></td><td colspan="3">%s</td></tr>'
            '<tr><td><b>NXB</b></td><td>%s</td>'
            '<td style="padding-left:24px;"><b>Ngôn ngữ</b></td><td>%s</td></tr>'
            '<tr><td><b>Kích thước</b></td><td>%s</td>'
            '<td style="padding-left:24px;"><b>Số chương</b></td><td>%s</td></tr>'
            '<tr><td><b>Số file audio</b></td><td colspan="3">%s</td></tr>'
            '<tr><td valign="top"><b>Đã thêm</b></td><td colspan="3">%s</td></tr>'
            '</table>'
        ) % (
            html.escape(book.creators_display),
            html.escape(book.publisher or 'Không rõ'),
            html.escape(book.language or 'Không rõ'),
            html.escape(_format_size(book.size_bytes)),
            book.chapter_count,
            audio_cell,
            html.escape(_format_datetime(book.added_at)),
        )
        info_label = QLabel(info_html, self)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        outer.addWidget(info_label)

        move_box = QFrame(self)
        move_box.setFrameShape(QFrame.Shape.StyledPanel)
        move_layout = QVBoxLayout(move_box)
        move_layout.addWidget(QLabel('<b>Di chuyển sang thư viện khác</b>', self))
        move_row = QHBoxLayout()
        self.move_combo = QComboBox(self)
        self.move_combo.addItem('— Chọn thư viện đích —', '')
        parent_lib = getattr(parent_dialog, 'library_name', '') or ''
        for lib in self._available_libraries(parent_lib):
            self.move_combo.addItem(lib, lib)
        move_row.addWidget(self.move_combo, 1)
        self.move_btn = QPushButton('Di chuyển', self)
        self.move_btn.clicked.connect(self._on_move)
        move_row.addWidget(self.move_btn)
        move_layout.addLayout(move_row)
        note = QLabel('Chuyển thư mục sách trên Drive và cập nhật metadata_public.json ở cả hai thư viện.', self)
        note.setWordWrap(True)
        move_layout.addWidget(note)
        outer.addWidget(move_box)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton('Đóng', self)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        outer.addLayout(close_row)

    def _on_open_drive(self):
        QDesktopServices.openUrl(
            QUrl('https://drive.google.com/drive/folders/%s' % self.book.drive_folder_id))

    # Đóng dialog chi tiết TRƯỚC khi phát tín hiệu, cùng lý do đã ghi
    # trong book_manage_dialog.BookDetailDialog: dialog cha sẽ reload()
    # sau khi hành động xong, làm mất hiệu lực mọi item mà dialog này
    # còn giữ tham chiếu gián tiếp.
    def _on_edit(self):
        self.accept()
        self.edit_requested.emit(self.book.key)

    def _on_save_metadata(self):
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, 'Google Drive Sync', 'Tiêu đề không được để trống.')
            return
        self.accept()
        self.save_metadata_requested.emit(
            self.book.key,
            title,
            self.creators_edit.text().strip(),
            self.publisher_edit.text().strip(),
            self.language_edit.text().strip(),
            self.description_edit.toPlainText().strip(),
        )

    def _on_delete(self):
        self.accept()
        self.delete_requested.emit(self.book.key)

    def _on_resync(self):
        self.accept()
        self.resync_requested.emit(self.book.key)

    def _on_replace_cover(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            'Chọn ảnh bìa thay thế',
            '',
            'Ảnh (*.jpg *.jpeg *.png *.webp *.gif);;Tất cả file (*)',
        )
        if not path:
            return
        # Đóng dialog chi tiết trước khi phát tín hiệu (cùng lý do các nút khác)
        self.accept()
        self.replace_cover_requested.emit(self.book.key, path)

    def _on_replace_opf(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            'Chọn metadata.opf thay thế',
            '',
            'OPF (*.opf);;XML (*.xml);;Tất cả file (*)',
        )
        if not path:
            return
        self.accept()
        self.replace_opf_requested.emit(self.book.key, path)

    def _on_download(self):
        d = QFileDialog.getExistingDirectory(
            self,
            'Chọn thư mục lưu audiobook offline',
            prefs.get('audiobook_download_folder') or '',
        )
        if not d:
            return
        prefs['audiobook_download_folder'] = d
        self.accept()
        self.download_requested.emit(self.book.key, d)

    def _available_libraries(self, current_library):
        return get_nonempty_library_names(exclude_name=current_library)

    def _on_move(self):
        target = self.move_combo.currentData()
        if not target:
            QMessageBox.information(self, 'Google Drive Sync', 'Hãy chọn thư viện đích.')
            return
        reply = QMessageBox.question(
            self, 'Google Drive Sync',
            'Di chuyển "%s" sang thư viện "%s"?' % (self.book.title, target),
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.accept()
        self.move_requested.emit(self.book.key, target)

    def event(self, event):
        if maybe_show_help_from_event(self, event):
            return True
        return QDialog.event(self, event)


class AudiobookLibraryManageDialog(QDialog):
    RESELECT_LIBRARY_CODE = 1001

    def __init__(self, gui, access_token, library_name=None):
        QDialog.__init__(self, gui)
        self.gui = gui
        self.access_token = access_token
        self.library_name = library_name or 'Audiobooks'
        self.root_id = None
        self.books = []
        self._by_key = {}
        self._items_by_key = {}
        self._cover_paths = {}
        self._cover_icons = {}       # key -> QIcon (cache, tránh load pixmap mỗi lần lọc)
        self._book_cache = {}        # key -> dict trường hiển thị đã precompute
        self._duplicate_keys = set()
        self.list_thread = None
        self.cover_thread = None
        self.action_thread = None
        self.download_thread = None
        self._reopen_detail_key = None
        self._pending_reopen_detail_key = None
        self._placeholder = _placeholder_icon()
        # Debounce ô tìm kiếm -- tránh rebuild 400 item mỗi phím gõ
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._refresh_view)

        self.setWindowTitle('Quản lý Audiobooks trên Drive — %s' % (library_name or 'Audiobooks'))
        self.resize(920, 640)

        outer = QVBoxLayout()
        self.setLayout(outer)

        top = QHBoxLayout()
        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText('Tìm theo tiêu đề hoặc tác giả...')
        self.search_box.textChanged.connect(self._on_search_text_changed)
        top.addWidget(self.search_box, 1)

        top.addWidget(QLabel('Lọc:'))
        self.filter_combo = QComboBox(self)
        for label, key in FILTER_OPTIONS:
            self.filter_combo.addItem(label, key)
        self.filter_combo.currentIndexChanged.connect(self._refresh_view)
        top.addWidget(self.filter_combo)

        top.addWidget(QLabel('Sắp xếp:'))
        self.sort_combo = QComboBox(self)
        for label, key in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.currentIndexChanged.connect(self._refresh_view)
        top.addWidget(self.sort_combo)
        outer.addLayout(top)

        top2 = QHBoxLayout()
        top2.addWidget(QLabel('Hiển thị:'))
        self.view_combo = QComboBox(self)
        self.view_combo.addItem('Danh sách', VIEW_LIST)
        self.view_combo.addItem('Lưới', VIEW_GRID)
        top2.addWidget(self.view_combo)

        top2.addWidget(QLabel('Cột:'))
        self.columns_spin = QSpinBox(self)
        self.columns_spin.setRange(2, 8)
        self.columns_spin.setValue(DEFAULT_GRID_COLUMNS)
        self.columns_spin.setEnabled(False)
        self.columns_spin.valueChanged.connect(self._apply_grid_geometry)
        top2.addWidget(self.columns_spin)
        top2.addStretch()
        outer.addLayout(top2)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setViewMode(QListWidget.ViewMode.ListMode)
        self.list_widget.setIconSize(QSize(32, 32))
        self.list_widget.itemSelectionChanged.connect(self._update_action_buttons)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        outer.addWidget(self.list_widget, 1)

        # Kích hoạt chế độ hiển thị mặc định NGAY SAU khi list_widget đã
        # tồn tại, cùng thủ thuật book_manage_dialog dùng: kết nối tín
        # hiệu view_combo ở đây để setCurrentIndex() bên dưới tự gọi
        # _on_view_changed() đúng 1 lần.
        idx = self.view_combo.findData(DEFAULT_VIEW)
        if idx >= 0:
            self.view_combo.setCurrentIndex(idx)
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        self._on_view_changed(self.view_combo.currentIndex())

        select_row = QHBoxLayout()
        self.select_all_btn = QPushButton('Chọn tất cả', self)
        self.select_all_btn.clicked.connect(self.list_widget.selectAll)
        select_row.addWidget(self.select_all_btn)
        self.select_none_btn = QPushButton('Bỏ chọn', self)
        self.select_none_btn.clicked.connect(self.list_widget.clearSelection)
        select_row.addWidget(self.select_none_btn)
        select_row.addStretch()
        self.count_label = QLabel('0 audiobook')
        select_row.addWidget(self.count_label)
        outer.addLayout(select_row)

        move_box = QFrame(self)
        move_box.setFrameShape(QFrame.Shape.StyledPanel)
        move_layout = QHBoxLayout(move_box)
        move_layout.addWidget(QLabel('Di chuyển đã chọn sang thư viện:', self))
        self.bulk_move_combo = QComboBox(self)
        self.bulk_move_combo.currentIndexChanged.connect(self._update_action_buttons)
        move_layout.addWidget(self.bulk_move_combo, 1)
        self.bulk_move_btn = QPushButton('Di chuyển', self)
        self.bulk_move_btn.clicked.connect(self._move_selected)
        move_layout.addWidget(self.bulk_move_btn)
        outer.addWidget(move_box)
        self._populate_bulk_move_targets()

        btn_row = QHBoxLayout()
        self.edit_btn = QPushButton('Sửa metadata...', self)
        self.edit_btn.clicked.connect(self._edit_selected)
        btn_row.addWidget(self.edit_btn)
        self.purge_checkbox = QCheckBox('Xóa cả thư mục trên Drive', self)
        self.purge_checkbox.setToolTip(
            'Bỏ trống: chỉ xóa khỏi metadata_public.json (app đọc sách nói '
            'sẽ không còn thấy cuốn này nữa, nhưng file thật trên Drive vẫn '
            'còn). Chọn: xóa vĩnh viễn cả thư mục cuốn sách trên Google '
            'Drive để giải phóng dung lượng -- không thể hoàn tác.')
        btn_row.addWidget(self.purge_checkbox)
        self.delete_btn = QPushButton('Xóa đã chọn', self)
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch()
        self.refresh_btn = QPushButton('Làm mới', self)
        self.refresh_btn.clicked.connect(self.reload)
        btn_row.addWidget(self.refresh_btn)
        self.rename_library_btn = QPushButton('Đổi tên thư viện', self)
        self.rename_library_btn.clicked.connect(self._rename_library)
        btn_row.addWidget(self.rename_library_btn)
        self.reselect_library_btn = QPushButton('Quay lại chọn Thư viện', self)
        self.reselect_library_btn.clicked.connect(self._reselect_library)
        btn_row.addWidget(self.reselect_library_btn)
        self.close_btn = QPushButton('Đóng', self)
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn)
        outer.addLayout(btn_row)

        self.status_label = QLabel('Đang tải...')
        outer.addWidget(self.status_label)
        self.progress_bar = QProgressBar(self)
        outer.addWidget(self.progress_bar)
        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(90)
        outer.addWidget(self.log_view)

        self._set_busy(True)
        self.reload()

    def _populate_bulk_move_targets(self):
        self.bulk_move_combo.clear()
        self.bulk_move_combo.addItem('— Chọn thư viện đích —', '')
        seen = set()

        def add_name(name):
            name = (name or '').strip()
            if not name or name == self.library_name or name in seen:
                return
            seen.add(name)
            self.bulk_move_combo.addItem(name, name)

        for name in get_nonempty_library_names(exclude_name=self.library_name):
            add_name(name)
        if self.bulk_move_combo.count() <= 1:
            self.bulk_move_combo.setToolTip('Chưa có thư viện đích khác trong cache. Hãy quay lại chọn thư viện rồi bấm Tải từ Drive.')
        else:
            self.bulk_move_combo.setToolTip('')

    def _reselect_library(self):
        self._detach_cover_loader()
        self.done(self.RESELECT_LIBRARY_CODE)

    def _rename_library(self):
        new_name, ok = QInputDialog.getText(
            self, 'Đổi tên thư viện',
            'Tên thư viện mới trên Google Drive:', text=self.library_name)
        if not ok:
            return
        new_name = (new_name or '').strip()
        if not new_name:
            QMessageBox.warning(self, 'Google Drive Sync', 'Tên thư viện mới không được để trống.')
            return
        if new_name == self.library_name:
            return
        reply = QMessageBox.question(
            self, 'Google Drive Sync',
            'Đổi tên thư viện "%s" thành "%s"?\n\nGoogle Drive sẽ giữ nguyên folder id; app sẽ cập nhật cache/state local theo tên mới.'
            % (self.library_name, new_name),
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.status_label.setText('Đang đổi tên thư viện...')
        self.action_thread = manage_worker.RenameLibraryThread(
            self.access_token, self.library_name, new_name, parent=self)
        self.action_thread.log.connect(self.log_view.appendPlainText)
        self.action_thread.finished_ok.connect(self._on_rename_library_finished)
        self.action_thread.failed.connect(self._on_action_failed)
        self.action_thread.start()

    def _on_rename_library_finished(self, new_name):
        self.library_name = new_name
        self.setWindowTitle('Quản lý Audiobooks trên Drive — %s' % self.library_name)
        self.status_label.setText('Đã đổi tên thư viện thành "%s".' % self.library_name)
        self.reload()

    # -- loading -----------------------------------------------------
    def reload(self):
        self._set_busy(True)
        self.status_label.setText('Đang tải metadata_public.json từ Google Drive...')
        self.list_widget.clear()
        self._items_by_key = {}
        # Giữ _cover_paths / _cover_icons giữa các lần reload (file_id không đổi)
        # nhưng reset book_cache vì metadata có thể đã đổi
        self._book_cache = {}

        self.list_thread = manage_worker.ListAudiobooksThread(self.access_token, parent=self, library_name=self.library_name)
        self.list_thread.finished_ok.connect(self._on_list_finished)
        self.list_thread.failed.connect(self._on_list_failed)
        self.list_thread.start()

    def _on_list_finished(self, root_id, books, source):
        self.root_id = root_id
        self.books = books
        self._by_key = {b.key: b for b in books}
        self._build_book_cache(books)
        source_label = 'Google Drive' if source == 'drive' else 'bản backup offline'
        # Nạp bìa đã cache trên đĩa ngay (không chờ thread / mạng)
        from audiobookgdrive.audiobook_sync.manage_ops import peek_cached_cover_path
        cached_n = 0
        for b in books:
            if not b.cover_file_id:
                continue
            path = peek_cached_cover_path(b.cover_file_id, key=b.key)
            if path:
                self._cover_paths[b.key] = path
                # Pre-warm icon cache (chỉ những key chưa có icon)
                if b.key not in self._cover_icons:
                    self._icon_for_book(b.key)
                cached_n += 1
        self.status_label.setText(
            '%d audiobook (nguồn: %s)%s.' % (
                len(books), source_label,
                (', %d bìa từ cache' % cached_n) if cached_n else ''))
        self._set_busy(False)
        self._refresh_view()
        self._start_cover_loading(books)

        # Mở lại dialog Chi tiết sau khi thay OPF (dữ liệu đã reload)
        reopen_key = getattr(self, '_pending_reopen_detail_key', None)
        self._pending_reopen_detail_key = None
        if reopen_key and reopen_key in self._by_key:
            item = self._items_by_key.get(reopen_key)
            if item is not None:
                self._show_book_details(item)
            else:
                # Item có thể bị filter ẩn -- vẫn mở bằng book object
                book = self._by_key[reopen_key]
                cover_pm = None
                path = self._cover_paths.get(reopen_key)
                if path:
                    pm = QPixmap(path)
                    if not pm.isNull():
                        cover_pm = pm
                dlg = AudiobookDetailDialog(self, book, cover_pm)
                dlg.edit_requested.connect(self._edit_metadata_dialog)
                dlg.delete_requested.connect(self._delete_by_key)
                dlg.resync_requested.connect(self._resync_by_key)
                dlg.replace_cover_requested.connect(self._replace_cover_by_key)
                dlg.replace_opf_requested.connect(self._replace_opf_by_key)
                dlg.download_requested.connect(self._download_by_key)
                dlg.save_metadata_requested.connect(self._save_metadata_by_key)
                dlg.move_requested.connect(self._move_by_key)
                dlg.exec_()

    def _on_list_failed(self, message):
        self.status_label.setText('Tải danh sách thất bại: %s' % message)
        self._set_busy(False)

    # -- search/filter/sort -------------------------------------------
    def _on_search_text_changed(self, *_args):
        # Debounce: chỉ refresh sau khi người dùng ngừng gõ ~120ms
        self._search_timer.start()

    def _build_book_cache(self, books):
        """Precompute chuỗi tìm kiếm / nhãn / size / tooltip -- chạy 1 lần
        sau khi load danh sách, không lặp lại mỗi lần lọc."""
        cache = {}
        dup_index = {}
        for book in books:
            title = book.title or ''
            creators = book.creators_display
            has_audio = book.has_audio
            size_bytes = book.size_bytes
            chapter_count = book.chapter_count
            audio_count = book.audio_file_count
            added = _format_datetime(book.added_at)
            size_txt = _format_size(size_bytes)
            label = '%s — %s' % (title, creators)
            if not has_audio:
                label = '⚠ KHÔNG CÓ AUDIO — ' + label
            tooltip = (
                '%s\n%s\n%s -- %d chương -- %d file audio -- thêm %s%s' % (
                    title, creators, size_txt, chapter_count, audio_count, added,
                    '\n⚠ Không có file audio nào!' if not has_audio else ''))
            cache[book.key] = {
                'search': (title + ' ' + creators).lower(),
                'label': label,
                'tooltip': tooltip,
                'has_audio': has_audio,
                'has_cover': book.has_cover,
                'chapter_count': chapter_count,
                'size_bytes': size_bytes,
                'added_at': book.added_at or 0,
                'title_lower': title.lower(),
            }
            dup_key = ((title or '').strip().lower(), (creators or '').strip().lower())
            if dup_key != ('', ''):
                dup_index.setdefault(dup_key, []).append(book.key)
        self._book_cache = cache
        self._duplicate_keys = {
            key for keys in dup_index.values() if len(keys) > 1 for key in keys
        }

    def _refresh_view(self, *_args):
        text = self.search_box.text().strip().lower()
        filter_key = self.filter_combo.currentData()
        sort_key = self.sort_combo.currentData() or 'title'
        cache = self._book_cache

        def matches(book):
            c = cache.get(book.key)
            if not c:
                return False
            if text and text not in c['search']:
                return False
            if filter_key == 'has_cover':
                return c['has_cover']
            if filter_key == 'no_cover':
                return not c['has_cover']
            if filter_key == 'has_chapters':
                return c['chapter_count'] > 0
            if filter_key == 'no_chapters':
                return c['chapter_count'] == 0
            if filter_key == 'has_audio':
                return c['has_audio']
            if filter_key == 'no_audio':
                return not c['has_audio']
            if filter_key == 'duplicates':
                return book.key in self._duplicate_keys
            return True

        visible = [b for b in self.books if matches(b)]
        if sort_key == 'added':
            visible.sort(key=lambda b: -(cache.get(b.key) or {}).get('added_at', 0))
        elif sort_key == 'size':
            visible.sort(key=lambda b: -(cache.get(b.key) or {}).get('size_bytes', 0))
        elif sort_key == 'chapters':
            visible.sort(key=lambda b: -(cache.get(b.key) or {}).get('chapter_count', 0))
        else:
            visible.sort(key=lambda b: (cache.get(b.key) or {}).get('title_lower', ''))
        self._populate(visible)

    def _icon_for_book(self, key):
        """Lấy QIcon đã cache; chỉ load pixmap từ đĩa lần đầu."""
        icon = self._cover_icons.get(key)
        if icon is not None:
            return icon
        path = self._cover_paths.get(key)
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                # Scale sẵn kích thước lưới phổ biến để paint nhanh hơn
                if pm.width() > 256 or pm.height() > 256:
                    pm = pm.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                icon = QIcon(pm)
                self._cover_icons[key] = icon
                return icon
        return self._placeholder

    def _populate(self, books):
        # Giữ lại lựa chọn hiện tại theo key khi sắp xếp/lọc lại
        selected_keys = {item.data(Qt.ItemDataRole.UserRole)
                          for item in self.list_widget.selectedItems()}
        lw = self.list_widget
        lw.setUpdatesEnabled(False)
        try:
            lw.clear()
            self._items_by_key = {}
            cache = self._book_cache
            placeholder = self._placeholder
            for book in books:
                c = cache.get(book.key) or {}
                label = c.get('label') or book.title
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, book.key)
                item.setIcon(self._icon_for_book(book.key) if book.key in self._cover_paths
                             else placeholder)
                if not c.get('has_audio', True):
                    item.setForeground(QColor('#c0392b'))
                if c.get('tooltip'):
                    item.setToolTip(c['tooltip'])
                lw.addItem(item)
                self._items_by_key[book.key] = item
                if book.key in selected_keys:
                    item.setSelected(True)
        finally:
            lw.setUpdatesEnabled(True)
        self.count_label.setText('%d / %d audiobook' % (len(books), len(self.books)))
        self._update_action_buttons()

    # -- covers --------------------------------------------------------
    def _start_cover_loading(self, books):
        if self.cover_thread and self.cover_thread.isRunning():
            self.cover_thread.requestInterruption()
            self.cover_thread.wait(200)
        self.cover_thread = manage_worker.CoverLoaderThread(self.access_token, books, parent=self)
        self.cover_thread.cover_loaded.connect(self._on_cover_loaded)
        self.cover_thread.start()

    def _on_cover_loaded(self, key, local_path):
        self._cover_paths[key] = local_path
        # Invalidate icon cache cho key này rồi tạo lại
        self._cover_icons.pop(key, None)
        icon = self._icon_for_book(key)
        item = self._items_by_key.get(key)
        if item is not None:
            item.setIcon(icon)

    # -- view mode -------------------------------------------------------
    def _on_view_changed(self, _index):
        mode = self.view_combo.currentData()
        self.columns_spin.setEnabled(mode == VIEW_GRID)
        if mode == VIEW_GRID:
            self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
            self.list_widget.setWrapping(True)
            self.list_widget.setMovement(QListWidget.Movement.Static)
            self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
            self._apply_grid_geometry()
        else:
            self.list_widget.setViewMode(QListWidget.ViewMode.ListMode)
            self.list_widget.setWrapping(False)
            self.list_widget.setIconSize(QSize(32, 32))
            self.list_widget.setGridSize(QSize())

    def _apply_grid_geometry(self):
        if self.view_combo.currentData() != VIEW_GRID:
            return
        columns = max(2, self.columns_spin.value())
        viewport_width = max(self.list_widget.viewport().width(), 400)
        cell_width = max(96, viewport_width // columns - 8)
        icon_size = int(cell_width * 0.75)
        self.list_widget.setIconSize(QSize(icon_size, icon_size))
        self.list_widget.setGridSize(QSize(cell_width, icon_size + 64))

    def resizeEvent(self, event):
        QDialog.resizeEvent(self, event)
        self._apply_grid_geometry()

    # -- actions ---------------------------------------------------------
    def _selected_books(self):
        return [b for item in self.list_widget.selectedItems()
                for b in [self._by_key.get(item.data(Qt.ItemDataRole.UserRole))] if b]

    def _update_action_buttons(self):
        if not hasattr(self, 'edit_btn'):
            return
        n = len(self.list_widget.selectedItems())
        self.edit_btn.setEnabled(n == 1)
        self.delete_btn.setEnabled(n >= 1)
        target = self.bulk_move_combo.currentData() if hasattr(self, 'bulk_move_combo') else ''
        if hasattr(self, 'bulk_move_btn'):
            self.bulk_move_btn.setEnabled(n >= 1 and bool(target))

    def _on_item_double_clicked(self, item):
        self._show_book_details(item)

    def _show_book_details(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        book = self._by_key.get(key)
        if not book:
            return
        cover_pm = item.icon().pixmap(220, 220) if not item.icon().isNull() else None
        if cover_pm is not None and cover_pm.isNull():
            cover_pm = None
        dlg = AudiobookDetailDialog(self, book, cover_pm)
        dlg.edit_requested.connect(self._edit_metadata_dialog)
        dlg.delete_requested.connect(self._delete_by_key)
        dlg.resync_requested.connect(self._resync_by_key)
        dlg.replace_cover_requested.connect(self._replace_cover_by_key)
        dlg.replace_opf_requested.connect(self._replace_opf_by_key)
        dlg.download_requested.connect(self._download_by_key)
        dlg.save_metadata_requested.connect(self._save_metadata_by_key)
        dlg.move_requested.connect(self._move_by_key)
        dlg.exec_()

    def _edit_selected(self):
        selected = self._selected_books()
        if len(selected) != 1:
            return
        self._edit_metadata_dialog(selected[0].key)

    def _edit_metadata_dialog(self, key):
        book = self._by_key.get(key)
        if not book:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle('Sửa metadata')
        dlg.resize(420, 360)
        layout = QVBoxLayout()
        dlg.setLayout(layout)

        layout.addWidget(QLabel('Tiêu đề:', dlg))
        title_edit = QLineEdit(book.title, dlg)
        layout.addWidget(title_edit)

        layout.addWidget(QLabel('Tác giả (phân tách bởi dấu phẩy):', dlg))
        creators_edit = QLineEdit(', '.join(book.creators), dlg)
        layout.addWidget(creators_edit)

        layout.addWidget(QLabel('Nhà xuất bản:', dlg))
        publisher_edit = QLineEdit(book.publisher, dlg)
        layout.addWidget(publisher_edit)

        layout.addWidget(QLabel('Ngôn ngữ:', dlg))
        language_edit = QLineEdit(book.language, dlg)
        layout.addWidget(language_edit)

        layout.addWidget(QLabel('Mô tả:', dlg))
        description_edit = QPlainTextEdit(book.description, dlg)
        description_edit.setMaximumHeight(100)
        layout.addWidget(description_edit)

        note = QLabel(
            'Chỉ cập nhật metadata_public.json (offline + trên Drive) -- '
            'không đụng tới metadata.opf hay file audio thật trên Drive.', dlg)
        note.setWordWrap(True)
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton('Lưu', dlg)
        save_btn.setDefault(True)
        save_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton('Hủy', dlg)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        if dlg.exec_() != QDialog.DialogCode.Accepted:
            return

        new_title = title_edit.text().strip()
        new_creators_text = creators_edit.text().strip()
        new_publisher = publisher_edit.text().strip()
        new_language = language_edit.text().strip()
        new_description = description_edit.toPlainText().strip()

        if not new_title:
            QMessageBox.warning(self, 'Google Drive Sync', 'Tiêu đề không được để trống.')
            return

        changed = (
            new_title != book.title
            or new_creators_text != ', '.join(book.creators)
            or new_publisher != book.publisher
            or new_language != book.language
            or new_description != book.description
        )
        if not changed:
            return

        self._run_action(
            manage_worker.EditMetadataThread(
                self.access_token, key, title=new_title, creators_text=new_creators_text,
                publisher=new_publisher, language=new_language, description=new_description,
                parent=self, library_name=self.library_name),
            'Đang cập nhật metadata...')

    def _save_metadata_by_key(self, key, title, creators_text, publisher, language, description):
        self._run_action(
            manage_worker.EditMetadataThread(
                self.access_token, key, title=title, creators_text=creators_text,
                publisher=publisher, language=language, description=description,
                parent=self, library_name=self.library_name),
            'Đang lưu metadata...')

    def _move_by_key(self, key, target_library_name):
        self._run_action(
            manage_worker.MoveAudiobookThread(
                self.access_token, key, target_library_name,
                parent=self, library_name=self.library_name),
            'Đang di chuyển sang thư viện "%s"...' % target_library_name)

    def _move_selected(self):
        selected = self._selected_books()
        if not selected:
            return
        target = self.bulk_move_combo.currentData()
        if not target:
            QMessageBox.warning(self, 'Google Drive Sync', 'Vui lòng chọn thư viện đích.')
            return
        if target == self.library_name:
            QMessageBox.warning(self, 'Google Drive Sync', 'Thư viện đích phải khác thư viện hiện tại.')
            return
        sample = '\n'.join('- %s' % b.title for b in selected[:5])
        if len(selected) > 5:
            sample += '\n...'
        reply = QMessageBox.question(
            self, 'Google Drive Sync',
            'Di chuyển %d audiobook sang thư viện "%s"?\n\n%s\n\n'
            'Thao tác này sẽ di chuyển thư mục thật trên Drive và cập nhật metadata_public.json.'
            % (len(selected), target, sample),
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_action(
            manage_worker.MoveAudiobooksThread(
                self.access_token, [b.key for b in selected], target,
                parent=self, library_name=self.library_name),
            'Đang di chuyển %d audiobook sang thư viện "%s"...' % (len(selected), target))

    def _download_by_key(self, key, target_parent_dir):
        self._set_busy(True)
        self.status_label.setText('Đang tải audiobook về máy...')
        self.download_thread = manage_worker.DownloadAudiobookThread(
            self.access_token, key, target_parent_dir, parent=self, library_name=self.library_name)
        self.download_thread.log.connect(self.log_view.appendPlainText)
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.finished_ok.connect(self._on_download_finished)
        self.download_thread.failed.connect(self._on_download_failed)
        self.download_thread.start()

    def _on_download_progress(self, done, total, name):
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(done)
        self.status_label.setText('Đang tải xuống... (%d/%d, %s)' % (done, total, name))

    def _on_download_finished(self, local_dir):
        self._set_busy(False)
        self.status_label.setText('Đã tải xuống: %s' % local_dir)
        QMessageBox.information(self, 'Google Drive Sync', 'Đã tải audiobook về:\n%s' % local_dir)

    def _on_download_failed(self, message):
        self._set_busy(False)
        self.status_label.setText('Tải xuống thất bại: %s' % message)
        QMessageBox.warning(self, 'Google Drive Sync', message)

    def _resync_by_key(self, key):
        """Nút "⟲ Đồng bộ từ Drive" trong Chi tiết Audiobook -- đọc lại
        metadata.opf + ảnh bìa chuẩn (bản tạo sớm nhất nếu có nhiều bản
        trùng tên do lỗi upload) từ đúng thư mục Drive của cuốn này rồi
        ghi đè lại metadata_public.json. Xem
        ``audiobook_sync/manage_ops.resync_book_from_drive``."""
        book = self._by_key.get(key)
        if not book:
            return
        reply = QMessageBox.question(
            self, 'Google Drive Sync',
            'Đồng bộ lại metadata + ảnh bìa của "%s" theo đúng file metadata.opf/cover '
            'đang có thật trên Google Drive (nếu có nhiều bản trùng tên do lỗi upload, '
            'lấy bản tạo sớm nhất làm chuẩn)?\n\nThao tác này sẽ GHI ĐÈ tiêu đề/tác giả/'
            'NXB/ngôn ngữ/mô tả/ảnh bìa hiện có trong metadata_public.json cho cuốn này.'
            % book.title,
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_action(
            manage_worker.ResyncBookThread(self.access_token, key, parent=self, library_name=self.library_name),
            'Đang đồng bộ từ Drive...')

    def _replace_cover_by_key(self, key, local_image_path):
        """Nút "🖼 Chọn ảnh thay thế" trong Chi tiết Audiobook -- upload
        ảnh local lên Drive, ưu tiên PATCH giữ nguyên file_id để không
        phải cập nhật metadata_public.json."""
        book = self._by_key.get(key)
        if not book:
            return
        if not local_image_path or not os.path.isfile(local_image_path):
            QMessageBox.warning(self, 'Google Drive Sync', 'File ảnh không hợp lệ.')
            return

        keep_id = bool(book.cover_file_id)
        if keep_id:
            msg = (
                'Thay ảnh bìa của "%s" bằng file:\n%s\n\n'
                'Ảnh sẽ được upload đè lên file bìa hiện tại trên Google Drive '
                '(giữ nguyên file_id -- không cần cập nhật metadata).'
                % (book.title, local_image_path)
            )
        else:
            msg = (
                'Cuốn "%s" chưa có ảnh bìa. Upload file:\n%s\n\n'
                'sẽ tạo file cover mới trên Drive và cập nhật metadata_public.json.'
                % (book.title, local_image_path)
            )
        reply = QMessageBox.question(
            self, 'Google Drive Sync', msg,
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_action(
            manage_worker.ReplaceCoverThread(
                self.access_token, key, local_image_path,
                parent=self, library_name=self.library_name),
            'Đang upload ảnh bìa...')

    def _replace_opf_by_key(self, key, local_opf_path):
        """Nút "📄 Chọn metadata.opf thay thế" -- upload OPF local lên Drive
        (giữ file_id nếu đã có), parse nội dung mới và cập nhật
        metadata_public.json. Sau khi xong, mở lại dialog Chi tiết để
        người dùng thấy thông tin vừa cập nhật."""
        book = self._by_key.get(key)
        if not book:
            return
        if not local_opf_path or not os.path.isfile(local_opf_path):
            QMessageBox.warning(self, 'Google Drive Sync', 'File OPF không hợp lệ.')
            return

        meta = (book.entry.get('metadata_opf') or {}) if hasattr(book, 'entry') else {}
        keep_id = bool(meta.get('file_id'))
        if keep_id:
            msg = (
                'Thay metadata.opf của "%s" bằng file:\n%s\n\n'
                'File sẽ được upload đè lên metadata.opf hiện tại trên Google Drive '
                '(giữ nguyên file_id). Sau đó cập nhật tiêu đề/tác giả/NXB/mô tả/'
                'chương... vào metadata_public.json (offline + Drive).'
                % (book.title, local_opf_path)
            )
        else:
            msg = (
                'Cuốn "%s" chưa có metadata.opf trên Drive. Upload file:\n%s\n\n'
                'sẽ tạo file mới và cập nhật metadata_public.json theo nội dung OPF.'
                % (book.title, local_opf_path)
            )
        reply = QMessageBox.question(
            self, 'Google Drive Sync', msg,
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._reopen_detail_key = key
        self._run_action(
            manage_worker.ReplaceOpfThread(
                self.access_token, key, local_opf_path,
                parent=self, library_name=self.library_name),
            'Đang upload metadata.opf...')

    def _delete_by_key(self, key):
        """Nút thùng rác trong Chi tiết Audiobook -- xóa đúng 1 cuốn, tái
        dùng cùng luồng xác nhận + ``purge_checkbox`` như nút "Xóa đã
        chọn" hàng loạt phía dưới."""
        book = self._by_key.get(key)
        if not book:
            return
        self.list_widget.clearSelection()
        item = self._items_by_key.get(key)
        if item:
            item.setSelected(True)
        self._delete_selected()

    def _delete_selected(self):
        selected = self._selected_books()
        if not selected:
            return
        purge = self.purge_checkbox.isChecked()
        if purge:
            reply = QMessageBox.question(
                self, 'Google Drive Sync',
                'Xóa VĨNH VIỄN %d audiobook đã chọn (cả metadata_public.json '
                'lẫn thư mục thật trên Google Drive)? Hành động này không '
                'thể hoàn tác.' % len(selected),
                QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No)
        else:
            reply = QMessageBox.question(
                self, 'Google Drive Sync',
                'Xóa %d audiobook đã chọn khỏi metadata_public.json? File '
                'thật trên Google Drive sẽ KHÔNG bị xóa.' % len(selected),
                QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        keys = [b.key for b in selected]
        self._run_action(
            manage_worker.DeleteAudiobooksThread(
                self.access_token, keys, purge, parent=self, library_name=self.library_name),
            'Đang xóa...')

    def _run_action(self, thread, status_text):
        self._set_busy(True)
        self.status_label.setText(status_text)
        self.action_thread = thread
        thread.log.connect(self.log_view.appendPlainText)
        thread.finished_ok.connect(self._on_action_finished)
        thread.failed.connect(self._on_action_failed)
        thread.start()

    def _on_action_finished(self):
        self.status_label.setText('Hoàn tất.')
        reopen_key = getattr(self, '_reopen_detail_key', None)
        self._reopen_detail_key = None
        self.reload()
        # Sau khi thay OPF: mở lại dialog Chi tiết với dữ liệu mới
        if reopen_key:
            # reload() chạy bất đồng bộ (list_thread) -- đợi list xong rồi mới mở
            self._pending_reopen_detail_key = reopen_key

    def _on_action_failed(self, message):
        self._reopen_detail_key = None
        self._pending_reopen_detail_key = None
        self._set_busy(False)
        self.status_label.setText('Thất bại: %s' % message)
        QMessageBox.warning(self, 'Google Drive Sync', message)

    # -- lifecycle ---------------------------------------------------------
    def _set_busy(self, busy):
        self.progress_bar.setMaximum(0 if busy else 1)
        self.progress_bar.setValue(0 if busy else 1)
        for w in (self.search_box, self.filter_combo, self.sort_combo, self.view_combo,
                  self.columns_spin, self.select_all_btn, self.select_none_btn,
                  self.refresh_btn, self.rename_library_btn, self.reselect_library_btn, self.bulk_move_combo,
                  self.list_widget):
            w.setEnabled(not busy)
        if busy:
            self.edit_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.bulk_move_btn.setEnabled(False)
        else:
            self._update_action_buttons()

    def _detach_cover_loader(self):
        """Stop cover loading as a best-effort background task.

        Loading thumbnails is useful but not important enough to block closing
        the manager. A Drive download may be inside a network timeout, so do not
        wait for the whole worker; detach it from this dialog and let it exit as
        soon as the current request returns.
        """
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
        for t in (self.list_thread, self.action_thread, self.download_thread):
            if t and t.isRunning():
                QMessageBox.information(
                    self, 'Google Drive Sync', 'Đang xử lý, vui lòng đợi.')
                return
        self._detach_cover_loader()
        QDialog.reject(self)

    def closeEvent(self, event):
        for t in (self.list_thread, self.action_thread, self.download_thread):
            if t and t.isRunning():
                QMessageBox.information(
                    self, 'Google Drive Sync', 'Đang xử lý, vui lòng đợi.')
                event.ignore()
                return
        self._detach_cover_loader()
        QDialog.closeEvent(self, event)

    def event(self, event):
        if maybe_show_help_from_event(self, event):
            return True
        return QDialog.event(self, event)
