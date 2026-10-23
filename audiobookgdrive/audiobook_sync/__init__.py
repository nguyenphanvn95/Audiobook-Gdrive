# -*- coding: utf-8 -*-
"""
audiobook_sync/
================

"Audiobook Sync" -- nhánh tính năng MỚI (v2.5.0), tách biệt hoàn toàn với
"Calibre Library" (sách trong metadata.db) lẫn "Device Library Sync
(Android)" (kho dữ liệu của app Calibre Sync): đồng bộ các thư mục audiobook
nằm ngay trên ổ đĩa máy tính (không phải sách trong thư viện Calibre) lên
Google Drive.

Quan hệ với các nhánh khác trong plugin:

* Dùng CHUNG tài khoản/token Google Drive (``oauth`` flow ``'library'``,
  scope ``drive.file``) với phần đồng bộ "Calibre Library" ở các tab
  Account/Sync/Metadata -- KHÔNG dùng chung với "Device Library Sync
  (Android)" (flow ``'device_sync'``, scope đầy đủ).
* Dùng lại nguyên vẹn ``drive_api.py`` (folder, upload resumable/simple),
  ``hash_utils.py`` (bỏ qua file không đổi theo size+mtime+hash) và
  ``bg_jobs.py`` (chạy dưới nền qua Jobs list của Calibre) ở thư mục gốc
  plugin -- không viết lại các phần đó.
* Có ``state_store.py``/``metadata_public.json``/schema JSON riêng (không
  đụng tới ``state_store.py``/``metadata_public.json`` của "Calibre
  Library"), vì đơn vị đồng bộ ở đây là "thư mục audiobook trên đĩa", không
  phải "book_id trong metadata.db".

Cấu trúc module:

* ``scanner.py``   -- duyệt các Audiobook Root Folder cấu hình trong
  Settings để tìm các thư mục con là audiobook (chứa ``metadata.opf``).
* ``opf_parser.py`` -- đọc (không phải ghi) file ``metadata.opf`` THẬT sự
  nằm sẵn trong mỗi thư mục audiobook, giữ nguyên mọi trường (kể cả các
  thẻ ``<meta property="...">`` tuỳ biến như ``voiz:chapter``) để gộp vào
  ``metadata_public.json`` tổng hợp.
* ``state_store.py`` -- bookkeeping folder id/file id/hash trên Drive,
  khoá theo ``<root_path>|<tên thư mục>``.
* ``uploader.py``  -- logic upload chính (blocking, không phụ thuộc Qt).
* ``worker.py``    -- bọc ``uploader.py`` trong 1 ``QThread``.

Giới hạn hiện tại (theo đúng yêu cầu nâng cấp): CHỈ hỗ trợ chiều "Upload
only". Lựa chọn "2 chiều" / "Chỉ download" đã có sẵn trong Settings
(``prefs['audiobook_sync_direction']``) và thư mục tải xuống thủ công
(``prefs['audiobook_download_folder']``) đã có ô nhập, nhưng logic
download/2-chiều thực sự sẽ làm ở bản sau -- xem CHANGELOG.md 2.5.0.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'
