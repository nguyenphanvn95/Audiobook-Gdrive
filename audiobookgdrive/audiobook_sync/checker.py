# -*- coding: utf-8 -*-
"""
audiobook_sync/checker.py
============================

Logic nghiệp vụ đứng sau mục menu **"Kiểm tra Audiobooks trên Drive..."**
(yêu cầu nâng cấp 2.5.1 mục #2) -- đối ứng với "Check Library on Drive"
của Calibre Library (``library_check.py``), nhưng đi NGƯỢC HƯỚNG:

* ``library_check.py`` xuất phát từ local (``metadata.db``) rồi tìm
  những thư mục "thừa" đang nằm trên Drive không khớp cuốn nào ở local
  -- tức dọn dẹp mồ côi **trên Drive**, việc XÓA thật xảy ra trên Drive
  nên cần cho người dùng chọn từng thư mục trước khi xóa.
* Module này xuất phát từ **thực tế trên Drive** (duyệt cây thư mục thật
  dưới ``Audiobooks/``) rồi đối chiếu với ``metadata_public.json`` --
  file duy nhất mà 1 app đọc sách nói bên ngoài dùng để biết audiobook
  nào tồn tại và file nào ứng với id nào. Nếu 1 cuốn (hoặc 1 file bên
  trong 1 cuốn) mà json còn ghi nhận nhưng KHÔNG CÒN THẬT trên Drive nữa
  (bị xóa thủ công trên drive.google.com, hoặc một lần đồng bộ trước bị
  ngắt giữa chừng) thì mục đó bị xóa khỏi json (và khỏi
  ``state_store.AudiobookState`` nội bộ) rồi json được tải lại lên
  Drive.

Quan trọng: bước này KHÔNG bao giờ xóa gì THẬT trên Drive, chỉ sửa lại
nội dung ``metadata_public.json`` cho đúng với thực tế đã quan sát được
-- vì vậy an toàn để chạy tự động (không cần người dùng chọn từng dòng
như "Check Library on Drive"). Mục tiêu là tránh việc json tiếp tục trỏ
tới 1 folder id/file id "mồ côi" không ai còn giữ trên Drive, khiến app
đọc sách nói cố tải 1 file đã không còn tồn tại.

Nâng cấp (mục #3): PHÁT HIỆN (không tự xóa) những thư mục cuốn sách
TỒN TẠI thật trên Drive (và vẫn khớp ``metadata_public.json``, tức
không rơi vào trường hợp "mồ côi" ở trên) nhưng KHÔNG CÓ FILE AUDIO
NÀO bên trong -- tình huống thực tế: 1 lượt "Đồng bộ Audiobooks lên
Google Drive" bị ngắt giữa chừng (mất mạng, tắt máy, Ctrl+C...) ngay
sau khi đã tạo xong thư mục + tải xong ``metadata.opf``/``cover`` (rất
nhanh) nhưng CHƯA kịp tải file audio nào (chậm hơn nhiều) -- để lại 1
thư mục "rỗng ruột" mà app đọc sách nói vẫn hiển thị như 1 cuốn hợp lệ
(có bìa, có tiêu đề, có số chương lấy từ ``metadata.opf``) nhưng bấm
vào nghe thì không có gì để phát. Nếu cuốn đó bị đồng bộ lại lần 2 từ 1
thư mục local cùng tên (ví dụ Calibre tự đặt tên khác 1 chút do trùng
tiêu đề), kết quả là 2 entry trùng tên trên app đọc sách nói, 1 cái
"chết". Vì việc XÓA hẳn 1 entry là hành động không thể hoàn tác dễ gây
mất dữ liệu oan (không loại trừ 1 cuốn nào đó THẬT SỰ chỉ có
metadata.opf/cover mà chưa kịp thêm audio), bước quét này CHỈ ghi nhận
lại danh sách (``stats['no_audio_books']``) để log/dialog hiển thị cho
người dùng tự quyết định xóa qua "Quản lý Audiobooks trên Drive..."
(``audiobook_manage_dialog.py``, có bộ lọc "Không có audio" riêng) --
không tự động sửa ``metadata_public.json`` vì đây không phải sai lệch
dữ liệu (khác với phần "mồ côi" ở trên), mà là dữ liệu đúng nhưng cảnh
báo về nội dung.

Nâng cấp (mục #3 nâng cấp id): các trường hợp ``_fix_book_entry`` phát
hiện *thiếu* file_id (không phải mồ côi) nhưng có file thật cùng tên
trên Drive (thường gặp nhất: thiếu id cover do 1 lượt đồng bộ trước bị
ngắt ngay sau khi Drive nhận file xong nhưng trước khi kịp ghi lại id)
giờ được TỰ ĐIỀN LẠI theo tên file, thay vì chỉ xử lý chiều "xóa mồ
côi" như trước.

Nâng cấp (mục #4): quét thêm những thư mục sách THẬT tồn tại trên
Drive (đúng định dạng: có metadata.opf + audio) nhưng KHÔNG có entry
nào trong metadata_public.json -- trường hợp người dùng tự
tạo/kéo-thả 1 thư mục sách thẳng lên Drive, không qua "Đồng bộ
Audiobooks lên Google Drive" của plugin. Với mỗi thư mục như vậy, tải
metadata.opf về đọc metadata thật rồi ghi bổ sung 1 entry mới (đánh dấu
``origin = 'drive_manual'``) vào json, kèm file_id lấy theo tên file
thật trên Drive. Vì entry này không xuất phát từ thư mục local nào,
``uploader._merge_missing_books`` phải chủ động giữ lại nó ở mỗi
lần đồng bộ bình thường tiếp theo (cùng cơ chế bảo lưu MỌI entry cũ
không còn thư mục local tương ứng -- không riêng gì
``origin == 'drive_manual'`` -- nếu không sẽ bị ghi đè mất, xem
``uploader.py``).
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import json
import os
import tempfile
import time
from datetime import datetime

from audiobookgdrive import drive_api
from audiobookgdrive.jsonconfig import JSONConfig

from . import backup, scanner
from .metadata_io import download_metadata_json, METADATA_JSON_NAME
from .opf_parser import parse_opf_file
from .state_store import AudiobookState
from .uploader import (
    ROOT_FOLDER_NAME, _upload_bytes_resumable, _with_retry,
)


class CheckCancelled(Exception):
    pass


from .library_bind import discover_library_root, import_state_from_payload


_checkpoint_store = JSONConfig('plugins/gdrive_sync_audiobook_checkpoints')
_checkpoint_store.defaults['checkpoints'] = {}


def _checkpoint_key(library_name, root_id):
    return '%s|%s' % ((library_name or '').strip(), root_id or '')


def _get_checkpoint(library_name, root_id):
    checkpoints = _checkpoint_store.get('checkpoints') or {}
    cp = checkpoints.get(_checkpoint_key(library_name, root_id)) or {}
    if cp.get('root_id') != root_id:
        return {}
    if cp.get('library_name') != library_name:
        return {}
    return cp if isinstance(cp, dict) else {}


def _save_checkpoint(library_name, root_id, checkpoint):
    checkpoints = dict(_checkpoint_store.get('checkpoints') or {})
    checkpoint = dict(checkpoint or {})
    checkpoint['library_name'] = library_name
    checkpoint['root_id'] = root_id
    checkpoint['updated_at'] = int(time.time() * 1000)
    checkpoints[_checkpoint_key(library_name, root_id)] = checkpoint
    _checkpoint_store['checkpoints'] = checkpoints


def _clear_checkpoint(library_name, root_id):
    checkpoints = dict(_checkpoint_store.get('checkpoints') or {})
    key = _checkpoint_key(library_name, root_id)
    if key in checkpoints:
        checkpoints.pop(key, None)
        _checkpoint_store['checkpoints'] = checkpoints


def _scan_drive_tree(access_token, root_id, log_fn=None, progress_fn=None, check_abort=None,
                     library_name=None, resume=True):
    """Duyệt cây thư mục Audiobooks THẬT trên Drive, 2 cấp (gốc rồi
    từng thư mục cuốn -- không cần sâu hơn, vì mỗi cuốn chỉ chứa file,
    không có thư mục con). Trả về
    ``{ten_thu_muc_cuon: {'id': folder_id, 'files': {ten_file: file_id}}}``.

    Đây là N+1 lệnh gọi HTTP TUẦN TỰ (1 gốc + 1 mỗi thư mục cuốn) --
    với thư viện có nhiều audiobook, tổng thời gian có thể khá lâu và
    xác suất gặp 1 lần timeout/mạng chập chờn ở đâu đó giữa chừng là
    không nhỏ. Trước đây mỗi lệnh gọi ``drive_api.list_folder_children``
    không có retry (khác với nhánh upload trong ``uploader.py``, vốn
    bọc mọi lệnh ghi bằng ``_with_retry``) nên 1 lần timeout bất kỳ sẽ
    làm ``DriveRetryableError`` văng thẳng lên, hủy TOÀN BỘ lượt kiểm
    tra ngay lập tức (xem log lỗi \"Timed out talking to Google Drive
    API\"). Giờ mỗi lệnh gọi được bọc ``_with_retry`` (thử lại theo
    ``prefs['max_retries']``/``prefs['retry_backoff_seconds']``, cùng
    cấu hình với upload) trước khi thực sự báo lỗi lên trên.

    ``progress_fn(done, total, current_folder_name)`` được gọi TRƯỚC
    khi quét từng thư mục cuốn (cùng khuôn dạng ``(scanned, current_path)``
    mà ``library_check.scan_drive_orphans`` đã dùng cho \"Check Library
    on Drive\") để nơi gọi (dialog) có thể hiện \"đang xem thư mục nào\"
    theo thời gian thực -- trước đây bước này hoàn toàn im lặng cho tới
    khi quét XONG HẾT mọi thư mục, khiến dialog treo cứng ở dòng
    \"Đang quét cây thư mục...\" không nhúc nhích nếu thư viện có nhiều
    audiobook.
    """
    lib_name = (library_name or '').strip()
    checkpoint = _get_checkpoint(lib_name, root_id) if (resume and lib_name) else {}
    tree = dict(checkpoint.get('tree') or {})
    completed_ids = set(checkpoint.get('completed_ids') or [])
    folders = checkpoint.get('folders') or []
    if folders and log_fn:
        log_fn('INFO', 'Tiếp tục từ checkpoint: đã có %d/%d thư mục được quét trước đó.'
               % (len(completed_ids), len(folders)))
    if not folders:
        root_children = _with_retry(
            lambda: drive_api.list_folder_children(access_token, root_id),
            log_fn, 'Quét thư mục Audiobooks')
        folders = [
            {'id': c.get('id') or '', 'name': c.get('name') or ''}
            for c in root_children
            if c.get('mimeType') == drive_api.FOLDER_MIME and c.get('id')
        ]
        if lib_name:
            _save_checkpoint(lib_name, root_id, {
                'phase': 'scan_drive_tree',
                'folders': folders,
                'completed_ids': [],
                'tree': {},
            })
    total = len(folders)
    for done, child in enumerate(folders, start=1):
        if check_abort and check_abort():
            raise CheckCancelled()
        name = child.get('name') or ''
        child_id = child.get('id') or ''
        if child_id in completed_ids:
            if progress_fn:
                progress_fn(done, total, name)
            continue
        if progress_fn:
            progress_fn(done, total, name)
        grandchildren = _with_retry(
            lambda cid=child_id: drive_api.list_folder_children(access_token, cid),
            log_fn, 'Quét thư mục "%s"' % name)
        files = {}
        for grandchild in grandchildren:
            if grandchild.get('mimeType') == drive_api.FOLDER_MIME:
                continue
            files[grandchild.get('name') or ''] = grandchild.get('id')
        tree[name] = {'id': child_id, 'files': files}
        completed_ids.add(child_id)
        if lib_name:
            _save_checkpoint(lib_name, root_id, {
                'phase': 'scan_drive_tree',
                'folders': folders,
                'completed_ids': sorted(completed_ids),
                'tree': tree,
            })
    return tree


def _fix_book_entry(entry, actual_files, state, key, log_fn):
    """Sửa 1 entry cuốn sách trong json TẠI CHỖ (in-place), đối chiếu
    với ``actual_files`` (``{ten_file: file_id}`` -- file THẬT đang có
    trong đúng thư mục cuốn này trên Drive). Với mỗi file mà entry ghi
    nhận (metadata.opf, cover, từng file audio), xử lý 2 CHIỀU:

    * Mồ côi (yêu cầu #2, ở mức từng file): entry còn ghi ``file_id``
      nhưng id đó không còn khớp file thật nào trong ``actual_files``
      (bị xóa/ghi đè thủ công trên Drive) -- xóa ``file_id`` đó đi (đặt
      về rỗng) ở cả json lẫn state_store, để lần đồng bộ upload kế
      tiếp tự biết cần tải lại file đó thay vì tưởng nhầm "đã có rồi".
    * Thiếu id (yêu cầu #3, nâng cấp mới): entry đang có ``file_id``
      RỖNG/THIẾU (ví dụ do 1 lượt đồng bộ trước bị lỗi/ngắt giữa chừng
      ngay sau khi Drive đã nhận xong file nhưng trước khi kịp ghi id
      trả về vào json/state) NHƯNG có 1 file thật CÙNG TÊN đang nằm sẵn
      trong ``actual_files`` -- điền lại ``file_id`` theo tên đó, để
      không phải tải lại 1 file vốn dĩ đã tồn tại đúng chỗ trên Drive.
      Ca thực tế hay gặp nhất: thiếu id của ``cover``.

    Trả về số field đã sửa (xóa hoặc điền).
    """
    fixed = 0
    actual_ids = set(actual_files.values())
    folder_label = entry.get('folder_name') or key

    def _stale(file_id):
        return bool(file_id) and file_id not in actual_ids

    # -- metadata.opf --------------------------------------------------
    opf_entry = entry.get('metadata_opf') or {}
    opf_name = opf_entry.get('filename') or 'metadata.opf'
    if _stale(opf_entry.get('file_id')):
        opf_entry['file_id'] = ''
        entry['metadata_opf'] = opf_entry
        state.update_file(key, opf_name, file_id='')
        fixed += 1
    elif not opf_entry.get('file_id') and opf_name in actual_files:
        opf_entry['file_id'] = actual_files[opf_name]
        entry['metadata_opf'] = opf_entry
        state.update_file(key, opf_name, file_id=actual_files[opf_name])
        fixed += 1
        if log_fn:
            log_fn('INFO', '"%s": metadata.opf thiếu file_id -- đã điền lại theo tên file trên Drive.'
                   % folder_label)

    # -- cover ------------------------------------------------------------
    cover_entry = entry.get('cover')
    if cover_entry:
        cover_name = cover_entry.get('filename') or ''
        if _stale(cover_entry.get('file_id')):
            cover_entry['file_id'] = ''
            entry['cover'] = cover_entry
            state.update_file(key, cover_name, file_id='')
            fixed += 1
        elif not cover_entry.get('file_id') and cover_name and cover_name in actual_files:
            cover_entry['file_id'] = actual_files[cover_name]
            entry['cover'] = cover_entry
            state.update_file(key, cover_name, file_id=actual_files[cover_name])
            fixed += 1
            if log_fn:
                log_fn('INFO', '"%s": cover thiếu file_id -- đã điền lại theo tên file trên Drive.'
                       % folder_label)

    # -- audio files ------------------------------------------------------
    audio_files = entry.get('audio_files') or {}
    for filename, finfo in list(audio_files.items()):
        finfo = finfo or {}
        if _stale(finfo.get('file_id')):
            finfo['file_id'] = ''
            audio_files[filename] = finfo
            state.update_file(key, filename, file_id='')
            fixed += 1
        elif not finfo.get('file_id') and filename in actual_files:
            finfo['file_id'] = actual_files[filename]
            audio_files[filename] = finfo
            state.update_file(key, filename, file_id=actual_files[filename])
            fixed += 1
            if log_fn:
                log_fn('INFO', '"%s": file audio "%s" thiếu file_id -- đã điền lại theo tên file trên Drive.'
                       % (folder_label, filename))
    entry['audio_files'] = audio_files

    if fixed and log_fn:
        log_fn('INFO', '"%s": %d file_id đã được đồng bộ lại theo thực tế trên Drive '
                        '(xóa mồ côi và/hoặc điền id thiếu) trong %s.'
               % (folder_label, fixed, METADATA_JSON_NAME))
    return fixed


def _download_opf_to_tempfile(access_token, file_id, log_fn=None):
    """Tải nội dung ``metadata.opf`` (bytes) từ Drive về, ghi ra 1 file
    tạm trên đĩa -- ``opf_parser.parse_opf_file`` cần 1 ĐƯỜNG DẪN file
    thật (dùng ``xml.etree.ElementTree.parse``), không nhận bytes trực
    tiếp. Nơi gọi chịu trách nhiệm ``os.remove`` file tạm này trong
    ``finally`` (kể cả khi parse lỗi) để không rác thư mục temp."""
    data = _with_retry(
        lambda: drive_api.download_file_bytes(access_token, file_id), log_fn, 'Tải metadata.opf')
    fd, tmp_path = tempfile.mkstemp(suffix='.opf', prefix='gdrive_sync_')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    return tmp_path


def _find_opf_and_cover(actual_files):
    """``actual_files``: ``{ten_file: file_id}`` thật trong 1 thư mục
    cuốn trên Drive. Nhận diện ``metadata.opf`` (không phân biệt hoa
    thường, giống ``scanner.scan_root_folder`` khi quét local) và cover
    (theo đúng thứ tự ưu tiên ``scanner.COVER_NAMES``, cùng quy ước với
    lúc upload bình thường). Trả về
    ``(opf_name, opf_id, cover_name, cover_id)`` -- từng phần tử là
    ``None`` nếu không tìm thấy."""
    opf_name = opf_id = None
    for fname, fid in actual_files.items():
        if fname.lower() == scanner.OPF_NAME.lower():
            opf_name, opf_id = fname, fid
            break

    cover_name = cover_id = None
    for candidate in scanner.COVER_NAMES:
        for fname, fid in actual_files.items():
            if fname.lower() == candidate:
                cover_name, cover_id = fname, fid
                break
        if cover_name:
            break

    return opf_name, opf_id, cover_name, cover_id


def _add_missing_books_from_drive(access_token, drive_tree, books, state, log_fn=None, check_abort=None):
    """Yêu cầu #4: với mỗi thư mục THẬT trên Drive (``drive_tree``, từ
    ``_scan_drive_tree``) mà KHÔNG có entry tương ứng trong
    ``metadata_public.json`` (``books``, so khớp theo ``folder_name``)
    -- trường hợp người dùng tự tạo/kéo-thả 1 thư mục sách nói thẳng
    lên Google Drive (đúng định dạng: có ``metadata.opf`` + các file
    audio), không đi qua "Đồng bộ Audiobooks lên Google Drive" của
    plugin nên chưa từng được ghi nhận -- tải ``metadata.opf`` về đọc
    lấy metadata thật (title, creators, chapters, ...) rồi ghi bổ sung
    1 entry mới vào ``books`` (in-place) + ``state`` (để lần "Đồng bộ
    Audiobooks lên Google Drive..." kế tiếp không tưởng nhầm cần tải
    lại các file này -- đã có sẵn ``file_id`` lấy đúng theo tên file
    thật trên Drive).

    Thư mục KHÔNG có ``metadata.opf`` bị bỏ qua (không phải 1 cuốn hợp
    lệ theo định dạng plugin yêu cầu). Thư mục CÓ ``metadata.opf``
    (cấu trúc hợp lệ) được thêm vào ``metadata_public.json`` kể cả khi
    chưa có file audio nào -- entry sẽ có ``audio_files`` rỗng và hiện
    cảnh báo "Không có audio" trong dialog Quản lý, để người dùng biết
    và có thể bổ sung audio / xóa sau. Trước đây các thư mục kiểu này
    bị bỏ qua hoàn toàn (ví dụ upload mới chỉ có opf + cover).

    Entry mới được đánh dấu ``entry['origin'] = 'drive_manual'`` và
    ``entry['root_path'] = ''`` (không có thư mục local tương ứng) để
    ``uploader.run_upload_sync`` biết PHẢI giữ lại entry này ở lần đồng
    bộ "Đồng bộ Audiobooks lên Google Drive..." kế tiếp thay vì xóa mất
    -- xem ``uploader._merge_missing_books``.

    Trả về số cuốn mới đã thêm.
    """
    known_folder_names = {(e.get('folder_name') or '') for e in books.values()}
    added = 0

    for folder_name, info in drive_tree.items():
        if check_abort and check_abort():
            raise CheckCancelled()
        if not folder_name or folder_name in known_folder_names:
            continue

        actual_files = info.get('files') or {}
        opf_name, opf_id, cover_name, cover_id = _find_opf_and_cover(actual_files)
        if not opf_name or not opf_id:
            continue  # không có metadata.opf -- không phải 1 cuốn hợp lệ

        # Mọi file không phải opf/cover được coi là audio (có thể rỗng)
        audio_names = [f for f in actual_files if f not in (opf_name, cover_name)]

        tmp_path = None
        try:
            tmp_path = _download_opf_to_tempfile(access_token, opf_id, log_fn=log_fn)
            opf_data = parse_opf_file(tmp_path)
        except Exception as e:
            if log_fn:
                log_fn('WARN', '"%s": tải/đọc metadata.opf thất bại (%s) -- bỏ qua thư mục này.'
                       % (folder_name, e))
            continue
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        audio_files = {fname: {'file_id': actual_files.get(fname) or '', 'size': None}
                       for fname in audio_names}

        key = 'drive_manual|%s' % folder_name
        added_at = int(time.time() * 1000)
        entry = {
            'folder_name': folder_name,
            'root_path': '',
            'drive_folder_id': info.get('id') or '',
            'added_at': added_at,
            'origin': 'drive_manual',
            'title': opf_data.get('title') or folder_name,
            'creators': opf_data.get('creators'),
            'publisher': opf_data.get('publisher'),
            'language': opf_data.get('language'),
            'description': opf_data.get('description'),
            'identifiers': opf_data.get('identifiers'),
            'modified': opf_data.get('modified'),
            'chapters': opf_data.get('chapters'),
            'extra_meta': opf_data.get('extra_meta'),
            'cover': {'filename': cover_name, 'file_id': cover_id or ''} if cover_name else None,
            'metadata_opf': {'filename': opf_name, 'file_id': opf_id or ''},
            'audio_files': audio_files,
        }
        books[key] = entry

        state.update_book(key, drive_folder_id=entry['drive_folder_id'], root_path='',
                           name=folder_name, added_at=added_at, origin='drive_manual')
        state.update_file(key, opf_name, file_id=opf_id)
        if cover_name:
            state.update_file(key, cover_name, file_id=cover_id or '')
        for fname in audio_names:
            state.update_file(key, fname, file_id=actual_files.get(fname) or '')

        added += 1
        if log_fn:
            if audio_names:
                log_fn('INFO', '"%s": phát hiện thư mục sách mới trên Drive (upload thủ công, %d file audio) -- '
                                'đã đọc metadata.opf và thêm vào %s.'
                       % (folder_name, len(audio_names), METADATA_JSON_NAME))
            else:
                log_fn('WARN', '"%s": có metadata.opf trên Drive nhưng chưa có file audio -- '
                                'vẫn thêm vào %s (entry không audio, hiện cảnh báo trong Quản lý).'
                       % (folder_name, METADATA_JSON_NAME))

    return added


def _count_real_audio_files(entry, actual_files):
    """Đếm số file audio THẬT đang có trong thư mục cuốn này trên Drive
    (``actual_files``, key = tên file) -- tức mọi file trừ
    ``metadata.opf`` và cover, xác định theo đúng tên file mà entry này
    tự ghi nhận (không đoán theo phần mở rộng, để nhất quán với cách
    ``uploader._build_book_metadata_entry`` đã phân loại lúc tải lên:
    mọi thứ không phải ``folder.opf_path``/``folder.cover_path`` đều là
    audio, bất kể đuôi file gì)."""
    exclude = set()
    opf_name = (entry.get('metadata_opf') or {}).get('filename')
    if opf_name:
        exclude.add(opf_name)
    cover_name = (entry.get('cover') or {}).get('filename')
    if cover_name:
        exclude.add(cover_name)
    return sum(1 for fname in actual_files if fname not in exclude)


def run_check(access_token, log_fn=None, progress_fn=None, check_abort=None, scan_progress_fn=None,
              library_name=None):
    """Blocking. ``log_fn(level, message)`` và ``progress_fn(done, total)``
    (tính theo SỐ CUỐN có trong json, không phải số file) được gọi trên
    cùng thread gọi hàm này -- ``worker.AudiobookCheckThread`` chuyển
    tiếp chúng thành Qt signal, đúng pattern ``uploader.run_upload_sync``.

    ``scan_progress_fn(done, total, current_folder_name)`` là RIÊNG cho
    giai đoạn quét cây thư mục thật trên Drive (``_scan_drive_tree``,
    xảy ra TRƯỚC vòng lặp đối chiếu từng cuốn dùng ``progress_fn`` ở
    trên) -- tách riêng vì đơn vị tính khác nhau (thư mục thật trên
    Drive so với số cuốn có trong ``metadata_public.json``) và để dialog
    hiện được tên thư mục đang xem, giống \"Check Library on Drive\".
    Trả về dict thống kê.
    """
    from .uploader import DEFAULT_LIBRARY_NAME
    lib_name = (library_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    state = AudiobookState(library_name=lib_name)
    stats = {
        'books_checked': 0, 'books_removed': 0, 'files_fixed': 0, 'books_added': 0,
        'source': None, 'changed': False, 'metadata_created': False,
        'no_audio_books': [],  # list[str] tiêu đề/tên thư mục -- chỉ ghi nhận, không tự xóa
        'library_name': lib_name,
        'imported_from_existing': 0,
    }

    # 1) Gắn với thư mục thư viện đã có trên Drive (kể cả do app khác tạo
    #    cùng OAuth client) -- không bắt buộc đã từng sync bằng app này.
    root_id = discover_library_root(access_token, lib_name, state, log_fn=log_fn)
    if not root_id:
        if log_fn:
            log_fn('WARN', 'Chưa có thư mục "%s" nào trên Drive (hoặc app không có quyền '
                           'nhìn thấy -- scope drive.file chỉ thấy thư mục do cùng Client OAuth tạo).'
                   % lib_name)
        return stats

    if check_abort and check_abort():
        raise CheckCancelled()

    # 2) Tải metadata_public.json (cache id / tìm theo tên trong folder / backup).
    #    Nếu chưa có metadata, vẫn tiếp tục quét Drive và tạo mới từ các thư
    #    mục audiobook hợp lệ, giống luồng Check của Chrome extension.
    payload, source = download_metadata_json(access_token, state, log_fn, root_id=root_id)
    stats['source'] = source
    if payload is None:
        payload = {
            'generated_at': datetime.now().isoformat(sep='T', timespec='seconds'),
            'source': 'audiobookgdrive_check',
            'drive_folder_url': 'https://drive.google.com/drive/folders/%s?usp=drive_link' % root_id,
            'audiobooks': {},
        }
        stats['source'] = 'created'
        stats['metadata_created'] = True
        if log_fn:
            log_fn('WARN', 'Chưa tìm thấy %s trong thư mục "%s" -- sẽ quét các thư mục con '
                           'có metadata.opf và tạo file metadata mới, giống extension.'
                   % (METADATA_JSON_NAME, lib_name))
    if source == 'backup' and log_fn:
        log_fn('WARN', 'Đang đối chiếu bằng bản backup offline (có thể không phải bản mới nhất).')

    # 3) Nạp state local từ json (để Đồng bộ / Quản lý sau này dùng chung dữ liệu)
    if not stats['metadata_created']:
        stats['imported_from_existing'] = import_state_from_payload(state, payload, log_fn=log_fn)

    if log_fn:
        log_fn('INFO', 'Đang quét cây thư mục "%s" thật trên Drive...' % lib_name)
    drive_tree = _scan_drive_tree(
        access_token, root_id, log_fn=log_fn, progress_fn=scan_progress_fn,
        check_abort=check_abort, library_name=lib_name, resume=True)

    books = payload.get('audiobooks') or {}
    total = len(books)
    done = 0
    for key, entry in list(books.items()):
        if check_abort and check_abort():
            raise CheckCancelled()
        done += 1
        if progress_fn:
            progress_fn(done, total)
        stats['books_checked'] += 1

        folder_name = entry.get('folder_name') or ''
        actual = drive_tree.get(folder_name)
        cached_folder_id = entry.get('drive_folder_id')

        # Không còn thư mục thật nào cùng tên trên Drive, hoặc thư mục
        # cùng tên đó lại có id khác hẳn (thư mục cũ bị xóa rồi có ai đó
        # tạo lại thư mục trùng tên) -- coi như cả cuốn này đã mất.
        if not actual or (cached_folder_id and actual['id'] != cached_folder_id):
            del books[key]
            state.remove_book(key)
            stats['books_removed'] += 1
            stats['changed'] = True
            if log_fn:
                log_fn('INFO', '"%s": thư mục đã bị xóa trên Drive -- đã xóa khỏi %s.'
                       % (folder_name, METADATA_JSON_NAME))
            continue

        fixed = _fix_book_entry(entry, actual['files'], state, key, log_fn)
        if fixed:
            stats['files_fixed'] += fixed
            stats['changed'] = True

        # Đếm lại số audio THẬT sau khi đã sửa id ở trên (dùng
        # actual['files'] -- thực tế trên Drive -- chứ không dùng
        # entry['audio_files'] đang có trong json, để không bỏ sót
        # trường hợp json có ghi nhận nhưng id đã bị _fix_book_entry
        # xóa/hoặc chưa từng khớp).
        if _count_real_audio_files(entry, actual['files']) == 0:
            title = entry.get('title') or folder_name or key
            stats['no_audio_books'].append(title)
            if log_fn:
                log_fn('WARN', '"%s": không có file audio nào trong thư mục trên Drive '
                                '(có thể do 1 lượt đồng bộ trước bị ngắt giữa chừng) -- '
                                'kiểm tra lại qua "Quản lý Audiobooks trên Drive...".' % title)

    if check_abort and check_abort():
        raise CheckCancelled()
    added = _add_missing_books_from_drive(
        access_token, drive_tree, books, state, log_fn=log_fn, check_abort=check_abort)
    if added:
        stats['books_added'] = added
        stats['changed'] = True
    elif stats['metadata_created']:
        if log_fn:
            log_fn('ERROR', 'Không có thư mục con hợp lệ để tạo %s (cần có metadata.opf).'
                   % METADATA_JSON_NAME)
        _clear_checkpoint(lib_name, root_id)
        return stats

    if not stats['changed']:
        if log_fn:
            if stats['no_audio_books']:
                log_fn('INFO', 'Không tìm thấy sai lệch nào -- %s đã khớp với thực tế trên Drive '
                                '(nhưng có %d cuốn không có audio, xem cảnh báo ở trên).'
                       % (METADATA_JSON_NAME, len(stats['no_audio_books'])))
            else:
                log_fn('INFO', 'Không tìm thấy sai lệch nào -- %s đã khớp với thực tế trên Drive.'
                       % METADATA_JSON_NAME)
        _clear_checkpoint(lib_name, root_id)
        return stats

    payload['audiobooks'] = books
    payload['generated_at'] = datetime.now().isoformat(sep='T', timespec='seconds')
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')

    existing_id = state.get_metadata_json_id() or None
    file_id = _with_retry(
        lambda: _upload_bytes_resumable(
            access_token, data, METADATA_JSON_NAME, root_id, 'application/json', existing_id),
        log_fn, METADATA_JSON_NAME)
    state.set_metadata_json_id(file_id or existing_id or '')
    backup.save_backup(data, log_fn, library_name=lib_name)

    if log_fn:
        log_fn('INFO', 'Đã cập nhật lại %s trên Drive: %d cuốn bị xóa, %d file đã sửa id, %d cuốn mới thêm.'
               % (METADATA_JSON_NAME, stats['books_removed'], stats['files_fixed'], stats['books_added']))
    _clear_checkpoint(lib_name, root_id)
    return stats
