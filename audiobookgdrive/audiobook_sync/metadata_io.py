# -*- coding: utf-8 -*-
"""
audiobook_sync/metadata_io.py
================================

Tải ``metadata_public.json`` HIỆN TẠI (ưu tiên bản thật trên Drive,
bản backup offline làm dự phòng) -- logic DÙNG CHUNG giữa
``checker.py`` ("Kiểm tra Audiobooks trên Drive...") và ``uploader.py``
(đồng bộ bình thường -- cần đọc lại payload cũ để không làm mất các
entry cũ không còn thư mục local tương ứng, xem
``uploader._merge_missing_books``).

Tách riêng module này (thay vì để nguyên trong ``checker.py`` như
trước) để tránh IMPORT VÒNG: ``checker.py`` vốn đã
``from .uploader import ...`` (dùng lại ``_upload_bytes_resumable``/
``_with_retry``), nên ``uploader.py`` không thể ``import checker``
ngược lại -- cả hai module nghiệp vụ giờ cùng phụ thuộc vào
``metadata_io.py`` (không phụ thuộc lẫn nhau).
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import json

from audiobookgdrive import drive_api

from . import backup

METADATA_JSON_NAME = 'metadata_public.json'


def download_metadata_json(access_token, state, log_fn=None, root_id=None):
    """Trả về ``(payload_dict, source)`` -- ``source`` là ``'drive'``
    hoặc ``'backup'`` để nơi gọi biết dữ liệu nền tảng để đối chiếu lấy
    từ đâu, hoặc ``(None, None)`` nếu không có nguồn nào dùng được.

    Ưu tiên:
      1. file_id đã cache trong state
      2. tìm ``metadata_public.json`` theo tên trong ``root_id`` (thư mục
         thư viện trên Drive) -- quan trọng khi import thư viện đã
         đồng bộ bởi app/plugin khác cùng tài khoản
      3. bản backup offline
    """
    file_id = state.get_metadata_json_id()
    if file_id:
        try:
            data = drive_api.download_file_bytes(access_token, file_id, timeout=180)
            return json.loads(data.decode('utf-8')), 'drive'
        except drive_api.DriveNotFoundError:
            if log_fn:
                log_fn('WARN', '%s trên Drive đã bị xóa (id cache) -- thử tìm lại theo tên.' % METADATA_JSON_NAME)
            state.set_metadata_json_id('')
        except Exception as e:
            if log_fn:
                log_fn('WARN', 'Không tải được %s từ Drive (%s) -- thử tìm lại theo tên / backup.'
                       % (METADATA_JSON_NAME, e))

    # Tìm theo tên trong thư mục thư viện (import dữ liệu cũ / cache mất)
    folder_id = root_id or state.get_root_folder_id()
    if folder_id:
        try:
            meta = drive_api.find_child_file(access_token, folder_id, METADATA_JSON_NAME)
            if meta and meta.get('id'):
                file_id = meta['id']
                data = drive_api.download_file_bytes(access_token, file_id, timeout=180)
                state.set_metadata_json_id(file_id)
                if log_fn:
                    log_fn('INFO', 'Đã tìm thấy %s trong thư mục thư viện trên Drive (id=%s).'
                           % (METADATA_JSON_NAME, file_id))
                return json.loads(data.decode('utf-8')), 'drive'
        except Exception as e:
            if log_fn:
                log_fn('WARN', 'Không tìm/tải được %s trong thư mục thư viện (%s).'
                       % (METADATA_JSON_NAME, e))

    library_name = getattr(state, 'library_name', None)
    data, backup_file_path = backup.load_backup(library_name=library_name)
    if data is None:
        return None, None
    try:
        return json.loads(data.decode('utf-8')), 'backup'
    except ValueError:
        if log_fn:
            log_fn('WARN', 'Bản backup offline tại %s bị hỏng, không đọc được.' % backup_file_path)
        return None, None
