# -*- coding: utf-8 -*-
"""
tray.py
=======

Singleton QSystemTrayIcon dùng chung cho cả ứng dụng -- thay thế vai trò
của danh sách "Jobs" + thông báo hoàn tất trong Calibre (``bg_jobs.py``
gốc). ``main_window.py`` tạo icon này 1 lần khi ứng dụng khởi động qua
:func:`init_tray`; mọi nơi khác (``bg_jobs.py``) chỉ cần
:func:`get_tray_icon` để hiện thông báo, không cần biết chi tiết icon
được tạo ở đâu.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import QSystemTrayIcon, QMenu

_tray_icon = None


def init_tray(icon, app, main_window=None):
    """Tạo tray icon kèm menu chuột phải "Mở AudiobookGdrive" / "Thoát"
    (cần thiết vì :meth:`main_window.MainWindow.closeEvent` chỉ ẩn cửa
    sổ xuống khay thay vì thoát hẳn -- nếu không có menu này, người
    dùng sẽ không còn cách nào thoát ứng dụng ngoài Task Manager)."""
    global _tray_icon
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    _tray_icon = QSystemTrayIcon(icon, app)
    _tray_icon.setToolTip('AudiobookGdrive')

    if main_window is not None:
        menu = QMenu()
        show_action = menu.addAction('Mở AudiobookGdrive')
        show_action.triggered.connect(lambda: (main_window.showNormal(), main_window.raise_(), main_window.activateWindow()))
        menu.addSeparator()
        quit_action = menu.addAction('Thoát')
        quit_action.triggered.connect(app.quit)
        _tray_icon.setContextMenu(menu)
        _tray_icon.activated.connect(
            lambda reason: (main_window.showNormal(), main_window.raise_(), main_window.activateWindow())
            if reason == QSystemTrayIcon.DoubleClick else None)

    _tray_icon.show()
    return _tray_icon


def get_tray_icon():
    return _tray_icon


def notify(title, message, is_error=False, timeout_ms=8000):
    """Best-effort: hiện thông báo hệ thống nếu tray icon đã được khởi
    tạo và hệ điều hành hỗ trợ; im lặng bỏ qua nếu không (không bao giờ
    raise -- 1 thông báo lỗi không nên tự nó gây crash)."""
    if _tray_icon is None:
        return
    try:
        icon_type = QSystemTrayIcon.Critical if is_error else QSystemTrayIcon.Information
        _tray_icon.showMessage(title, message, icon_type, timeout_ms)
    except Exception:
        pass
