# -*- coding: utf-8 -*-
"""
audiobookgdrive
================

AudiobookGdrive -- ứng dụng desktop độc lập (không phải plugin Calibre)
để đồng bộ, kiểm tra và quản lý thư viện sách nói (audiobook) trên
Google Drive của chính bạn, đồng thời ghép nối nhanh với app nghe sách
nói **Voice** trên Android qua link/mã QR.

Được tách ra từ nhánh "Audiobook Sync" của plugin Calibre "Calibre
Gdrive Sync" 2.7.7 -- xem ``README.md`` ở thư mục gốc project để biết
lịch sử/kiến trúc chi tiết.

Cấu trúc package:

* ``jsonconfig.py``   -- lưu trữ cấu hình/token/state dạng JSON (thay
  ``calibre.utils.config.JSONConfig`` của bản plugin gốc).
* ``oauth.py``         -- đăng nhập Google (OAuth2 + PKCE, loopback).
* ``drive_api.py``     -- gọi Google Drive API v3 (REST).
* ``hash_utils.py``    -- so sánh file đã đổi hay chưa (hash/size/mtime).
* ``config.py``        -- prefs + cửa sổ Cài đặt (Tài khoản/Audiobook
  Sync/Nâng cao).
* ``logger.py``        -- ghi log ra đĩa + ring buffer trong bộ nhớ.
* ``tray.py``/``bg_jobs.py`` -- icon khay hệ thống + "chạy dưới nền".
* ``audiobook_sync/``  -- toàn bộ nghiệp vụ Audiobook Sync (scan, upload,
  check, quản lý) -- KHÔNG phụ thuộc Qt, chỉ bị bọc QThread ở
  ``audiobook_sync/worker.py``/``manage_worker.py``.
* ``audiobook_dialog.py``, ``audiobook_check_dialog.py``,
  ``audiobook_manage_dialog.py``, ``audiobook_pairing_dialog.py``,
  ``pairing_dialog.py`` -- giao diện Qt (PyQt5) cho từng hành động.
* ``main_window.py``   -- cửa sổ chính, điều phối mọi hành động trên.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'
