# -*- coding: utf-8 -*-
"""
bg_jobs.py
==========

Thay thế cho ``bg_jobs.py`` gốc (vốn đưa 1 QThread đang chạy vào danh
sách "Jobs" của Calibre + hiện thông báo hoàn tất kiểu Calibre). Ứng
dụng độc lập này không có Jobs list, nên "Chạy dưới nền" ở đây có nghĩa
đơn giản hơn: ĐÓNG dialog tiến trình lại (thread vẫn tiếp tục chạy bên
dưới, không bị huỷ) và hiện 1 thông báo hệ thống (khay hệ thống/tray)
khi thread thực sự hoàn tất/thất bại -- xem ``tray.py``.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

from . import tray


def send_thread_to_background(parent, thread, task_description):
    """``thread`` đã được ``.start()`` từ trước (xem các dialog tiến
    trình trong ``audiobook_dialog.py``/``audiobook_check_dialog.py``).
    Ở đây chỉ cần gắn thêm 1 lần thông báo hệ thống khi thread hoàn tất,
    vì dialog sở hữu ``thread`` sắp bị đóng (``self.done(...)``) và các
    kết nối signal->slot cập nhật UI của nó sẽ không còn tác dụng."""
    tray.notify('AudiobookGdrive', '%s đang chạy dưới nền...' % task_description)

    def _on_ok(stats=None):
        tray.notify('AudiobookGdrive', '%s: hoàn tất.' % task_description)

    def _on_failed(message=''):
        tray.notify('AudiobookGdrive', '%s: thất bại (%s).' % (task_description, message), is_error=True)

    try:
        thread.finished_ok.connect(_on_ok)
    except AttributeError:
        pass
    try:
        thread.failed.connect(_on_failed)
    except AttributeError:
        pass
