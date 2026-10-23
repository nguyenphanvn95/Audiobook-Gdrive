# -*- coding: utf-8 -*-
"""
audiobook_sync/manage_ops.py
==============================

Nghiệp vụ đứng sau **"Quản lý Audiobooks trên Drive..."** (nâng cấp
2.6.0) -- đối ứng với ``device_sync/manage_ops.py`` (quản lý Device
Library), nhưng thao tác trên nguồn dữ liệu khác hẳn:

* Device Library quản lý qua ``calibre_sync_manifest.json``, xoá bằng
  tombstone (``deletedAt``), vì điện thoại cần thấy tombstone để tự gỡ
  file đã tải.
* Audiobook Sync là *một chiều* (chỉ upload từ máy tính lên Drive) và
  app đọc sách nói bên ngoài chỉ dựa vào **``metadata_public.json``**
  (xem ``uploader.py``) để biết audiobook nào tồn tại -- không có khái
  niệm tombstone/đồng bộ 2 chiều nào cần giữ lại. Vì vậy ở đây "xoá" đơn
  giản là loại hẳn entry khỏi ``audiobooks`` dict trong json (đủ để app
  đọc sách nói ngừng hiển thị cuốn đó), kèm tuỳ chọn xoá luôn thư mục
  thật trên Drive để giải phóng dung lượng.

Mọi thao tác ghi (sửa/xoá) đều TẢI LẠI ``metadata_public.json`` mới
nhất (ưu tiên từ Drive, dự phòng bản backup offline -- tái dùng
``checker.download_metadata_json``) ngay trước khi ghi đè, để giảm rủi
ro ghi đè mất dữ liệu nếu vừa có 1 lượt "Đồng bộ Audiobooks lên Google
Drive" khác chạy xong trong lúc dialog quản lý đang mở.

Không đụng Qt ở đây -- chạy trên background QThread qua
``manage_worker.py``, đúng nguyên tắc xuyên suốt plugin này (không bao
giờ gọi mạng trên GUI thread).
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime

from audiobookgdrive import drive_api

from . import backup, checker, metadata_io
from .opf_parser import parse_opf_file
from .scanner import COVER_NAMES, OPF_NAME
from .state_store import AudiobookState
from .uploader import METADATA_JSON_NAME, _upload_bytes_resumable, _with_retry


class ManageError(Exception):
    pass


def _normalize_log_fn(log_fn):
    """Chuẩn hoá log_fn để chấp nhận cả 1 đối số (msg) lẫn 2 đối số
    (level, msg) như ``uploader._with_retry`` / ``backup`` đôi khi gọi.

    Worker GUI thường truyền ``lambda msg: signal.emit(msg)`` -- nếu
    không bọc lại, lần gọi ``log_fn('WARN', '...')`` sẽ ném
    ``TypeError: takes 1 positional argument but 2 were given``.
    """
    if log_fn is None:
        return lambda *args: None

    def _wrapped(*args):
        if not args:
            return
        if len(args) == 1:
            log_fn(args[0])
        else:
            # level, msg[, ...] -- ghép lại 1 dòng cho UI log
            level = str(args[0]).strip()
            msg = args[1] if len(args) > 1 else ''
            if level and level.upper() in ('WARN', 'WARNING', 'ERROR', 'INFO', 'DEBUG'):
                log_fn('%s: %s' % (level.upper(), msg) if msg else level)
            else:
                log_fn(' '.join(str(a) for a in args if a is not None))

    return _wrapped


class ManagedAudiobook(object):
    """View nhẹ quanh 1 entry thô (dict) trong ``audiobooks`` của
    ``metadata_public.json``, không copy dữ liệu. ``key`` là khoá
    ``<root_path>|<ten_thu_muc>`` (``scanner.AudiobookFolder.key``)."""

    def __init__(self, key, entry):
        self.key = key
        self.entry = entry or {}

    @property
    def title(self):
        return (self.entry.get('title') or '').strip() or self.folder_name or self.key

    @property
    def folder_name(self):
        return self.entry.get('folder_name') or ''

    @property
    def creators(self):
        return [c.get('name') for c in (self.entry.get('creators') or []) if c.get('name')]

    @property
    def creators_display(self):
        return ', '.join(self.creators) if self.creators else 'Không rõ tác giả'

    @property
    def publisher(self):
        return self.entry.get('publisher') or ''

    @property
    def language(self):
        return self.entry.get('language') or ''

    @property
    def description(self):
        return self.entry.get('description') or ''

    @property
    def chapters(self):
        return self.entry.get('chapters') or []

    @property
    def chapter_count(self):
        return len(self.chapters)

    @property
    def has_cover(self):
        cover = self.entry.get('cover')
        return bool(cover and cover.get('file_id'))

    @property
    def cover_file_id(self):
        cover = self.entry.get('cover')
        return cover.get('file_id') if cover else None

    @property
    def drive_folder_id(self):
        return self.entry.get('drive_folder_id') or ''

    @property
    def added_at(self):
        return self.entry.get('added_at') or 0

    @property
    def size_bytes(self):
        total = 0
        for finfo in (self.entry.get('audio_files') or {}).values():
            total += (finfo or {}).get('size') or 0
        return total

    @property
    def audio_file_count(self):
        """Số file audio ghi nhận trong ``metadata_public.json`` cho cuốn
        này. 0 nghĩa là cuốn "rỗng ruột" -- thường do 1 lượt "Đồng bộ
        Audiobooks lên Google Drive" bị ngắt giữa chừng ngay sau khi tạo
        xong thư mục + tải xong metadata.opf/cover nhưng chưa kịp tải
        audio nào (xem ``audiobook_sync/checker.py``, mục phát hiện
        "không có audio")."""
        return len(self.entry.get('audio_files') or {})

    @property
    def has_audio(self):
        return self.audio_file_count > 0


def _load(access_token, log_fn=None, library_name=None):
    """Trả về ``(state, root_id, payload, source)``. Raise
    :class:`ManageError` nếu chưa từng đồng bộ Audiobook lần nào, hoặc
    không tìm thấy ``metadata_public.json`` ở cả Drive lẫn bản backup
    offline. Tự tìm thư mục thư viện + file metadata nếu state local trống
    (thư viện đồng bộ trước đó bằng app khác)."""
    from .state_store import DEFAULT_LIBRARY_NAME
    from .library_bind import discover_library_root, import_state_from_payload
    lib_name = (library_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    state = AudiobookState(library_name=lib_name)
    root_id = discover_library_root(access_token, lib_name, state, log_fn=log_fn)
    if not root_id:
        raise ManageError(
            'Không tìm thấy thư mục "%s" trên Google Drive '
            '(hoặc app không có quyền nhìn thấy với scope drive.file).' % lib_name)

    payload, source = checker.download_metadata_json(
        access_token, state, log_fn=log_fn, root_id=root_id)
    if payload is None:
        if log_fn:
            log_fn('Không tìm thấy %s -- đang tự kiểm tra thư mục Drive và tạo metadata nếu có audiobook hợp lệ...'
                   % METADATA_JSON_NAME)
        checker.run_check(access_token, log_fn=log_fn, library_name=lib_name)
        payload, source = checker.download_metadata_json(
            access_token, state, log_fn=log_fn, root_id=root_id)
        if payload is None:
            raise ManageError(
                'Không tìm thấy %s trên Drive lẫn bản backup offline (thư viện "%s"). '
                'Hãy chắc chắn thư mục có ít nhất một thư mục con chứa metadata.opf.'
                % (METADATA_JSON_NAME, lib_name))
    import_state_from_payload(state, payload, log_fn=log_fn)
    return state, root_id, payload, source


def _save(access_token, state, root_id, payload, log_fn=None):
    log_fn = _normalize_log_fn(log_fn)
    payload['generated_at'] = datetime.now().isoformat(sep='T', timespec='seconds')
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')

    existing_id = state.get_metadata_json_id() or None
    file_id = _with_retry(
        lambda: _upload_bytes_resumable(
            access_token, data, METADATA_JSON_NAME, root_id, 'application/json', existing_id),
        log_fn, METADATA_JSON_NAME)
    state.set_metadata_json_id(file_id or existing_id or '')
    # Yêu cầu nâng cấp 2.6.0: mọi thay đổi qua dialog quản lý phải phản
    # ánh cả ở bản offline lẫn trên Drive -- dùng lại đúng cơ chế backup
    # có xoay vòng của audiobook_sync/backup.py (2.5.1 #1) để 2 nơi luôn
    # khớp nhau, giống hệt sau mỗi lần Audiobook Sync/Check chạy xong.
    library_name = getattr(state, 'library_name', None)
    backup.save_backup(data, log_fn, library_name=library_name)


def list_audiobooks(access_token, log_fn=None, library_name=None):
    """Trả về ``(root_id, list[ManagedAudiobook], source)``, sắp xếp mặc
    định theo tiêu đề không phân biệt hoa/thường (dialog quản lý sẽ tự
    sắp lại theo tiêu chí người dùng chọn -- đây chỉ là thứ tự khởi
    điểm ổn định)."""
    _state, root_id, payload, source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}
    result = [ManagedAudiobook(key, entry) for key, entry in books.items()]
    result.sort(key=lambda b: b.title.lower())
    return root_id, result, source


def _parse_creators(text):
    """"Nguyễn Nhật Ánh, Nguyễn Ngọc Thuần" -> [{"name": ..., "role": None}, ...].
    Không giữ lại `role` gốc (nếu opf có) khi sửa qua đây -- người dùng
    chỉ nhập tên phân tách bởi dấu phẩy, đơn giản hoá đúng tinh thần ô
    "Tác giả" một dòng của dialog sửa metadata."""
    names = [n.strip() for n in (text or '').split(',')]
    return [{'name': n, 'role': None} for n in names if n]


def edit_metadata(access_token, key, title=None, creators_text=None, library_name=None,
                   publisher=None, language=None, description=None, log_fn=None):
    """Sửa metadata của 1 entry -- CHỈ ghi vào ``metadata_public.json``
    (offline + Drive), KHÔNG đụng tới ``metadata.opf`` thật hay các file
    audio nằm trong thư mục cuốn sách trên Drive (đúng yêu cầu nâng cấp
    2.6.0 mục #2: "cập nhật metadata_public.json offline và trên
    drive"). Tham số nào truyền ``None`` nghĩa là giữ nguyên; truyền
    chuỗi rỗng nghĩa là xoá trắng trường đó."""
    log_fn = _normalize_log_fn(log_fn)
    state, root_id, payload, _source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}
    entry = books.get(key)
    if not entry:
        raise ManageError(
            'Không tìm thấy cuốn sách này trong %s (có thể đã bị xoá/đổi ở '
            'nơi khác -- hãy Làm mới danh sách rồi thử lại).' % METADATA_JSON_NAME)

    entry = dict(entry)
    if title is not None:
        title = title.strip()
        if not title:
            raise ManageError('Tiêu đề không được để trống.')
        entry['title'] = title
    if creators_text is not None:
        entry['creators'] = _parse_creators(creators_text)
    if publisher is not None:
        entry['publisher'] = publisher.strip() or None
    if language is not None:
        entry['language'] = language.strip() or None
    if description is not None:
        entry['description'] = description.strip() or None

    books[key] = entry
    payload['audiobooks'] = books
    _save(access_token, state, root_id, payload, log_fn)
    log_fn('Đã cập nhật metadata cho "%s".' % (entry.get('title') or key))


def _pick_earliest(candidates):
    """``candidates``: danh sách file THẬT (dict Drive API) cùng khớp 1
    tên logic (ví dụ nhiều file đều tên ``cover.jpg`` trong cùng 1 thư
    mục -- Google Drive, khác hệ điều hành thường, KHÔNG tự đổi tên file
    trùng nên cho phép nhiều bản cùng tên tồn tại song song). Đây là hậu
    quả thường gặp của 1 lượt "Đồng bộ Audiobooks lên Google Drive" bị
    lỗi/ngắt giữa chừng rồi chạy lại: bản upload đầu (SỚM NHẤT theo
    ``createdTime``) mới là bản chuẩn/đầy đủ, các bản sau chỉ là rác từ
    lần thử lại. Trả về ``None`` nếu ``candidates`` rỗng."""
    if not candidates:
        return None
    with_time = [c for c in candidates if c.get('createdTime')]
    if with_time:
        return min(with_time, key=lambda c: c['createdTime'])
    # Hiếm khi thiếu createdTime (API luôn trả về field đã yêu cầu) --
    # dự phòng bằng cách giữ nguyên thứ tự trả về từ Drive.
    return candidates[0]


def _find_canonical_opf_and_cover(folder_children):
    """``folder_children``: list file+thư mục con THẬT (dict Drive API,
    từ ``drive_api.list_folder_children``) nằm trực tiếp trong 1 thư
    mục cuốn audiobook. Trả về ``(opf_file, cover_file)`` -- mỗi phần tử
    là dict Drive API (có ``id``/``name``) của bản CHUẨN, hoặc ``None``
    nếu không tìm thấy loại đó. Nhận diện tên không phân biệt hoa/thường
    (giống ``scanner``/``checker``); nếu có NHIỀU bản trùng tên, chọn
    bản tạo sớm nhất qua :func:`_pick_earliest`."""
    opf_candidates = [c for c in folder_children
                       if (c.get('name') or '').lower() == OPF_NAME.lower()]
    opf_file = _pick_earliest(opf_candidates)

    cover_file = None
    for candidate_name in COVER_NAMES:
        matches = [c for c in folder_children
                   if (c.get('name') or '').lower() == candidate_name]
        if matches:
            cover_file = _pick_earliest(matches)
            break

    return opf_file, cover_file


def resync_book_from_drive(access_token, key, log_fn=None, library_name=None):
    """Nút "⟲ Đồng bộ từ Drive" trong "Chi tiết Audiobook" -- đọc lại
    ``metadata.opf`` + ảnh bìa THẬT từ đúng thư mục Drive của 1 cuốn rồi
    ghi đè lại entry tương ứng trong ``metadata_public.json`` (offline +
    Drive). Khắc phục trường hợp 1 cuốn bị hiển thị nhầm "Không có ảnh
    bìa" (hoặc thiếu metadata) do 1 lượt upload trước lỗi/chạy lại giữa
    chừng để lại NHIỀU file ``cover.jpg``/``metadata.opf`` trùng tên
    trong cùng 1 thư mục trên Drive mà entry vẫn trỏ tới ``file_id`` cũ
    hoặc rỗng -- xem :func:`_find_canonical_opf_and_cover`: file được
    TẠO SỚM NHẤT trong số các bản trùng tên được coi là bản chuẩn.

    CHỈ cập nhật ``metadata_opf``/``cover`` + các trường metadata suy ra
    từ opf (tiêu đề/tác giả/NXB/ngôn ngữ/mô tả/chương/...) -- không đụng
    tới ``audio_files`` (đúng phạm vi nút "Đồng bộ", khác nút "Sửa
    metadata" vốn chỉ sửa tay từng trường, và khác "Kiểm tra Audiobooks
    trên Drive..." vốn chạy hàng loạt cho MỌI cuốn thay vì 1 cuốn theo
    yêu cầu người dùng)."""
    log_fn = _normalize_log_fn(log_fn)
    state, root_id, payload, _source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}
    entry = books.get(key)
    if not entry:
        raise ManageError(
            'Không tìm thấy cuốn sách này trong %s (có thể đã bị xoá/đổi ở '
            'nơi khác -- hãy Làm mới danh sách rồi thử lại).' % METADATA_JSON_NAME)

    folder_id = entry.get('drive_folder_id')
    if not folder_id:
        raise ManageError('Cuốn sách này không có thư mục trên Google Drive để đồng bộ lại.')

    title = entry.get('title') or entry.get('folder_name') or key
    log_fn('"%s": đang quét thư mục trên Drive...' % title)
    children = _with_retry(
        lambda: drive_api.list_folder_children(access_token, folder_id),
        log_fn, 'Quét thư mục "%s"' % title)
    opf_file, cover_file = _find_canonical_opf_and_cover(children)

    entry = dict(entry)
    changed = False

    if opf_file:
        opf_matches = sum(1 for c in children if (c.get('name') or '').lower() == OPF_NAME.lower())
        if opf_matches > 1 and log_fn:
            log_fn('WARN: "%s" có %d file metadata.opf trùng tên trên Drive -- '
                   'đã lấy bản tạo sớm nhất (id %s) làm bản chuẩn.'
                   % (title, opf_matches, opf_file['id']))
        tmp_path = None
        try:
            data = _with_retry(
                lambda: drive_api.download_file_bytes(access_token, opf_file['id']),
                log_fn, 'Tải metadata.opf')
            fd, tmp_path = tempfile.mkstemp(suffix='.opf', prefix='gdrive_sync_')
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            opf_data = parse_opf_file(tmp_path)
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        entry['metadata_opf'] = {'filename': opf_file.get('name'), 'file_id': opf_file['id']}
        if opf_data.get('title'):
            entry['title'] = opf_data['title']
        if opf_data.get('creators'):
            entry['creators'] = opf_data['creators']
        entry['publisher'] = opf_data.get('publisher') or entry.get('publisher')
        entry['language'] = opf_data.get('language') or entry.get('language')
        entry['description'] = opf_data.get('description') or entry.get('description')
        if opf_data.get('identifiers'):
            entry['identifiers'] = opf_data['identifiers']
        entry['modified'] = opf_data.get('modified') or entry.get('modified')
        if opf_data.get('chapters'):
            entry['chapters'] = opf_data['chapters']
        if opf_data.get('extra_meta'):
            entry['extra_meta'] = opf_data['extra_meta']
        state.update_file(key, opf_file.get('name'), file_id=opf_file['id'])
        changed = True
        log_fn('"%s": đã đọc lại metadata.opf chuẩn (id %s) từ Drive.' % (title, opf_file['id']))
    else:
        log_fn('WARN: "%s" không tìm thấy metadata.opf nào trong thư mục trên Drive.' % title)

    if cover_file:
        cover_matches = sum(1 for c in children if (c.get('name') or '').lower() == (cover_file.get('name') or '').lower())
        if cover_matches > 1 and log_fn:
            log_fn('WARN: "%s" có %d file ảnh bìa "%s" trùng tên trên Drive -- '
                   'đã lấy bản tạo sớm nhất (id %s) làm bản chuẩn.'
                   % (title, cover_matches, cover_file.get('name'), cover_file['id']))
        entry['cover'] = {'filename': cover_file.get('name'), 'file_id': cover_file['id']}
        state.update_file(key, cover_file.get('name'), file_id=cover_file['id'])
        changed = True
        log_fn('"%s": đã lấy lại ảnh bìa chuẩn (id %s) từ Drive.' % (title, cover_file['id']))
    else:
        log_fn('WARN: "%s" không tìm thấy file ảnh bìa nào (%s) trong thư mục trên Drive.'
               % (title, '/'.join(COVER_NAMES)))

    if not changed:
        raise ManageError(
            'Không tìm thấy metadata.opf lẫn ảnh bìa trong thư mục trên Drive của cuốn này.')

    books[key] = entry
    payload['audiobooks'] = books
    _save(access_token, state, root_id, payload, log_fn)
    log_fn('Hoàn tất đồng bộ lại "%s" từ Drive.' % title)


def delete_audiobooks(access_token, keys, purge_files=False, log_fn=None, library_name=None):
    """Xoá hẳn (các) entry khỏi ``metadata_public.json`` -- không có
    tombstone (xem docstring module). Nếu ``purge_files=True``, NGOÀI RA
    còn xoá vĩnh viễn thư mục cuốn sách thật trên Drive
    (``Audiobooks/<ten_thu_muc>/``) để giải phóng dung lượng; best-effort,
    lỗi xoá file không làm hỏng việc cập nhật json. Luôn xoá cache trong
    ``state_store.AudiobookState`` cho những key đã xoá, để lần "Đồng bộ
    Audiobooks lên Google Drive" kế tiếp (nếu thư mục local tương ứng
    vẫn còn) coi cuốn đó như mới hoàn toàn thay vì dùng nhầm id cũ."""
    log_fn = _normalize_log_fn(log_fn)
    state, root_id, payload, _source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}

    count = 0
    for key in keys:
        entry = books.get(key)
        if not entry:
            continue
        title = entry.get('title') or entry.get('folder_name') or key

        if purge_files:
            folder_id = entry.get('drive_folder_id')
            if folder_id:
                try:
                    drive_api.delete_file(access_token, folder_id)
                    log_fn('Đã xoá thư mục trên Drive: %s' % title)
                except drive_api.DriveNotFoundError:
                    log_fn('Thư mục trên Drive của "%s" đã không còn tồn tại từ trước.' % title)
                except Exception as e:
                    log_fn('WARN: không xoá được thư mục trên Drive cho "%s": %s' % (title, e))
            else:
                log_fn('WARN: "%s" không có drive_folder_id trong %s, bỏ qua xoá file trên Drive.'
                       % (title, METADATA_JSON_NAME))

        del books[key]
        state.remove_book(key)
        log_fn('Đã xoá khỏi %s: %s' % (METADATA_JSON_NAME, title))
        count += 1

    payload['audiobooks'] = books
    _save(access_token, state, root_id, payload, log_fn)
    log_fn('Hoàn tất: đã xoá %d cuốn.' % count)


def move_audiobook_to_library(access_token, key, target_library_name, log_fn=None, library_name=None):
    """Move one audiobook folder to another library folder on Drive.

    This mirrors the extension manager: move the real Drive folder while
    keeping all child file ids unchanged, remove the entry from the source
    ``metadata_public.json``, then add/update the entry in the destination
    library's ``metadata_public.json``.
    """
    log_fn = _normalize_log_fn(log_fn)
    target_library_name = (target_library_name or '').strip()
    if not target_library_name:
        raise ManageError('Chưa chọn thư viện đích.')
    if target_library_name == (library_name or ''):
        raise ManageError('Thư viện đích phải khác thư viện hiện tại.')

    from .library_bind import discover_library_root, import_state_from_payload
    from .state_store import DEFAULT_LIBRARY_NAME

    source_state, source_root_id, source_payload, _source = _load(
        access_token, log_fn, library_name=library_name)
    source_books = source_payload.get('audiobooks') or {}
    entry = source_books.get(key)
    if not entry:
        raise ManageError('Không tìm thấy cuốn sách trong thư viện nguồn.')

    folder_id = entry.get('drive_folder_id')
    folder_name = entry.get('folder_name') or entry.get('title') or key
    if not folder_id:
        raise ManageError('Cuốn này không có drive_folder_id để di chuyển.')

    target_lib = target_library_name or DEFAULT_LIBRARY_NAME
    target_state = AudiobookState(library_name=target_lib)
    target_root_id = discover_library_root(access_token, target_lib, target_state, log_fn=log_fn)
    if not target_root_id:
        raise ManageError('Không tìm thấy thư viện đích "%s" trên Drive.' % target_lib)

    if source_root_id == target_root_id:
        raise ManageError('Thư viện đích trùng thư viện hiện tại.')

    # Avoid accidentally merging two real folders with the same name.
    existing_child = drive_api.find_child_folder(access_token, target_root_id, folder_name)
    if existing_child and existing_child != folder_id:
        raise ManageError(
            'Thư viện đích đã có thư mục "%s". Hãy đổi tên/xóa thư mục đó trước khi di chuyển.'
            % folder_name)

    log_fn('Đang di chuyển thư mục "%s" sang thư viện "%s"...' % (folder_name, target_lib))
    _with_retry(
        lambda: drive_api.move_file(access_token, folder_id, target_root_id, source_root_id),
        log_fn, 'Di chuyển thư mục "%s"' % folder_name)

    # Save source without the moved book.
    moved_entry = dict(entry)
    moved_entry['drive_folder_id'] = folder_id
    del source_books[key]
    source_payload['audiobooks'] = source_books
    source_state.remove_book(key)
    _save(access_token, source_state, source_root_id, source_payload, log_fn)

    # Load or create destination metadata and write entry there.
    target_payload, _target_source = metadata_io.download_metadata_json(
        access_token, target_state, log_fn=log_fn, root_id=target_root_id)
    if target_payload is None:
        target_payload = {
            'generated_at': datetime.now().isoformat(sep='T', timespec='seconds'),
            'source': 'audiobookgdrive_manage_move',
            'drive_folder_url': 'https://drive.google.com/drive/folders/%s?usp=drive_link' % target_root_id,
            'audiobooks': {},
        }
    target_books = target_payload.setdefault('audiobooks', {})

    conflict_key = None
    for k, v in list(target_books.items()):
        if (v or {}).get('folder_name') == folder_name:
            conflict_key = k
            break
    if conflict_key:
        target_books.pop(conflict_key, None)

    new_key = key
    if new_key in target_books:
        new_key = 'moved|%s' % folder_name
        i = 2
        while new_key in target_books:
            new_key = 'moved|%s|%d' % (folder_name, i)
            i += 1
    target_books[new_key] = moved_entry
    target_payload['audiobooks'] = target_books
    _save(access_token, target_state, target_root_id, target_payload, log_fn)
    import_state_from_payload(target_state, {'audiobooks': {new_key: moved_entry}}, log_fn=log_fn)
    log_fn('Đã di chuyển "%s" sang thư viện "%s".' % (folder_name, target_lib))


def rename_library_folder(access_token, old_library_name, new_library_name, log_fn=None):
    """Rename the Drive library folder while keeping the same folder id."""
    log_fn = _normalize_log_fn(log_fn)
    old_name = (old_library_name or '').strip()
    new_name = (new_library_name or '').strip()
    if not old_name:
        raise ManageError('Chưa xác định thư viện hiện tại.')
    if not new_name:
        raise ManageError('Tên thư viện mới không được để trống.')
    if old_name == new_name:
        raise ManageError('Tên thư viện mới đang trùng tên hiện tại.')
    if '/' in new_name or '\\' in new_name:
        raise ManageError('Tên thư viện không được chứa / hoặc \\.')

    from .library_bind import discover_library_root
    from .state_store import rename_library_state
    from audiobookgdrive.config import prefs

    state = AudiobookState(library_name=old_name)
    root_id = state.get_root_folder_id()
    if not root_id:
        root_id = discover_library_root(access_token, old_name, state, log_fn=log_fn)
    if not root_id:
        raise ManageError('Không tìm thấy thư mục thư viện "%s" trên Drive.' % old_name)

    existing = drive_api.find_child_folder(access_token, 'root', new_name)
    if existing and existing != root_id:
        raise ManageError('My Drive đã có thư mục "%s". Hãy chọn tên khác.' % new_name)

    log_fn('Đang đổi tên thư viện "%s" thành "%s"...' % (old_name, new_name))
    _with_retry(
        lambda: drive_api.update_file_metadata(access_token, root_id, {'name': new_name}),
        log_fn, 'Đổi tên thư viện "%s"' % old_name)

    rename_library_state(old_name, new_name)
    if (prefs.get('audiobook_active_library') or '') == old_name:
        prefs['audiobook_active_library'] = new_name

    names = []
    seen = set()
    for name in prefs.get('audiobook_library_folders') or []:
        name = new_name if name == old_name else name
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if new_name not in seen:
        names.append(new_name)
    prefs['audiobook_library_folders'] = names

    cache = []
    replaced = False
    for item in prefs.get('audiobook_library_cache') or []:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        if item.get('id') == root_id or item.get('name') == old_name:
            item['name'] = new_name
            item['id'] = root_id
            replaced = True
        cache.append(item)
    if not replaced:
        cache.append({'name': new_name, 'id': root_id, 'has_metadata': True})
    prefs['audiobook_library_cache'] = cache
    log_fn('Đã đổi tên thư viện thành "%s" (folder id không đổi: %s).' % (new_name, root_id))
    return new_name


# -- cover thumbnail cache ------------------------------------------------
# Cache theo cover.file_id (không phụ thuộc key sách) để lần mở Quản lý
# sau hiện bìa ngay từ đĩa, kể cả khi key local đổi (root_path khác máy).
# Tương thích file cache cũ dạng ``<hash16>_<file_id>.jpg``.

def _cover_cache_dir():
    from audiobookgdrive.jsonconfig import config_dir
    d = os.path.join(config_dir, 'plugins', 'gdrive_sync_audiobook_covers')
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except OSError:
            pass
    return d


def _safe_file_id(cover_file_id):
    # Drive file id thường [A-Za-z0-9_-]; lọc để an toàn trên Windows path
    return ''.join(c if c.isalnum() or c in '-_' else '_' for c in (cover_file_id or ''))[:120]


def peek_cached_cover_path(cover_file_id, key=None):
    """Trả về đường dẫn cache nếu đã có trên đĩa -- KHÔNG tải mạng.

    Dùng để hiện bìa tức thì khi mở dialog Quản lý.
    """
    if not cover_file_id:
        return None
    cache_dir = _cover_cache_dir()
    primary = os.path.join(cache_dir, '%s.jpg' % _safe_file_id(cover_file_id))
    if os.path.exists(primary) and os.path.getsize(primary) > 0:
        return primary
    # Cache cũ: ``<sha16>_<file_id>.jpg``
    if key:
        safe_key = hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
        legacy = os.path.join(cache_dir, '%s_%s.jpg' % (safe_key, cover_file_id))
        if os.path.exists(legacy) and os.path.getsize(legacy) > 0:
            return legacy
    # Quét legacy theo hậu tố file_id (key đã đổi)
    suffix = '_%s.jpg' % cover_file_id
    try:
        for name in os.listdir(cache_dir):
            if name.endswith(suffix):
                path = os.path.join(cache_dir, name)
                if os.path.getsize(path) > 0:
                    return path
    except OSError:
        pass
    return None


def cached_cover_path(access_token, key, cover_file_id):
    """Lấy path cover: cache hit thì trả ngay; miss thì tải từ Drive và lưu."""
    if not cover_file_id:
        return None
    hit = peek_cached_cover_path(cover_file_id, key=key)
    if hit:
        return hit
    cache_dir = _cover_cache_dir()
    cached = os.path.join(cache_dir, '%s.jpg' % _safe_file_id(cover_file_id))
    data = drive_api.download_file_bytes(access_token, cover_file_id, timeout=60)
    if not data:
        return None
    try:
        tmp = cached + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, cached)
    except OSError:
        try:
            with open(cached, 'wb') as f:
                f.write(data)
        except OSError:
            return None
    return cached


def _guess_image_mime_and_name(local_path):
    """Trả về (mime_type, preferred_filename) từ đuôi file ảnh local."""
    ext = os.path.splitext(local_path or '')[1].lower()
    if ext in ('.png',):
        return 'image/png', 'cover.png'
    if ext in ('.jpeg', '.jpg', '.jpe'):
        return 'image/jpeg', 'cover.jpg'
    if ext in ('.webp',):
        return 'image/webp', 'cover.webp'
    if ext in ('.gif',):
        return 'image/gif', 'cover.gif'
    # Mặc định coi là JPEG (phổ biến nhất cho bìa sách)
    return 'image/jpeg', 'cover.jpg'


def _write_cover_cache(cover_file_id, content_bytes):
    """Ghi đè cache thumbnail local theo file_id (để UI hiện ảnh mới ngay)."""
    if not cover_file_id or not content_bytes:
        return
    cache_dir = _cover_cache_dir()
    cached = os.path.join(cache_dir, '%s.jpg' % _safe_file_id(cover_file_id))
    try:
        tmp = cached + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(content_bytes)
        os.replace(tmp, cached)
    except OSError:
        try:
            with open(cached, 'wb') as f:
                f.write(content_bytes)
        except OSError:
            pass


def replace_cover_image(access_token, key, local_image_path, log_fn=None, library_name=None):
    """Thay ảnh bìa của 1 audiobook trên Drive.

    Ưu tiên giữ nguyên ``cover.file_id`` (PATCH nội dung file) để
    ``metadata_public.json`` không cần cập nhật ``file_id``. Chỉ khi
    cuốn chưa có ảnh bìa (hoặc chưa có ``drive_folder_id``) thì mới
    upload file mới và ghi ``entry['cover']`` vào metadata.

    Cập nhật luôn cache thumbnail local theo file_id để lần reload
    sau hiện ảnh mới ngay.
    """
    log_fn = _normalize_log_fn(log_fn)
    if not local_image_path or not os.path.isfile(local_image_path):
        raise ManageError('File ảnh không tồn tại: %s' % local_image_path)

    try:
        with open(local_image_path, 'rb') as f:
            content_bytes = f.read()
    except OSError as e:
        raise ManageError('Không đọc được file ảnh: %s' % e)

    if not content_bytes:
        raise ManageError('File ảnh rỗng.')

    if len(content_bytes) > 15 * 1024 * 1024:
        raise ManageError('Ảnh bìa quá lớn (giới hạn 15 MB).')

    mime_type, preferred_name = _guess_image_mime_and_name(local_image_path)

    state, root_id, payload, _source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}
    entry = books.get(key)
    if not entry:
        raise ManageError(
            'Không tìm thấy cuốn sách này trong %s (có thể đã bị xoá/đổi ở '
            'nơi khác -- hãy Làm mới danh sách rồi thử lại).' % METADATA_JSON_NAME)

    title = entry.get('title') or entry.get('folder_name') or key
    folder_id = entry.get('drive_folder_id')
    cover = entry.get('cover') or {}
    existing_file_id = cover.get('file_id') if cover else None
    existing_name = (cover.get('filename') or preferred_name) if cover else preferred_name

    if existing_file_id:
        # Giữ nguyên file_id: chỉ PATCH nội dung (và có thể đổi tên nếu cần)
        log_fn('"%s": đang thay nội dung ảnh bìa (giữ nguyên file_id %s)...'
               % (title, existing_file_id))
        name_for_upload = existing_name or preferred_name
        new_id = _with_retry(
            lambda: drive_api.upload_small_file(
                access_token, name_for_upload, folder_id, mime_type, content_bytes,
                existing_file_id=existing_file_id),
            log_fn, 'Upload ảnh bìa "%s"' % title)
        if new_id and new_id != existing_file_id:
            # Hiếm khi Drive trả id khác; cập nhật metadata cho chắc
            entry = dict(entry)
            entry['cover'] = {'filename': name_for_upload, 'file_id': new_id}
            books[key] = entry
            payload['audiobooks'] = books
            _save(access_token, state, root_id, payload, log_fn)
            log_fn('"%s": Drive trả file_id mới (%s) -- đã cập nhật metadata.'
                   % (title, new_id))
            _write_cover_cache(new_id, content_bytes)
        else:
            # file_id giữ nguyên → không cần đụng metadata_public.json
            _write_cover_cache(existing_file_id, content_bytes)
            # Vẫn cập nhật state local nếu có tracking
            try:
                state.update_file(key, name_for_upload, file_id=existing_file_id)
            except Exception:
                pass
            log_fn('"%s": đã thay ảnh bìa thành công (file_id không đổi).' % title)
        return

    # Chưa có cover: cần thư mục trên Drive để upload file mới
    if not folder_id:
        raise ManageError(
            'Cuốn sách này chưa có thư mục trên Google Drive -- '
            'không thể upload ảnh bìa mới. Hãy chạy Đồng bộ trước.')

    log_fn('"%s": chưa có ảnh bìa -- đang upload file mới "%s"...'
           % (title, preferred_name))
    new_id = _with_retry(
        lambda: drive_api.upload_small_file(
            access_token, preferred_name, folder_id, mime_type, content_bytes),
        log_fn, 'Upload ảnh bìa mới "%s"' % title)
    if not new_id:
        raise ManageError('Upload ảnh bìa thất bại (không nhận được file_id).')

    entry = dict(entry)
    entry['cover'] = {'filename': preferred_name, 'file_id': new_id}
    books[key] = entry
    payload['audiobooks'] = books
    _save(access_token, state, root_id, payload, log_fn)
    try:
        state.update_file(key, preferred_name, file_id=new_id)
    except Exception:
        pass
    _write_cover_cache(new_id, content_bytes)
    log_fn('"%s": đã thêm ảnh bìa mới (file_id %s) và cập nhật metadata.'
           % (title, new_id))


def replace_metadata_opf(access_token, key, local_opf_path, log_fn=None, library_name=None):
    """Thay file ``metadata.opf`` của 1 audiobook trên Drive.

    Ưu tiên giữ nguyên ``metadata_opf.file_id`` (PATCH nội dung). Sau khi
    upload, parse OPF mới và ghi đè các trường metadata suy ra
    (title/creators/publisher/language/description/chapters/...) vào
    ``metadata_public.json`` (offline + Drive). Không đụng ``audio_files``
    hay ảnh bìa.
    """
    log_fn = _normalize_log_fn(log_fn)
    if not local_opf_path or not os.path.isfile(local_opf_path):
        raise ManageError('File OPF không tồn tại: %s' % local_opf_path)

    ext = os.path.splitext(local_opf_path)[1].lower()
    if ext and ext not in ('.opf', '.xml'):
        raise ManageError('File phải có đuôi .opf (nhận được: %s).' % ext)

    try:
        with open(local_opf_path, 'rb') as f:
            content_bytes = f.read()
    except OSError as e:
        raise ManageError('Không đọc được file OPF: %s' % e)

    if not content_bytes:
        raise ManageError('File OPF rỗng.')

    if len(content_bytes) > 5 * 1024 * 1024:
        raise ManageError('File OPF quá lớn (giới hạn 5 MB).')

    # Parse trước khi upload để fail sớm nếu OPF hỏng
    opf_data = parse_opf_file(local_opf_path)
    if not opf_data.get('title') and not opf_data.get('creators') and not opf_data.get('chapters'):
        # Vẫn cho phép upload nhưng cảnh báo -- có thể là OPF tối giản
        log_fn('WARN: File OPF gần như không có title/creators/chapters -- vẫn tiếp tục upload.')

    mime_type = 'application/oebps-package+xml'
    preferred_name = OPF_NAME

    state, root_id, payload, _source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}
    entry = books.get(key)
    if not entry:
        raise ManageError(
            'Không tìm thấy cuốn sách này trong %s (có thể đã bị xoá/đổi ở '
            'nơi khác -- hãy Làm mới danh sách rồi thử lại).' % METADATA_JSON_NAME)

    title = entry.get('title') or entry.get('folder_name') or key
    folder_id = entry.get('drive_folder_id')
    meta_opf = entry.get('metadata_opf') or {}
    existing_file_id = meta_opf.get('file_id') if meta_opf else None
    existing_name = (meta_opf.get('filename') or preferred_name) if meta_opf else preferred_name

    entry = dict(entry)

    if existing_file_id:
        log_fn('"%s": đang thay nội dung metadata.opf (giữ nguyên file_id %s)...'
               % (title, existing_file_id))
        name_for_upload = existing_name or preferred_name
        new_id = _with_retry(
            lambda: drive_api.upload_small_file(
                access_token, name_for_upload, folder_id, mime_type, content_bytes,
                existing_file_id=existing_file_id),
            log_fn, 'Upload metadata.opf "%s"' % title)
        file_id = new_id or existing_file_id
        entry['metadata_opf'] = {'filename': name_for_upload, 'file_id': file_id}
        try:
            state.update_file(key, name_for_upload, file_id=file_id)
        except Exception:
            pass
        log_fn('"%s": đã thay metadata.opf (file_id %s).' % (title, file_id))
    else:
        if not folder_id:
            raise ManageError(
                'Cuốn sách này chưa có thư mục trên Google Drive -- '
                'không thể upload metadata.opf. Hãy chạy Đồng bộ trước.')
        log_fn('"%s": chưa có metadata.opf -- đang upload file mới "%s"...'
               % (title, preferred_name))
        new_id = _with_retry(
            lambda: drive_api.upload_small_file(
                access_token, preferred_name, folder_id, mime_type, content_bytes),
            log_fn, 'Upload metadata.opf mới "%s"' % title)
        if not new_id:
            raise ManageError('Upload metadata.opf thất bại (không nhận được file_id).')
        entry['metadata_opf'] = {'filename': preferred_name, 'file_id': new_id}
        try:
            state.update_file(key, preferred_name, file_id=new_id)
        except Exception:
            pass
        log_fn('"%s": đã thêm metadata.opf mới (file_id %s).' % (title, new_id))

    # Cập nhật các trường suy ra từ OPF vào metadata_public.json
    if opf_data.get('title'):
        entry['title'] = opf_data['title']
    if opf_data.get('creators'):
        entry['creators'] = opf_data['creators']
    if opf_data.get('publisher') is not None:
        entry['publisher'] = opf_data.get('publisher') or None
    if opf_data.get('language') is not None:
        entry['language'] = opf_data.get('language') or None
    if opf_data.get('description') is not None:
        entry['description'] = opf_data.get('description') or None
    if opf_data.get('identifiers'):
        entry['identifiers'] = opf_data['identifiers']
    if opf_data.get('modified') is not None:
        entry['modified'] = opf_data.get('modified')
    if opf_data.get('chapters') is not None:
        entry['chapters'] = opf_data['chapters']
    if opf_data.get('extra_meta'):
        entry['extra_meta'] = opf_data['extra_meta']

    books[key] = entry
    payload['audiobooks'] = books
    _save(access_token, state, root_id, payload, log_fn)
    log_fn('"%s": đã cập nhật metadata_public.json theo nội dung OPF mới.'
           % (entry.get('title') or title))


def _safe_local_name(name, fallback='Audiobook'):
    name = (name or '').strip() or fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.rstrip(' .')
    return name[:180] or fallback


def download_audiobook_folder(access_token, key, target_parent_dir, log_fn=None,
                              progress_fn=None, check_abort=None, library_name=None):
    """Download every direct file in an audiobook's Drive folder.

    The output is ``target_parent_dir/<folder_name>/`` and includes audio,
    cover, and ``metadata.opf`` exactly as stored on Drive.
    """
    log_fn = _normalize_log_fn(log_fn)
    if not target_parent_dir or not os.path.isdir(target_parent_dir):
        raise ManageError('Thư mục tải xuống không hợp lệ.')

    state, _root_id, payload, _source = _load(access_token, log_fn, library_name=library_name)
    books = payload.get('audiobooks') or {}
    entry = books.get(key)
    if not entry:
        raise ManageError('Không tìm thấy cuốn sách này trong metadata_public.json.')
    folder_id = entry.get('drive_folder_id')
    if not folder_id:
        raise ManageError('Cuốn sách này không có drive_folder_id để tải xuống.')

    folder_name = entry.get('folder_name') or entry.get('title') or key
    local_dir = os.path.join(target_parent_dir, _safe_local_name(folder_name))
    os.makedirs(local_dir, exist_ok=True)

    children = _with_retry(
        lambda: drive_api.list_folder_children(access_token, folder_id),
        log_fn, 'Liệt kê thư mục "%s"' % folder_name)
    files = [c for c in children if c.get('mimeType') != drive_api.FOLDER_MIME and c.get('id')]
    files.sort(key=lambda x: (x.get('name') or '').lower())
    total = len(files)
    if log_fn:
        log_fn('Bắt đầu tải "%s" (%d file) về %s' % (folder_name, total, local_dir))

    for i, f in enumerate(files, start=1):
        if check_abort and check_abort():
            raise ManageError('Đã hủy tải xuống.')
        name = _safe_local_name(f.get('name') or ('file_%03d' % i), fallback='file_%03d' % i)
        local_path = os.path.join(local_dir, name)
        if progress_fn:
            progress_fn(i - 1, total, name)
        _with_retry(
            lambda fid=f['id'], lp=local_path: drive_api.download_file_to_path(access_token, fid, lp),
            log_fn, 'Tải "%s"' % name)
        if log_fn:
            log_fn('Đã tải: %s' % name)
        if progress_fn:
            progress_fn(i, total, name)

    # Keep local state warm for later upload/check paths.
    try:
        state.update_book(key, drive_folder_id=folder_id, root_path=target_parent_dir, name=folder_name)
    except Exception:
        pass
    if log_fn:
        log_fn('Hoàn tất tải xuống "%s".' % folder_name)
    return local_dir
