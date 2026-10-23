# -*- coding: utf-8 -*-
"""
audiobook_sync/uploader.py
=============================

Logic nghiệp vụ chính của Audiobook Sync -- KHÔNG phụ thuộc Qt (giống
``device_sync/runner.py``), để ``worker.py`` chỉ cần bọc nó trong 1
``QThread``. Tái sử dụng nguyên vẹn ``drive_api.py`` (folder/upload
resumable) và ``hash_utils.py`` (bỏ qua file không đổi theo
size+mtime+hash) đã có sẵn ở thư mục gốc plugin.

Chiều đồng bộ hiện tại: CHỈ "Upload only" (yêu cầu 2.4 -- 2 chiều/download
sẽ làm ở bản sau), bất kể ``prefs['audiobook_sync_direction']`` được đặt
là gì -- xem ghi chú trong ``config.py``.

Cấu trúc trên Drive::

    Audiobooks/                       <- 1 folder cố định ở gốc "My Drive"
      metadata_public.json            <- tổng hợp metadata thật từ mọi metadata.opf
      <Tên thư mục cuốn 1>/
        <file audio...>
        metadata.opf
        cover.jpg
      <Tên thư mục cuốn 2>/
        ...
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import concurrent.futures
import json
import threading
import time
from datetime import datetime
from os import path

from audiobookgdrive import drive_api
from audiobookgdrive.config import prefs
from audiobookgdrive.hash_utils import file_digest, file_fingerprint, unchanged

from . import backup, metadata_io, scanner
from .opf_parser import parse_opf_file
from .state_store import AudiobookState

ROOT_FOLDER_NAME = 'Audiobooks'  # mặc định; có thể override bằng library_name
METADATA_JSON_NAME = 'metadata_public.json'
DEFAULT_LIBRARY_NAME = 'Audiobooks'

# Nâng cấp 2.7.0: số file audio được tải lên SONG SONG cùng lúc (trước đó
# luôn là 1 -- tuần tự từng file). Mặc định thận trọng (3) vì mỗi luồng
# đã tự chia nhỏ chunk resumable riêng (xem ``_chunk_size``/``max_retries``
# trong Settings) -- quá nhiều luồng cùng lúc dễ bão hoà băng thông upload
# hoặc chạm giới hạn rate-limit của Drive API hơn là giúp nhanh hơn. Người
# dùng chỉnh được ở Settings -> tab "Audiobook Sync" (``audiobook_upload_workers``).
DEFAULT_UPLOAD_WORKERS = 3
MAX_UPLOAD_WORKERS = 8


class Cancelled(Exception):
    pass


def _chunk_size():
    size = max(256 * 1024, prefs['chunk_size_mb'] * 1024 * 1024)
    # Google yêu cầu kích thước chunk resumable phải là bội số của 256 KiB.
    return size - (size % (256 * 1024))


def _upload_worker_count():
    try:
        n = int(prefs['audiobook_upload_workers'])
    except (KeyError, TypeError, ValueError):
        n = DEFAULT_UPLOAD_WORKERS
    return max(1, min(MAX_UPLOAD_WORKERS, n))


def _with_retry(fn, log_fn, tag):
    max_retries = prefs['max_retries']
    backoff = prefs['retry_backoff_seconds']
    attempt = 0
    while True:
        try:
            return fn()
        except drive_api.DriveRetryableError as e:
            attempt += 1
            if attempt > max_retries:
                raise
            if log_fn:
                log_fn('WARN', '%s: lỗi tạm thời (%s), thử lại lần %d/%d...' % (tag, e, attempt, max_retries))
            time.sleep(backoff)


def _upload_file_resumable(access_token, file_path, name, parent_id, mime_type, existing_file_id,
                            check_abort=None):
    total_size = path.getsize(file_path)
    chunk_size = _chunk_size()
    session_uri = drive_api.start_resumable_session(
        access_token, name, parent_id, mime_type, total_size, existing_file_id=existing_file_id)

    sent = 0
    with open(file_path, 'rb') as f:
        while sent < total_size:
            if check_abort and check_abort():
                raise Cancelled()
            chunk = f.read(chunk_size)
            if not chunk:
                break
            try:
                done, file_id = drive_api.upload_chunk(session_uri, chunk, sent, total_size)
            except drive_api.DriveRetryableError:
                # Kết nối rớt giữa chừng: hỏi lại Google đã nhận tới đâu
                # rồi tiếp tục từ đó thay vì tải lại từ đầu.
                resume_from = drive_api.query_resumable_offset(session_uri, total_size)
                f.seek(resume_from)
                sent = resume_from
                continue
            sent += len(chunk)
            if done:
                return file_id
    if total_size == 0:
        _done, file_id = drive_api.upload_chunk(session_uri, b'', 0, 0)
        return file_id
    return None


def _upload_bytes_resumable(access_token, data_bytes, name, parent_id, mime_type, existing_file_id):
    total_size = len(data_bytes)
    chunk_size = _chunk_size()
    session_uri = drive_api.start_resumable_session(
        access_token, name, parent_id, mime_type, total_size, existing_file_id=existing_file_id)

    sent = 0
    file_id = existing_file_id
    while sent < total_size:
        chunk = data_bytes[sent:sent + chunk_size]
        done, returned_id = drive_api.upload_chunk(session_uri, chunk, sent, total_size)
        sent += len(chunk)
        if done:
            file_id = returned_id or file_id
    if total_size == 0:
        _done, file_id = drive_api.upload_chunk(session_uri, b'', 0, 0)
    return file_id


def _ensure_root_folder(access_token, state, library_name=None, log_fn=None):
    folder_name = (library_name or getattr(state, 'library_name', None) or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    root_id = state.get_root_folder_id()
    if root_id and not drive_api.file_exists(access_token, root_id):
        if log_fn:
            log_fn('WARN', 'Thư mục "%s" trên Drive không còn tồn tại (đã bị xoá thủ công?) '
                            '-- tạo lại và đồng bộ lại từ đầu.' % folder_name)
        state.reset_root()
        root_id = ''
    if not root_id:
        root_id = drive_api.find_or_create_folder(access_token, folder_name, 'root')
        state.set_root_folder_id(root_id)
        if log_fn:
            log_fn('INFO', 'Đã tạo/tìm thư mục thư viện "%s" trên Drive (id=%s).' % (folder_name, root_id))
    return root_id


def _resolve_book_folder(access_token, audiobooks_root_id, folder, state, log_fn):
    """Giai đoạn TUẦN TỰ (nhanh, rẻ) cho 1 cuốn: resolve/tạo folder trên
    Drive cho cuốn này + backfill ``added_at`` + dọn cache file đã đổi
    tên/xoá cục bộ. Chạy tuần tự (không song song) để tránh 2 luồng cùng
    lúc tạo trùng folder cho cùng 1 cuốn (``find_or_create_folder`` không
    tự khoá) -- các call Drive ở đây rẻ (1-2 lần/cuốn) nên tuần tự không
    phải là nút thắt cổ chai, khác hẳn việc TẢI FILE AUDIO (thường nặng
    hơn hẳn) đã được song song hoá ở :func:`run_upload_sync`.

    Trả về ``(book_folder_id, all_files)``."""
    key = folder.key
    cached = state.get_book(key) or {}
    book_folder_id = cached.get('drive_folder_id')

    # Local key có thể khác key trong metadata cũ (root_path khác máy/app)
    # -- map theo folder_name để tái sử dụng drive_folder_id + file cache.
    if not book_folder_id:
        alt_key = getattr(state, '_folder_name_index', {}).get(folder.name)
        if alt_key and alt_key != key:
            alt = state.get_book(alt_key) or {}
            if alt.get('drive_folder_id'):
                book_folder_id = alt['drive_folder_id']
                # Copy file cache sang key local mới
                alt_files = alt.get('files') or {}
                state.update_book(key, drive_folder_id=book_folder_id,
                                  root_path=folder.root_path, name=folder.name,
                                  added_at=alt.get('added_at'))
                for fname, finfo in alt_files.items():
                    if isinstance(finfo, dict) and finfo.get('file_id'):
                        state.update_file(key, fname, **{k: v for k, v in finfo.items()
                                                         if k in ('file_id', 'size', 'mtime', 'digest')})
                if log_fn:
                    log_fn('INFO', '"%s": khớp thư mục đã có trên Drive (từ metadata cũ, key khác root_path).'
                           % folder.name)

    if book_folder_id and not drive_api.file_exists(access_token, book_folder_id):
        book_folder_id = None

    if not book_folder_id:
        book_folder_id = _with_retry(
            lambda: drive_api.find_or_create_folder(access_token, folder.name, audiobooks_root_id),
            log_fn, 'folder:%s' % folder.name)
        state.update_book(key, drive_folder_id=book_folder_id, root_path=folder.root_path, name=folder.name)

    # Nâng cấp 2.6.0 ("Quản lý Audiobooks trên Drive..." -- sort theo "mới
    # thêm"): ghi lại mốc thời gian lần đầu tiên `key` này xuất hiện trong
    # state. Đặt ở ĐÂY (mỗi lần sync, không chỉ khi vừa tạo folder) để các
    # cuốn đã đồng bộ từ trước phiên bản này (chưa từng có `added_at`)
    # cũng tự được backfill ở lần đồng bộ kế tiếp, thay vì mãi mãi thiếu
    # trường này. Không bao giờ ghi đè nếu đã có sẵn.
    if not (state.get_book(key) or {}).get('added_at'):
        state.update_book(key, added_at=int(time.time() * 1000))

    all_files = folder.all_files()
    state.remove_stale_files(key, all_files)
    return book_folder_id, all_files


class _BookTracker(object):
    """Gom kết quả của nhiều file-job (chạy song song, có thể thuộc
    nhiều cuốn khác nhau xen kẽ nhau) lại theo TỪNG CUỐN, để giữ đúng
    hợp đồng ``progress_fn(done, total)`` đã có từ trước (tính theo SỐ
    CUỐN, không phải số file) dù việc tải file bên trong giờ chạy song
    song qua nhiều luồng. Thread-safe qua 1 ``threading.Lock`` chung --
    số lượng cuốn/file trong 1 lượt đồng bộ đủ nhỏ để không cần khoá mịn
    hơn.

    Bắt buộc gọi :meth:`init_book` cho MỌI cuốn TRƯỚC KHI bất kỳ file-job
    nào của cuốn đó được nộp vào thread pool -- ``run_upload_sync`` đảm
    bảo thứ tự này vì toàn bộ job được xây dựng xong (giai đoạn tuần tự)
    rồi mới nộp hàng loạt vào pool (giai đoạn song song)."""

    def __init__(self, keys, progress_fn):
        self._lock = threading.Lock()
        self._remaining = {}
        self._did_upload = {}
        self._errors = {}
        self._uploaded_count = 0
        self._done_books = 0
        self._total_books = len(keys)
        self._progress_fn = progress_fn

    def init_book(self, key, n_jobs):
        finished = False
        with self._lock:
            self._remaining[key] = n_jobs
            self._did_upload.setdefault(key, False)
            if n_jobs == 0:
                finished = True
                self._done_books += 1
                done, total = self._done_books, self._total_books
        if finished and self._progress_fn:
            self._progress_fn(done, total)

    def file_uploaded(self, key):
        with self._lock:
            self._did_upload[key] = True
            self._uploaded_count += 1
        self._finish_one(key)

    def file_errored(self, key, message):
        with self._lock:
            self._errors.setdefault(key, []).append(message)
        self._finish_one(key)

    def file_skipped(self, key):
        self._finish_one(key)

    def mark_prepare_failed(self, key, message):
        """Dùng cho lỗi xảy ra ở GIAI ĐOẠN 1 (tuần tự, resolve folder --
        trước khi cuốn này có bất kỳ file-job nào), khác với
        :meth:`file_errored` (lỗi khi đang tải 1 file cụ thể ở giai đoạn
        2) -- cuốn này coi như hoàn tất ngay (0 job) và được tính luôn
        vào ``done_books`` để mẫu số ``progress_fn(done, total)`` luôn
        khớp ``total`` = tổng số cuốn tìm thấy, kể cả những cuốn lỗi
        ngay từ bước resolve folder."""
        with self._lock:
            self._errors.setdefault(key, []).append(message)
            self._remaining[key] = 0
            self._did_upload.setdefault(key, False)
            self._done_books += 1
            done, total = self._done_books, self._total_books
        if self._progress_fn:
            self._progress_fn(done, total)

    def _finish_one(self, key):
        finished = False
        with self._lock:
            self._remaining[key] = self._remaining.get(key, 1) - 1
            if self._remaining[key] <= 0:
                finished = True
                self._done_books += 1
                done, total = self._done_books, self._total_books
        if finished and self._progress_fn:
            self._progress_fn(done, total)

    def did_upload(self, key):
        with self._lock:
            return self._did_upload.get(key, False)

    def errors(self, key):
        with self._lock:
            return list(self._errors.get(key, []))

    def total_uploaded(self):
        with self._lock:
            return self._uploaded_count


def _upload_one_file(access_token, folder, filename, book_folder_id, state, log_fn, check_abort, tracker):
    """1 "job" -- chạy trên 1 worker của ``ThreadPoolExecutor`` trong
    :func:`run_upload_sync`. KHÔNG raise ra ngoài (trừ ``Cancelled`` sẽ
    được nuốt lại thành ``file_skipped`` -- ``run_upload_sync`` tự kiểm
    tra ``check_abort()`` sau khi cả pool xong để quyết định có raise
    ``Cancelled`` lên trên hay không) -- mọi lỗi khác được gom vào
    ``tracker`` để cuốn tương ứng được tính là ``books_failed`` thay vì
    làm crash cả lượt đồng bộ."""
    key = folder.key
    if check_abort and check_abort():
        tracker.file_skipped(key)
        return

    file_path = path.join(folder.abs_path, filename)
    cached_book = state.get_book(key) or {}
    cached_file = (cached_book.get('files') or {}).get(filename)
    fp = file_fingerprint(file_path)
    existing_file_id = cached_file.get('file_id') if cached_file else None
    mime_type = drive_api.guess_mime_type(filename)

    try:
        file_id = _with_retry(
            lambda: _upload_file_resumable(
                access_token, file_path, filename, book_folder_id, mime_type,
                existing_file_id, check_abort=check_abort),
            log_fn, 'upload:%s/%s' % (folder.name, filename))
        digest = file_digest(file_path)
        state.update_file(key, filename, file_id=file_id, size=fp['size'], mtime=fp['mtime'], digest=digest)
        if log_fn:
            log_fn('INFO', '%s -> %s: đã tải lên.' % (folder.name, filename))
        tracker.file_uploaded(key)
    except Cancelled:
        tracker.file_skipped(key)
    except Exception as e:
        tracker.file_errored(key, str(e))
        if log_fn:
            log_fn('ERROR', '%s -> %s: lỗi khi tải lên (%s).' % (folder.name, filename, e))


def _build_book_metadata_entry(folder, state):
    key = folder.key
    cached = state.get_book(key) or {}
    files_cached = cached.get('files') or {}
    opf_data = parse_opf_file(folder.opf_path)

    audio_files = {}
    for filename in folder.other_files:
        entry = files_cached.get(filename) or {}
        audio_files[filename] = {'file_id': entry.get('file_id') or '', 'size': entry.get('size')}

    cover_entry = None
    if folder.cover_path:
        cname = path.basename(folder.cover_path)
        entry = files_cached.get(cname) or {}
        cover_entry = {'filename': cname, 'file_id': entry.get('file_id') or ''}

    opf_name = path.basename(folder.opf_path)
    opf_cached = files_cached.get(opf_name) or {}
    opf_entry = {'filename': opf_name, 'file_id': opf_cached.get('file_id') or ''}

    return {
        'folder_name': folder.name,
        'root_path': folder.root_path,
        'drive_folder_id': cached.get('drive_folder_id') or '',
        'added_at': cached.get('added_at') or 0,
        'title': opf_data.get('title'),
        'creators': opf_data.get('creators'),
        'publisher': opf_data.get('publisher'),
        'language': opf_data.get('language'),
        'description': opf_data.get('description'),
        'identifiers': opf_data.get('identifiers'),
        'modified': opf_data.get('modified'),
        'chapters': opf_data.get('chapters'),
        'extra_meta': opf_data.get('extra_meta'),
        'cover': cover_entry,
        'metadata_opf': opf_entry,
        'audio_files': audio_files,
    }


def _merge_missing_books(access_token, state, local_folders, books, log_fn=None):
    """Trước khi GHI ĐÈ hẳn ``metadata_public.json`` bằng danh sách
    cuốn build từ các thư mục LOCAL (``local_folders`` -- kết quả
    ``scanner.scan_all``), giữ lại MỌI entry cũ (bất kể ``origin``)
    không còn khớp 1 thư mục local nào -- ví dụ:

    * entry có ``origin == 'drive_manual'`` (được
      ``checker._add_missing_books_from_drive`` thêm vào -- yêu cầu
      nâng cấp "phát hiện + thêm thư mục sách upload thủ công trên
      Drive").
    * entry đồng bộ BÌNH THƯỜNG từ 1 thư mục local ở lần đồng bộ
      trước, nhưng thư mục đó giờ không còn nữa (đổi tên/xóa/ổ đĩa
      rời không cắm...) -- trước bản nâng cấp này, ``books`` chỉ được
      build lại từ ``local_folders`` HIỆN TẠI nên các entry dạng này
      sẽ bị XÓA MẤT khỏi ``metadata_public.json`` (dù file/thư mục
      thật trên Drive không hề bị đụng tới), khiến app đọc sách nói
      bên ngoài không còn thấy cuốn đó nữa dù vẫn tải nghe được bình
      thường qua link Drive trực tiếp. Yêu cầu nâng cấp: LUÔN bảo lưu
      các entry cũ này -- ``metadata_public.json`` chỉ được PHÉP thêm
      mới, không tự ý bớt đi entry nào chỉ vì thư mục local tương ứng
      biến mất (muốn dọn hẳn 1 entry mồ côi thật sự trên Drive, dùng
      "Kiểm tra Audiobooks trên Drive..." ở ``checker.py`` hoặc xóa thủ
      công qua "Quản lý Audiobooks trên Drive...").

    Các entry được giữ lại vì lý do thứ 2 (không có sẵn ``origin`` --
    tức vốn dĩ đồng bộ bình thường từ local) được đánh dấu
    ``origin = 'local_removed'`` để phân biệt với cuốn vẫn còn thư mục
    local (không có 'origin') và cuốn thêm thủ công trên Drive
    (``'drive_manual'``) -- thuần thông tin, không có logic nào khác
    trong plugin phụ thuộc giá trị này. ``origin`` đã có sẵn (kể cả đã
    là ``'local_removed'`` từ 1 lần giữ lại trước đó) không bao giờ bị
    ghi đè.

    Ghi thẳng vào ``books`` (in-place). Best-effort: không raise
    nếu không tải được payload cũ (coi như chưa có gì để giữ lại,
    không nên làm hỏng cả lượt đồng bộ chỉ vì bước bảo toàn dữ liệu
    phụ này lỗi). Trả về số cuốn đã giữ lại.
    """
    try:
        payload, _source = metadata_io.download_metadata_json(access_token, state, log_fn=None)
    except Exception:
        payload = None
    if not payload:
        return 0

    local_keys = {f.key for f in local_folders}
    kept = 0
    for key, entry in (payload.get('audiobooks') or {}).items():
        if key in local_keys or key in books:
            continue
        entry = dict(entry)
        entry.setdefault('origin', 'local_removed')
        books[key] = entry
        kept += 1
    if kept and log_fn:
        log_fn('INFO', 'Giữ lại %d cuốn cũ (không còn thư mục local tương ứng) khi cập nhật '
                        '%s -- entry cũ không bị xóa, chỉ thêm mới.' % (kept, METADATA_JSON_NAME))
    return kept


def _upload_metadata_json(access_token, audiobooks_root_id, state, folders, log_fn=None):
    books = {}
    for folder in folders:
        books[folder.key] = _build_book_metadata_entry(folder, state)

    _merge_missing_books(access_token, state, folders, books, log_fn=log_fn)

    payload = {
        'generated_at': datetime.now().isoformat(sep='T', timespec='seconds'),
        'source': 'google_drive_api',
        'drive_folder_url': 'https://drive.google.com/drive/folders/%s?usp=drive_link' % audiobooks_root_id,
        'audiobooks': books,
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')

    existing_id = state.get_metadata_json_id() or None
    file_id = _upload_bytes_resumable(
        access_token, data, METADATA_JSON_NAME, audiobooks_root_id, 'application/json', existing_id)
    state.set_metadata_json_id(file_id or '')
    # Yêu cầu 2.5.1 #1: luôn giữ 1 bản backup offline mới nhất trong thư
    # mục dữ liệu của plugin, ngay sau khi bản trên Drive đã upload
    # thành công (để 2 nơi luôn khớp nhau).
    library_name = getattr(state, 'library_name', None)
    backup.save_backup(data, log_fn, library_name=library_name)
    return file_id


def run_upload_sync(access_token, root_paths, log_fn=None, progress_fn=None, check_abort=None,
                    library_name=None, book_paths=None):
    """Blocking. ``log_fn(level, message)`` và ``progress_fn(done, total)``
    (tính theo SỐ CUỐN, không phải số file) được gọi từ NHIỀU luồng khác
    nhau kể từ bản 2.7.0 (giai đoạn tải file chạy song song qua
    ``ThreadPoolExecutor`` -- xem ``_BookTracker``) -- ``worker.AudiobookSyncThread``
    chuyển tiếp chúng thành Qt signal, việc này an toàn vì
    ``pyqtSignal.emit()`` có thể gọi từ bất kỳ luồng nào. Trả về dict
    thống kê.

    Chiến lược 2 giai đoạn (yêu cầu nâng cấp 2.7.0 -- "upload song song
    nhiều file audiobook 1 lúc"):

    1. TUẦN TỰ, rẻ: với mỗi cuốn, resolve/tạo folder trên Drive (giai
       đoạn duy nhất còn tuần tự -- tránh 2 luồng cùng tạo trùng 1
       folder) rồi xác định những file THẬT SỰ cần tải (bỏ qua file
       không đổi theo size+mtime+hash, y hệt logic cũ), gom thành 1
       hàng đợi job PHẲNG cho MỌI cuốn (không phải hàng đợi riêng từng
       cuốn) -- 1 cuốn có nhiều chương audio sẽ tự nhiên chiếm nhiều
       "chỗ" trong pool hơn 1 cuốn gộp thành 1 file, tối ưu băng thông
       tổng thể thay vì giới hạn song song trong phạm vi từng cuốn.
    2. SONG SONG: tải thật các file trong hàng đợi qua 1
       ``ThreadPoolExecutor`` dùng chung (số luồng cấu hình ở Settings
       -> tab "Audiobook Sync", mặc định 3), có retry/resume theo chunk
       y hệt trước (``_with_retry``/``_upload_file_resumable``), gom kết
       quả theo cuốn qua ``_BookTracker`` để tính đúng
       synced/skipped/failed và gọi ``progress_fn`` theo đúng số cuốn.
    """
    lib_name = (library_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    state = AudiobookState(library_name=lib_name)
    stats = {
        'uploaded_files': 0, 'books_synced': 0, 'books_skipped': 0,
        'books_failed': 0, 'failures': [],
        'library_name': lib_name,
    }

    root_paths = [p for p in (root_paths or []) if p]
    book_paths = [p for p in (book_paths or []) if p]
    if not root_paths and not book_paths:
        if log_fn:
            log_fn('WARN', 'Chưa chọn thư mục Audiobook nào để upload.')
        return stats

    if log_fn:
        log_fn('INFO', 'Thư viện đích trên Drive: "%s"' % lib_name)

    audiobooks_root_id = _with_retry(
        lambda: _ensure_root_folder(access_token, state, library_name=lib_name, log_fn=log_fn),
        log_fn, 'root-folder')

    # Nạp state từ metadata_public.json đã có (thư viện đồng bộ trước đó)
    # để khớp file_id / drive_folder_id theo folder_name, tránh upload trùng.
    try:
        from .metadata_io import download_metadata_json
        from .library_bind import import_state_from_payload, folder_name_index
        existing_payload, src = download_metadata_json(
            access_token, state, log_fn=None, root_id=audiobooks_root_id)
        if existing_payload:
            n = import_state_from_payload(state, existing_payload, log_fn=log_fn)
            if n and log_fn:
                log_fn('INFO', 'Đã liên kết %d cuốn từ metadata hiện có trên Drive (nguồn: %s).'
                       % (n, src or '?'))
            # Map folder_name -> entry key để resolve khi local root_path khác
            state._folder_name_index = folder_name_index(existing_payload)
    except Exception as e:
        if log_fn:
            log_fn('WARN', 'Không nạp được metadata hiện có trên Drive (%s) -- tiếp tục đồng bộ bình thường.' % e)

    if book_paths:
        folders = scanner.scan_book_folders(book_paths, log_fn=log_fn)
    else:
        folders = scanner.scan_all(root_paths, log_fn=log_fn)
    total = len(folders)
    if log_fn:
        if book_paths:
            log_fn('INFO', 'Tìm thấy %d audiobook trong %d thư mục sách đã chọn.'
                   % (total, len(book_paths)))
        else:
            log_fn('INFO', 'Tìm thấy %d audiobook trong %d thư mục gốc.' % (total, len(root_paths)))

    # Nếu không tìm thấy audiobook nào: báo lỗi và BỎ QUA hoàn toàn việc
    # ghi đè metadata_public.json (tránh xoá dữ liệu đã có trên Drive).
    if total == 0:
        if log_fn:
            log_fn('ERROR',
                   'Không tìm thấy audiobook nào trong các thư mục đã chọn '
                   '(cần thư mục con có metadata.opf). Bỏ qua đồng bộ, '
                   'không cập nhật %s để giữ nguyên dữ liệu hiện có.' % METADATA_JSON_NAME)
        return stats

    if check_abort and check_abort():
        if log_fn:
            log_fn('WARN', 'Đã huỷ theo yêu cầu.')
        return stats

    # -- Giai đoạn 1 (tuần tự) --------------------------------------
    tracker = _BookTracker([f.key for f in folders], progress_fn)
    prepare_failed_keys = set()
    jobs = []  # list[(folder, filename, book_folder_id)]
    for folder in folders:
        if check_abort and check_abort():
            if log_fn:
                log_fn('WARN', 'Đã huỷ theo yêu cầu.')
            return stats
        try:
            book_folder_id, all_files = _resolve_book_folder(
                access_token, audiobooks_root_id, folder, state, log_fn)
        except Exception as e:
            stats['books_failed'] += 1
            stats['failures'].append((folder.name, str(e)))
            prepare_failed_keys.add(folder.key)
            tracker.mark_prepare_failed(folder.key, str(e))
            if log_fn:
                log_fn('ERROR', 'Lỗi khi chuẩn bị thư mục cho "%s": %s' % (folder.name, e))
            continue

        book_jobs = []
        for filename in all_files:
            file_path = path.join(folder.abs_path, filename)
            cached_book = state.get_book(folder.key) or {}
            cached_file = (cached_book.get('files') or {}).get(filename)
            if unchanged(cached_file, file_path):
                continue
            book_jobs.append((folder, filename, book_folder_id))
        tracker.init_book(folder.key, len(book_jobs))
        jobs.extend(book_jobs)

    # -- Giai đoạn 2 (song song) --------------------------------------
    worker_count = _upload_worker_count()
    if jobs:
        if log_fn:
            log_fn('INFO', 'Sẽ tải lên %d file (song song %d luồng)...' % (len(jobs), worker_count))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(_upload_one_file, access_token, folder, filename, book_folder_id,
                            state, log_fn, check_abort, tracker)
                for folder, filename, book_folder_id in jobs
            ]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()  # _upload_one_file tự bắt hết lỗi -- không nên raise gì ở đây

    if check_abort and check_abort():
        if log_fn:
            log_fn('WARN', 'Đã huỷ theo yêu cầu.')
        raise Cancelled()

    # -- Tổng hợp kết quả theo từng cuốn (bỏ qua cuốn đã tính vào
    # books_failed ở giai đoạn 1, kẻo cộng trùng) ---------------------
    for folder in folders:
        key = folder.key
        if key in prepare_failed_keys:
            continue
        errors = tracker.errors(key)
        if errors:
            stats['books_failed'] += 1
            stats['failures'].append((folder.name, '; '.join(errors)))
            if log_fn:
                log_fn('ERROR', 'Lỗi khi đồng bộ "%s": %s' % (folder.name, '; '.join(errors)))
        elif tracker.did_upload(key):
            stats['books_synced'] += 1
        else:
            stats['books_skipped'] += 1
    stats['uploaded_files'] = tracker.total_uploaded()

    try:
        _with_retry(
            lambda: _upload_metadata_json(access_token, audiobooks_root_id, state, folders, log_fn=log_fn),
            log_fn, 'metadata_public.json')
        if log_fn:
            log_fn('INFO', 'Đã cập nhật %s.' % METADATA_JSON_NAME)
    except Cancelled:
        raise
    except Exception as e:
        if log_fn:
            log_fn('ERROR', 'Không thể cập nhật %s: %s' % (METADATA_JSON_NAME, e))

    return stats
