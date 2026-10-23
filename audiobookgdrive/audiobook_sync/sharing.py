# -*- coding: utf-8 -*-
"""
audiobook_sync/sharing.py
============================

Chia sẻ công khai ("Anyone with the link") **chỉ** file
``metadata_public.json`` -- dùng bởi mục **"Thêm thư viện sách nói vào
Voice..."** (``main_window.show_audiobook_pairing_dialog``).

Vì sao chỉ share 1 file metadata, không share cả thư mục thư viện:

* Share cả folder gốc (hoặc từng thư mục cuốn) dễ bị Google từ chối /
  treo lâu khi thư viện do app khác tạo, hoặc có nhiều file app không
  có quyền ghi (OAuth scope ``drive.file``).
* App Voice chỉ cần **link/ID của ``metadata_public.json``** để đọc danh
  sách sách + link file bên trong; không cần folder thư viện phải public.
* Share 1 file nhỏ là thao tác nhanh, ổn định, tránh đơ ứng dụng.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from audiobookgdrive import drive_api


def share_metadata_public(access_token, metadata_file_id):
    """Đặt permission ``anyone`` / ``reader`` lên file ``metadata_public.json``.

    Trả về ``None`` nếu thành công.

    Raise ``drive_api.DriveApiError`` / ``DriveRetryableError`` nếu API
    từ chối (không có quyền, mất mạng, token hết hạn...) -- nơi gọi tự
    bọc try/except và hiện cảnh báo cho người dùng, vẫn cho mở dialog
    link/QR để họ có thể share thủ công trên drive.google.com nếu cần.
    """
    drive_api.create_public_permission(access_token, metadata_file_id)


# Giữ alias cũ để code gọi nhầm không gãy ngay (deprecated).
def share_audiobooks_public(access_token, root_id):
    """Deprecated: không còn share cả thư mục. Gọi
    :func:`share_metadata_public` với ID của ``metadata_public.json``."""
    raise RuntimeError(
        'share_audiobooks_public đã bỏ: chỉ share metadata_public.json '
        'qua share_metadata_public(access_token, metadata_file_id).')
