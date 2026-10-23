# -*- coding: utf-8 -*-
"""
main_window.py
================

Cửa sổ chính của AudiobookGdrive -- ứng dụng độc lập tách riêng từ nhánh
"Audiobook Sync" của plugin Calibre Gdrive Sync 2.7.7. Thay cho toolbar
dropdown trong Calibre (``ui.py`` gốc), đây là 1 cửa sổ Qt độc lập với
các nút bấm tương ứng 1-1 với từng hành động gốc:

* Đăng nhập / Đăng xuất Google
* Đồng bộ Audiobooks lên Google Drive (Upload)
* Kiểm tra Audiobooks trên Drive...
* Quản lý Audiobooks trên Drive...
* Mở thư mục Audiobooks trên Drive (trình duyệt)
* Thêm thư viện sách nói vào Voice... (ghép nối Android qua QR)
* Xem log
* Cài đặt...

Toàn bộ logic nghiệp vụ (OAuth, Drive API, upload, kiểm tra, quản lý,
chia sẻ) đều tái sử dụng NGUYÊN VẸN từ các module đã tách ra
(``audiobook_sync/``, ``oauth.py``, ``drive_api.py``, ...) -- file này
chỉ đóng vai trò tương đương ``ui.py`` gốc: điều phối UI, không chứa
logic Drive API nào của riêng nó.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QGroupBox, QFrame, QDesktopServices, QUrl, Qt, QIcon,
    QPixmap, QPainter, QColor, QSize, QAction, QToolBar, QDialog,
    QTextBrowser, QDialogButtonBox, QThread, pyqtSignal, QTimer,
)

from audiobookgdrive import oauth
from audiobookgdrive.config import prefs, SettingsDialog, update_library_cache_from_folders
from audiobookgdrive.logger import get_logger
from audiobookgdrive.log_dialog import LogDialog
from audiobookgdrive import tray

APP_TITLE = 'AudiobookGdrive'


class LibraryCacheRefreshThread(QThread):
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self):
        try:
            if not (oauth.has_usable_client() and oauth.is_logged_in()):
                self.finished_ok.emit([])
                return
            access_token = oauth.get_valid_access_token(*oauth.get_effective_credentials())
            from audiobookgdrive.audiobook_sync.library_bind import discover_library_folders
            self.finished_ok.emit(discover_library_folders(access_token))
        except Exception as e:
            self.failed.emit(str(e))


def _resource_path(*parts):
    """Đường dẫn tài nguyên (icon...) -- hoạt động cả khi chạy source
    lẫn khi đóng gói PyInstaller (``sys._MEIPASS``)."""
    import os
    import sys
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
        # Ưu tiên resources nằm cạnh exe / trong bundle
        candidates = [
            os.path.join(base, *parts),
            os.path.join(base, 'audiobookgdrive', *parts),
            os.path.join(os.path.dirname(sys.executable), *parts),
        ]
    else:
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(pkg_dir, *parts),
            os.path.join(pkg_dir, 'resources', *parts) if parts[0] != 'resources' else os.path.join(pkg_dir, *parts),
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


def _app_icon():
    """Icon ứng dụng: ưu tiên ``resources/icon.ico`` (đa kích thước, tốt
    cho taskbar Windows), rồi ``icon.png``; fallback vẽ chữ A nếu thiếu."""
    import os
    # .ico trước: Windows taskbar chọn size phù hợp (16/32/48/256) từ ICO
    for name in ('resources/icon.ico', 'resources/icon.png', 'icon.ico', 'icon.png'):
        path = _resource_path(*name.split('/'))
        if path and os.path.isfile(path):
            icon = QIcon(path)
            if not icon.isNull():
                # Ép thêm vài size phổ biến nếu chỉ load được 1 pixmap (PNG)
                if path.lower().endswith('.png'):
                    pm = icon.pixmap(256, 256)
                    if not pm.isNull():
                        for s in (16, 24, 32, 48, 64, 128, 256):
                            icon.addPixmap(pm.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return icon
    # Fallback vẽ bằng code
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#1a73e8'))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, size - 4, size - 4)
        painter.setPen(QColor('white'))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(28)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, 'A')
    finally:
        painter.end()
    return QIcon(pix)


def _precheck_signed_in(parent):
    """Trả về access_token hợp lệ, hoặc None (đã tự hiện thông báo lỗi
    phù hợp cho người dùng)."""
    if not (oauth.has_usable_client() and oauth.is_logged_in()):
        QMessageBox.information(
            parent, APP_TITLE,
            'Hãy đăng nhập Google Drive trước (nút "Đăng nhập Google" '
            'hoặc mở Cài đặt -> tab Tài khoản).')
        return None
    try:
        return oauth.get_valid_access_token(*oauth.get_effective_credentials())
    except oauth.OAuthError as e:
        QMessageBox.warning(parent, APP_TITLE, str(e))
        return None


class MainWindow(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.setWindowTitle(APP_TITLE)
        self.resize(520, 480)
        self.setWindowIcon(_app_icon())

        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # -- Account status -------------------------------------------
        account_box = QGroupBox('Tài khoản Google Drive')
        account_layout = QHBoxLayout(account_box)
        self.account_label = QLabel()
        account_layout.addWidget(self.account_label, stretch=1)
        self.login_btn = QPushButton('Đăng nhập Google')
        self.login_btn.clicked.connect(self.login)
        account_layout.addWidget(self.login_btn)
        self.logout_btn = QPushButton('Đăng xuất')
        self.logout_btn.clicked.connect(self.logout)
        account_layout.addWidget(self.logout_btn)
        outer.addWidget(account_box)

        # -- Audiobook actions ------------------------------------------
        actions_box = QGroupBox('Audiobook Sync')
        actions_layout = QVBoxLayout(actions_box)

        self.sync_btn = QPushButton('⬆  Đồng bộ Audiobooks lên Google Drive')
        self.sync_btn.clicked.connect(self.run_audiobook_sync)
        actions_layout.addWidget(self.sync_btn)

        self.check_btn = QPushButton('✓  Kiểm tra Audiobooks trên Drive...')
        self.check_btn.clicked.connect(self.run_audiobook_check)
        actions_layout.addWidget(self.check_btn)

        self.manage_btn = QPushButton('☰  Quản lý Audiobooks trên Drive...')
        self.manage_btn.clicked.connect(self.run_audiobook_manage)
        actions_layout.addWidget(self.manage_btn)

        self.open_drive_btn = QPushButton('🌐  Mở thư mục Audiobooks trên Drive')
        self.open_drive_btn.clicked.connect(self.open_audiobooks_on_drive)
        actions_layout.addWidget(self.open_drive_btn)

        outer.addWidget(actions_box)

        # -- Android pairing ----------------------------------------------
        pairing_box = QGroupBox('Ghép nối với ứng dụng nghe sách nói Voice (Android)')
        pairing_layout = QVBoxLayout(pairing_box)
        pairing_hint = QLabel(
            'Sau khi đã đồng bộ ít nhất 1 lần, bấm nút bên dưới để lấy link '
            '+ mã QR, dán/quét trong app Voice trên điện thoại để thêm thư '
            'viện sách nói.')
        pairing_hint.setWordWrap(True)
        pairing_layout.addWidget(pairing_hint)
        self.pairing_btn = QPushButton('📱  Thêm thư viện sách nói vào Voice...')
        self.pairing_btn.clicked.connect(self.show_audiobook_pairing_dialog)
        pairing_layout.addWidget(self.pairing_btn)
        outer.addWidget(pairing_box)

        outer.addStretch()

        self.status_bar = self.statusBar()
        self.status_bar.showMessage('Sẵn sàng.')

        self.logger = get_logger()
        self._library_cache_thread = None
        self._refresh_account_label()

        # -- Menu bar: Thao tác (các tính năng chính) + nút phải ---------
        self._build_menu_bar()
        self._quitting = False
        QTimer.singleShot(500, self._start_library_cache_refresh)

    def _build_menu_bar(self):
        """Menu 'Thao tác' bên trái; Cài đặt / Log / Trợ giúp bên phải."""
        menubar = self.menuBar()

        ops = menubar.addMenu('Thao tác')
        act_sync = ops.addAction('Đồng bộ Audiobooks lên Google Drive')
        act_sync.triggered.connect(self.run_audiobook_sync)
        act_check = ops.addAction('Kiểm tra Audiobooks trên Drive...')
        act_check.triggered.connect(self.run_audiobook_check)
        act_manage = ops.addAction('Quản lý Audiobooks trên Drive...')
        act_manage.triggered.connect(self.run_audiobook_manage)
        act_open = ops.addAction('Mở thư mục Audiobooks trên Drive')
        act_open.triggered.connect(self.open_audiobooks_on_drive)
        ops.addSeparator()
        act_share = ops.addAction('Chia sẻ / Thêm thư viện vào Voice...')
        act_share.triggered.connect(self.show_audiobook_pairing_dialog)
        ops.addSeparator()
        act_exit = ops.addAction('Thoát')
        act_exit.triggered.connect(self._force_quit)

        # Nút Cài đặt / Log / Trợ giúp căn phải trên thanh menu
        right = QWidget()
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 6, 0)
        right_layout.setSpacing(4)

        self.settings_btn = QPushButton('⚙  Cài đặt')
        self.settings_btn.setFlat(True)
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self.show_settings)
        right_layout.addWidget(self.settings_btn)

        self.log_btn = QPushButton('📋  Log')
        self.log_btn.setFlat(True)
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.clicked.connect(self.show_log)
        right_layout.addWidget(self.log_btn)

        self.help_btn = QPushButton('❓  Trợ giúp')
        self.help_btn.setFlat(True)
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.clicked.connect(self.show_help)
        right_layout.addWidget(self.help_btn)

        menubar.setCornerWidget(right, Qt.TopRightCorner)

    def _force_quit(self):
        from PyQt5.Qt import QApplication
        self._quitting = True
        QApplication.instance().quit()

    # -- Account -----------------------------------------------------------
    def _refresh_account_label(self):
        if oauth.is_logged_in():
            self.account_label.setText('Đã đăng nhập: %s' % (oauth.get_account_email() or '(không rõ)'))
            self.login_btn.setEnabled(False)
            self.logout_btn.setEnabled(True)
        else:
            self.account_label.setText('Chưa đăng nhập.')
            self.login_btn.setEnabled(True)
            self.logout_btn.setEnabled(False)

    def _start_library_cache_refresh(self):
        if self._library_cache_thread and self._library_cache_thread.isRunning():
            return
        if not (oauth.has_usable_client() and oauth.is_logged_in()):
            return
        self._library_cache_thread = LibraryCacheRefreshThread(self)
        self._library_cache_thread.finished_ok.connect(self._on_library_cache_refreshed)
        self._library_cache_thread.failed.connect(self._on_library_cache_refresh_failed)
        self._library_cache_thread.start()

    def _on_library_cache_refreshed(self, folders):
        if not folders:
            return
        items = update_library_cache_from_folders(folders)
        count = len([i for i in items if int(i.get('book_count') or 0) > 0])
        self.status_bar.showMessage('Đã cập nhật cache thư viện audiobook (%d thư viện có sách).' % count, 5000)

    def _on_library_cache_refresh_failed(self, message):
        self.logger.warn('Library cache refresh failed: %s' % message)

    def login(self):
        from audiobookgdrive.login_dialog import run_login_flow
        client_id, client_secret = oauth.get_effective_credentials()
        if not client_id or not client_secret:
            QMessageBox.warning(
                self, APP_TITLE,
                'Chưa có Client ID/Secret hợp lệ. Kiểm tra lại tab '
                'Nâng cao trong Cài đặt.')
            return
        run_login_flow(self, client_id, client_secret)
        self._refresh_account_label()
        self._start_library_cache_refresh()

    def logout(self):
        oauth.logout()
        self._refresh_account_label()

    # -- Audiobook Sync ------------------------------------------------
    def run_audiobook_sync(self):
        from audiobookgdrive.audiobook_dialog import AudiobookSyncProgressDialog

        root_folders = [p for p in (prefs['audiobook_root_folders'] or []) if p]

        access_token = _precheck_signed_in(self)
        if not access_token:
            return
        dialog = AudiobookSyncProgressDialog(
            self, access_token, root_folders, logger=self.logger,
            library_name=prefs.get('audiobook_active_library'))
        dialog.exec_()
        self.status_bar.showMessage('Đã mở Upload Audiobooks.', 5000)

    def open_audiobooks_on_drive(self):
        from audiobookgdrive.audiobook_sync.state_store import AudiobookState
        from audiobookgdrive.library_picker_dialog import LibraryPickerDialog

        access_token = _precheck_signed_in(self)
        if not access_token:
            return

        library_name = LibraryPickerDialog.pick(
            self, title='Chọn thư viện để mở',
            message='Chọn thư mục thư viện audiobook trên Google Drive để mở:',
            force_show=True, access_token=access_token, load_from_drive=True)
        if not library_name:
            return
        # Ưu tiên id đã cache trong state (không gọi mạng)
        state = AudiobookState(library_name=library_name)
        root_id = state.get_root_folder_id()
        if not root_id:
            # Gọi mạng ngắn: chỉ tìm folder theo tên (không tải metadata 4MB)
            self.status_bar.showMessage('Đang tìm thư mục "%s" trên Drive...' % library_name)
            from audiobookgdrive.audiobook_sync.library_bind import discover_library_root
            from PyQt5.Qt import QApplication
            QApplication.processEvents()
            root_id = discover_library_root(access_token, library_name, state, log_fn=None)
            self.status_bar.clearMessage()
        if not root_id:
            QMessageBox.information(
                self, APP_TITLE,
                'Không tìm thấy thư mục "%s" trên Drive. Hãy chạy "Kiểm tra" trước.' % library_name)
            return
        QDesktopServices.openUrl(QUrl('https://drive.google.com/drive/folders/%s' % root_id))

    def run_audiobook_check(self):
        from audiobookgdrive.audiobook_check_dialog import AudiobookCheckProgressDialog
        from audiobookgdrive.library_picker_dialog import LibraryPickerDialog

        access_token = _precheck_signed_in(self)
        if not access_token:
            return

        library_name = LibraryPickerDialog.pick(
            self, title='Chọn thư viện để kiểm tra',
            message='Chọn thư mục thư viện trên Google Drive để kiểm tra '
                    '(danh sách được tải từ các thư mục audiobook hợp lệ trên Drive):',
            force_show=True, access_token=access_token, load_from_drive=True)
        if not library_name:
            return

        # Không chặn khi state local trống -- checker sẽ tự tìm thư mục
        # + metadata_public.json trên Drive và nạp vào state.
        dialog = AudiobookCheckProgressDialog(
            self, access_token, logger=self.logger, library_name=library_name)
        dialog.exec_()
        self.status_bar.showMessage('Đã chạy Kiểm tra [%s].' % library_name, 5000)

    def run_audiobook_manage(self):
        from audiobookgdrive.audiobook_manage_dialog import AudiobookLibraryManageDialog
        from audiobookgdrive.library_picker_dialog import LibraryPickerDialog

        access_token = _precheck_signed_in(self)
        if not access_token:
            return

        library_name = LibraryPickerDialog.pick(
            self, title='Chọn thư viện để quản lý',
            message='Chọn thư mục thư viện audiobook hợp lệ trên Google Drive để quản lý:',
            force_show=True, access_token=access_token, load_from_drive=True)
        if not library_name:
            return

        # Không gọi Drive API trên UI thread -- dialog tự tải danh sách
        # qua ListAudiobooksThread (manage_ops._load sẽ discover + tải json).
        dialog = AudiobookLibraryManageDialog(self, access_token, library_name=library_name)
        result = dialog.exec_()
        if result == AudiobookLibraryManageDialog.RESELECT_LIBRARY_CODE:
            self.run_audiobook_manage()

    def show_audiobook_pairing_dialog(self):
        from audiobookgdrive.audiobook_pairing_dialog import AudiobookPairingDialog
        from audiobookgdrive.audiobook_sync.state_store import AudiobookState
        from audiobookgdrive.audiobook_sync import sharing
        from audiobookgdrive.library_picker_dialog import LibraryPickerDialog

        access_token = _precheck_signed_in(self)
        if not access_token:
            return

        library_name = LibraryPickerDialog.pick(
            self, title='Chọn thư viện để thêm vào Voice',
            message='Chọn thư mục thư viện trên Google Drive để ghép nối app Voice:',
            force_show=True, access_token=access_token, load_from_drive=True)
        if not library_name:
            return

        state = AudiobookState(library_name=library_name)
        file_id = state.get_metadata_json_id()
        root_id = state.get_root_folder_id()
        # Nếu chưa có cache: tìm folder + file metadata theo tên (không parse toàn bộ json)
        if not file_id or not root_id:
            from audiobookgdrive.audiobook_sync.library_bind import discover_library_root
            from audiobookgdrive import drive_api
            from audiobookgdrive.audiobook_sync.metadata_io import METADATA_JSON_NAME
            from PyQt5.Qt import QApplication
            self.status_bar.showMessage('Đang tìm metadata trên Drive...')
            QApplication.processEvents()
            if not root_id:
                root_id = discover_library_root(access_token, library_name, state, log_fn=None)
            if root_id and not file_id:
                try:
                    meta = drive_api.find_child_file(access_token, root_id, METADATA_JSON_NAME)
                    if meta and meta.get('id'):
                        file_id = meta['id']
                        state.set_metadata_json_id(file_id)
                except Exception:
                    pass
            self.status_bar.clearMessage()
        if not file_id:
            QMessageBox.information(
                self, APP_TITLE,
                'Chưa tìm thấy metadata_public.json trong thư viện "%s". '
                'Hãy chạy "Kiểm tra" hoặc "Đồng bộ" trước.' % library_name)
            return

        # Chỉ share công khai FILE metadata_public.json (không share cả
        # thư mục thư viện -- tránh đơ/403 khi thư viện do app khác tạo
        # hoặc có nhiều file app không có quyền ghi).
        share_warning = None
        try:
            self.status_bar.showMessage('Đang chia sẻ công khai metadata_public.json...')
            from PyQt5.Qt import QApplication
            QApplication.processEvents()
            sharing.share_metadata_public(access_token, file_id)
        except Exception as e:
            share_warning = (
                'Không tự chia sẻ công khai được file metadata_public.json (%s). '
                'Nếu app Voice không mở được link, hãy vào drive.google.com, '
                'mở file metadata_public.json trong thư viện "%s" -> Share -> '
                '"Anyone with the link".'
                % (e, library_name))
        finally:
            self.status_bar.clearMessage()

        book_count = len(state.all_keys())
        dialog = AudiobookPairingDialog(file_id, book_count, share_warning=share_warning, parent=self)
        dialog.exec_()

    # -- Settings / logs -------------------------------------------------
    def show_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec_():
            self._refresh_account_label()

    def show_log(self):
        dialog = LogDialog(self, active_logger=self.logger)
        dialog.exec_()

    def show_help(self):
        dialog = HelpDialog(self)
        dialog.exec_()

    def closeEvent(self, event):
        """Nhấn nút [X] chỉ ẩn cửa sổ xuống khay hệ thống (để các tác vụ
        đang chạy không bị huỷ giữa chừng) -- muốn thoát hẳn, dùng menu
        Thao tác -> Thoát, hoặc chuột phải icon khay -> Thoát."""
        if self._quitting or tray.get_tray_icon() is None:
            event.accept()
            return
        tray.notify(APP_TITLE, 'Đã thu nhỏ xuống khay hệ thống. Nhấp đúp icon để mở lại, '
                                'hoặc chuột phải icon -> Thoát để đóng hẳn.')
        self.hide()
        event.ignore()


class HelpDialog(QDialog):
    """Cửa sổ Trợ giúp -- FAQ ngắn về cách dùng AudiobookGdrive."""

    def __init__(self, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle('Trợ giúp — AudiobookGdrive')
        self.resize(560, 480)
        self.setWindowIcon(_app_icon())

        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_HELP_HTML)
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)


_HELP_HTML = """
<style>
  body { font-family: Segoe UI, sans-serif; font-size: 13px; color: #222; }
  h2 { color: #1a73e8; margin-top: 18px; margin-bottom: 6px; font-size: 15px; }
  h2:first-child { margin-top: 4px; }
  p, li { line-height: 1.45; }
  code { background: #f1f3f4; padding: 1px 4px; border-radius: 3px; }
</style>

<h2>1. Ứng dụng này có tác dụng gì?</h2>
<p><b>AudiobookGdrive</b> giúp bạn đồng bộ thư viện <b>sách nói (audiobook)</b>
từ máy tính lên <b>Google Drive</b>, kiểm tra / quản lý các cuốn đã có trên Drive,
và ghép nối nhanh với app nghe sách nói <b>Voice</b> trên Android qua link hoặc mã QR.</p>
<p>Ứng dụng chạy độc lập trên Windows (không cần Calibre), chỉ xin quyền
<code>drive.file</code> — chỉ đọc/ghi file và thư mục do chính nó tạo ra.</p>

<h2>2. Làm sao đăng nhập Google?</h2>
<p>Bấm <b>Đăng nhập Google</b> trên cửa sổ chính. Trình duyệt sẽ mở trang xác nhận
của Google — chọn tài khoản và cho phép. Sau khi thấy đăng nhập thành công,
quay lại ứng dụng; dòng trạng thái tài khoản sẽ cập nhật.</p>
<p>Nếu gặp màn hình "Google hasn't verified this app", chọn
<b>Advanced / Nâng cao</b> → <b>Go to AudiobookGdrive (unsafe)</b> để tiếp tục
(bình thường với app desktop dùng OAuth cá nhân).</p>

<h2>3. Đồng bộ sách nói lên Drive như thế nào?</h2>
<ol>
  <li>Vào <b>Cài đặt → tab Audiobook Sync</b>, thêm thư mục gốc chứa các cuốn
      sách nói (mỗi thư mục con = 1 cuốn, nên có <code>metadata.opf</code>).</li>
  <li>Bấm <b>Đồng bộ Audiobooks lên Google Drive</b> (hoặc menu
      <b>Thao tác → Đồng bộ...</b>).</li>
  <li>Chọn tên thư viện trên Drive (ví dụ <code>Audiobooks</code>).</li>
</ol>
<p>App chỉ tải file mới/đổi; file không đổi sẽ được bỏ qua.</p>

<h2>4. Kiểm tra / Quản lý trên Drive để làm gì?</h2>
<ul>
  <li><b>Kiểm tra</b>: đối chiếu <code>metadata_public.json</code> với thực tế trên Drive,
      sửa id thiếu/mồ côi, phát hiện cuốn không có audio hoặc cuốn tải thủ công.</li>
  <li><b>Quản lý</b>: xem lưới bìa, tìm kiếm, sửa metadata, xóa cuốn
      (có thể kèm xóa file trên Drive).</li>
</ul>

<h2>5. Thêm thư viện vào app Voice (Android) thế nào?</h2>
<ol>
  <li>Đồng bộ hoặc Kiểm tra ít nhất 1 lần để có file
      <code>metadata_public.json</code> trên Drive.</li>
  <li>Bấm <b>Thêm thư viện sách nói vào Voice...</b>
      (hoặc <b>Thao tác → Chia sẻ / Thêm thư viện vào Voice...</b>).</li>
  <li>Chọn thư viện — app chỉ chia sẻ <b>công khai file metadata_public.json</b>
      (không share cả thư mục), rồi hiện link + mã QR.</li>
  <li>Trên điện thoại, mở Voice → thêm thư viện → quét QR hoặc dán link/ID.</li>
</ol>

<h2>6. Nút [X] có thoát hẳn không?</h2>
<p>Không. Nút đóng cửa sổ chỉ <b>ẩn xuống khay hệ thống</b> để tác vụ nền
không bị cắt. Muốn thoát hẳn: menu <b>Thao tác → Thoát</b>, hoặc chuột phải
icon khay → <b>Thoát</b>.</p>

<h2>7. Xem log / Cài đặt ở đâu?</h2>
<p>Hai nút nằm góc phải thanh menu trên cùng:
<b>Cài đặt</b> và <b>Log</b>. Log ghi chi tiết các lần đồng bộ, kiểm tra,
lỗi mạng/API để dễ chẩn đoán.</p>
"""
