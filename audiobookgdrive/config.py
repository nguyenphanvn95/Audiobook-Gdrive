# -*- coding: utf-8 -*-
"""
config.py
=========

Cấu hình cho AudiobookGdrive -- hỗ trợ nhiều thư viện trên GDrive
(folder gốc, mặc định ``Audiobooks`` + các tên custom) và thư mục tải xuống.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QLabel,
    QLineEdit, QCheckBox, QComboBox, QSpinBox, QPushButton, QListWidget,
    QSizePolicy, QSize, QDialog, QDialogButtonBox,
)

from audiobookgdrive.jsonconfig import JSONConfig
from audiobookgdrive.help_utils import maybe_show_help_from_event

prefs = JSONConfig('audiobookgdrive_prefs')

_LABEL_MAX_WIDTH = 480
DEFAULT_LIBRARY_NAME = 'Audiobooks'


class _WrapLabel(QLabel):
    def __init__(self, text, max_width=_LABEL_MAX_WIDTH):
        QLabel.__init__(self, text)
        self._max_width = max_width
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def sizeHint(self):
        height = self.heightForWidth(self._max_width)
        if height <= 0:
            height = QLabel.sizeHint(self).height()
        return QSize(self._max_width, height)

    def minimumSizeHint(self):
        return self.sizeHint()


def _wrap_label(text, max_width=_LABEL_MAX_WIDTH):
    return _WrapLabel(text, max_width=max_width)


prefs.defaults['client_id'] = ''
prefs.defaults['client_secret'] = ''
prefs.defaults['max_retries'] = 3
prefs.defaults['retry_backoff_seconds'] = 5
prefs.defaults['chunk_size_mb'] = 8
prefs.defaults['audiobook_root_folders'] = []
prefs.defaults['audiobook_sync_direction'] = 'upload'
prefs.defaults['audiobook_download_folder'] = ''
prefs.defaults['audiobook_upload_workers'] = 3
prefs.defaults['audiobook_library_folders'] = [DEFAULT_LIBRARY_NAME]
prefs.defaults['audiobook_active_library'] = DEFAULT_LIBRARY_NAME
prefs.defaults['audiobook_library_cache'] = []
prefs.defaults['debug'] = True


def get_library_folders():
    libs = list(prefs.get('audiobook_library_folders') or [])
    if DEFAULT_LIBRARY_NAME not in libs:
        libs = [DEFAULT_LIBRARY_NAME] + libs
    seen = set()
    out = []
    for n in libs:
        n = (n or '').strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    if not out:
        out = [DEFAULT_LIBRARY_NAME]
    return out


def get_nonempty_library_names(include_configured=False, exclude_name=None):
    """Return cached audiobook libraries that currently contain at least 1 book."""
    seen = set()
    out = []
    exclude_name = (exclude_name or '').strip()

    def add(name):
        name = (name or '').strip()
        if not name or name == exclude_name or name in seen:
            return
        seen.add(name)
        out.append(name)

    for item in prefs.get('audiobook_library_cache') or []:
        if not isinstance(item, dict):
            continue
        if int(item.get('book_count') or 0) <= 0:
            continue
        add(item.get('name') or '')

    if include_configured and not out:
        for name in get_library_folders():
            add(name)
    return out


def cache_items_from_discovered_folders(folders, merge_existing=False):
    items = []
    seen = set()
    for f in folders or []:
        if not isinstance(f, dict):
            continue
        name = (f.get('name') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        items.append({
            'name': name,
            'id': f.get('id') or '',
            'has_metadata': bool(f.get('has_metadata')),
            'has_book_folder': bool(f.get('has_book_folder')),
            'metadata_file_id': f.get('metadata_file_id') or '',
            'book_count': int(f.get('book_count') or 0),
            'cover_file_id': f.get('cover_file_id') or '',
        })

    if merge_existing:
        for item in prefs.get('audiobook_library_cache') or []:
            if not isinstance(item, dict):
                continue
            name = (item.get('name') or '').strip()
            if name and name not in seen:
                seen.add(name)
                items.append(dict(item))

    items.sort(key=lambda x: (x.get('name') or '').lower())
    return items


def update_library_cache_from_folders(folders, merge_existing=False):
    items = cache_items_from_discovered_folders(folders, merge_existing=merge_existing)
    prefs['audiobook_library_cache'] = items
    names = [i['name'] for i in items if i.get('name')]
    if names:
        prefs['audiobook_library_folders'] = names
    return items


class ConfigWidget(QWidget):
    def __init__(self):
        QWidget.__init__(self)
        self.l = QVBoxLayout()
        self.setLayout(self.l)
        self.tabs = QTabWidget(self)
        self.l.addWidget(self.tabs)
        self._build_account_tab()
        self._build_audiobook_tab()
        self._build_advanced_tab()
        self.setMinimumWidth(560)
        self.setMaximumWidth(640)

    def _build_account_tab(self):
        tab = QWidget()
        form = QFormLayout()
        tab.setLayout(form)
        help_label = _wrap_label(
            '<b>Tài khoản Google Drive</b><br>'
            'Bấm "Đăng nhập Google" và đăng nhập bằng tài khoản Google của '
            'bạn -- ứng dụng chỉ đọc/ghi trong các thư mục thư viện nó tự '
            'tạo trên Drive của bạn (phạm vi drive.file), không cần bạn tự '
            'tạo Client ID/Secret. Nếu muốn dùng Google Cloud project '
            'riêng, khai báo ở tab Nâng cao.'
        )
        form.addRow(help_label)
        account_row = QHBoxLayout()
        self.account_status = QLabel()
        account_row.addWidget(self.account_status)
        account_row.addStretch()
        self.login_btn = QPushButton('Đăng nhập Google')
        self.login_btn.clicked.connect(self._login)
        account_row.addWidget(self.login_btn)
        self.logout_btn = QPushButton('Đăng xuất')
        self.logout_btn.clicked.connect(self._logout)
        account_row.addWidget(self.logout_btn)
        form.addRow('Tài khoản:', account_row)
        self._refresh_account_status()
        self.tabs.addTab(tab, 'Tài khoản')

    def _refresh_account_status(self):
        from audiobookgdrive import oauth
        if oauth.is_logged_in():
            self.account_status.setText('Đã đăng nhập: %s' % (oauth.get_account_email() or '(không rõ)'))
        elif not oauth.has_usable_client():
            self.account_status.setText(
                'Chưa đăng nhập (chưa có Client ID/Secret -- điền ở tab Nâng cao).')
        else:
            self.account_status.setText('Chưa đăng nhập')

    def _login(self):
        from audiobookgdrive import oauth
        from audiobookgdrive.login_dialog import run_login_flow
        client_id, client_secret = oauth.get_effective_credentials()
        if not client_id or not client_secret:
            self.account_status.setText(
                'Chưa có Client ID/Secret. Điền ở tab Nâng cao rồi thử lại.')
            return
        run_login_flow(self, client_id, client_secret)
        self._refresh_account_status()

    def _logout(self):
        from audiobookgdrive import oauth
        oauth.logout()
        self._refresh_account_status()

    def _build_audiobook_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        info = _wrap_label(
            'Các lựa chọn thư viện Google Drive và nguồn sách nói giờ nằm '
            'ngay trong cửa sổ Upload. Tab này chỉ giữ các tuỳ chọn chung '
            'cho quá trình đồng bộ.'
        )
        layout.addWidget(info)

        sync_form = QFormLayout()
        self.audiobook_sync_direction = QComboBox(self)
        self.audiobook_sync_direction.addItem('Chỉ upload (mặc định)', 'upload')
        self.audiobook_sync_direction.addItem('2 chiều (sẽ hỗ trợ ở bản sau)', 'two_way')
        self.audiobook_sync_direction.addItem('Chỉ download (sẽ hỗ trợ ở bản sau)', 'download')
        idx = self.audiobook_sync_direction.findData(prefs['audiobook_sync_direction'])
        self.audiobook_sync_direction.setCurrentIndex(idx if idx >= 0 else 0)
        sync_form.addRow('Tuỳ chọn đồng bộ:', self.audiobook_sync_direction)
        self.audiobook_upload_workers = QComboBox(self)
        for n in (1, 2, 3, 4, 6, 8):
            self.audiobook_upload_workers.addItem(str(n), n)
        idx = self.audiobook_upload_workers.findData(prefs['audiobook_upload_workers'])
        self.audiobook_upload_workers.setCurrentIndex(idx if idx >= 0 else 2)
        sync_form.addRow('Số file tải song song:', self.audiobook_upload_workers)
        layout.addLayout(sync_form)
        layout.addStretch()
        self.tabs.addTab(tab, 'Audiobook Sync')

    def _build_advanced_tab(self):
        tab = QWidget()
        form = QFormLayout()
        tab.setLayout(form)
        form.addRow(_wrap_label(
            '<b>Google Cloud OAuth Client riêng (tuỳ chọn)</b><br>'
            'Để trống để dùng client mặc định của ứng dụng. Chỉ điền nếu '
            'bạn muốn dùng quota API riêng của bạn.'))
        self.client_id = QLineEdit(self)
        self.client_id.setText(prefs['client_id'])
        self.client_id.setPlaceholderText('(dùng mặc định của ứng dụng)')
        form.addRow('Client ID:', self.client_id)
        self.client_secret = QLineEdit(self)
        self.client_secret.setText(prefs['client_secret'])
        self.client_secret.setEchoMode(QLineEdit.Password)
        self.client_secret.setPlaceholderText('(dùng mặc định của ứng dụng)')
        form.addRow('Client Secret:', self.client_secret)
        self.max_retries = QSpinBox(self)
        self.max_retries.setRange(0, 10)
        self.max_retries.setValue(prefs['max_retries'])
        form.addRow('Số lần thử lại tối đa:', self.max_retries)
        self.retry_backoff = QSpinBox(self)
        self.retry_backoff.setRange(1, 120)
        self.retry_backoff.setSuffix(' giây')
        self.retry_backoff.setValue(prefs['retry_backoff_seconds'])
        form.addRow('Thời gian chờ giữa các lần thử lại:', self.retry_backoff)
        self.chunk_size = QSpinBox(self)
        self.chunk_size.setRange(1, 512)
        self.chunk_size.setSuffix(' MB')
        self.chunk_size.setSingleStep(1)
        self.chunk_size.setValue(prefs['chunk_size_mb'])
        form.addRow('Kích thước chunk upload:', self.chunk_size)
        self.debug_checkbox = QCheckBox('Ghi log chi tiết (debug)', self)
        self.debug_checkbox.setChecked(prefs['debug'])
        form.addRow(self.debug_checkbox)
        self.tabs.addTab(tab, 'Nâng cao')

    def save_settings(self):
        prefs['client_id'] = self.client_id.text().strip()
        prefs['client_secret'] = self.client_secret.text().strip()
        prefs['max_retries'] = self.max_retries.value()
        prefs['retry_backoff_seconds'] = self.retry_backoff.value()
        prefs['chunk_size_mb'] = self.chunk_size.value()
        prefs['debug'] = self.debug_checkbox.isChecked()
        prefs['audiobook_sync_direction'] = self.audiobook_sync_direction.currentData()
        prefs['audiobook_upload_workers'] = self.audiobook_upload_workers.currentData()


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle('Cài đặt - AudiobookGdrive')
        layout = QVBoxLayout(self)
        self.widget = ConfigWidget()
        layout.addWidget(self.widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        self.widget.save_settings()
        self.accept()

    def event(self, event):
        if maybe_show_help_from_event(self, event):
            return True
        return QDialog.event(self, event)
