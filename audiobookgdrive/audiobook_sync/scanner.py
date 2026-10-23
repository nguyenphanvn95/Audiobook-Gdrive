# -*- coding: utf-8 -*-
"""
audiobook_sync/scanner.py
==========================

Duyệt các "Audiobook Root Folder" cấu hình trong Settings (tab
"Audiobook Sync") để tìm các thư mục con là 1 cuốn sách nói, đúng theo mô
tả trong yêu cầu nâng cấp: mỗi thư mục con TRỰC TIẾP của 1 root folder là
1 cuốn (chứa các file audio, 1 file ``metadata.opf``, 1 file
``cover.jpg``/``cover.png``) -- KHÔNG đệ quy sâu hơn. Toàn bộ file nằm
trực tiếp trong thư mục con đó được coi là thuộc về cuốn sách và được tải
lên Drive nguyên vẹn (yêu cầu 2.2: "upload nguyên vẹn thư mục đó").

Thư mục con không có ``metadata.opf`` bị bỏ qua (chỉ log cảnh báo, không
phải lỗi) -- coi như chưa phải 1 cuốn sách hoàn chỉnh (ví dụ còn đang tải
dở).
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from os import listdir, path

OPF_NAME = 'metadata.opf'
COVER_NAMES = ('cover.jpg', 'cover.jpeg', 'cover.png')


class AudiobookFolder:
    """1 thư mục con = 1 cuốn sách nói đã tìm thấy khi quét."""

    def __init__(self, root_path, name, abs_path, opf_path, cover_path, other_files):
        self.root_path = root_path
        self.name = name
        self.abs_path = abs_path
        self.opf_path = opf_path
        self.cover_path = cover_path
        # Mọi file khác (audio, v.v.) nằm trực tiếp trong abs_path, không
        # gồm metadata.opf/cover -- đã sort để thứ tự ổn định giữa các lần quét.
        self.other_files = other_files

    @property
    def key(self):
        """Khoá duy nhất dùng cho state_store: gộp cả root_path lẫn tên
        thư mục, để 2 root folder khác nhau vẫn có thể chứa 1 cuốn cùng
        tên mà không bị đè state lên nhau."""
        return '%s|%s' % (self.root_path, self.name)

    def all_files(self):
        """Tất cả file cần đồng bộ của cuốn này (audio + opf + cover)."""
        files = list(self.other_files)
        files.append(path.basename(self.opf_path))
        if self.cover_path:
            files.append(path.basename(self.cover_path))
        return files


def scan_root_folder(root_path, log_fn=None):
    """Trả về list :class:`AudiobookFolder` cho mỗi thư mục con trực tiếp
    của ``root_path`` có chứa ``metadata.opf``."""
    results = []
    try:
        entries = sorted(listdir(root_path))
    except OSError as e:
        if log_fn:
            log_fn('WARN', 'Không đọc được thư mục gốc "%s": %s' % (root_path, e))
        return results

    for name in entries:
        abs_path = path.join(root_path, name)
        if not path.isdir(abs_path):
            continue
        try:
            children = listdir(abs_path)
        except OSError as e:
            if log_fn:
                log_fn('WARN', 'Không đọc được thư mục "%s": %s' % (abs_path, e))
            continue

        by_lower = {c.lower(): c for c in children}

        opf_real = by_lower.get(OPF_NAME)
        if not opf_real:
            if log_fn:
                log_fn('WARN', 'Bỏ qua "%s": không có metadata.opf.' % abs_path)
            continue
        opf_path = path.join(abs_path, opf_real)

        cover_path = None
        for cover_name in COVER_NAMES:
            real = by_lower.get(cover_name)
            if real:
                cover_path = path.join(abs_path, real)
                break

        skip_names = {opf_real.lower()}
        if cover_path:
            skip_names.add(path.basename(cover_path).lower())

        other_files = []
        for c in children:
            full = path.join(abs_path, c)
            if not path.isfile(full):
                continue
            if c.lower() in skip_names:
                continue
            other_files.append(c)

        results.append(AudiobookFolder(
            root_path, name, abs_path, opf_path, cover_path, sorted(other_files)))

    return results


def scan_book_folder(book_path, root_path=None, log_fn=None):
    """Return one :class:`AudiobookFolder` for an explicitly selected book
    directory, or ``None`` if it is not valid.

    This supports the upload dialog's "1 cuốn (thư mục)" mode. Unlike
    :func:`scan_root_folder`, it does not scan sibling folders.
    """
    book_path = path.abspath(book_path or '')
    if not path.isdir(book_path):
        if log_fn:
            log_fn('WARN', 'Thư mục sách không hợp lệ: "%s".' % book_path)
        return None
    root_path = path.abspath(root_path or path.dirname(book_path))
    name = path.basename(book_path.rstrip('\\/'))

    try:
        children = listdir(book_path)
    except OSError as e:
        if log_fn:
            log_fn('WARN', 'Không đọc được thư mục "%s": %s' % (book_path, e))
        return None

    by_lower = {c.lower(): c for c in children}
    opf_real = by_lower.get(OPF_NAME)
    if not opf_real:
        if log_fn:
            log_fn('WARN', 'Bỏ qua "%s": không có metadata.opf.' % book_path)
        return None
    opf_path = path.join(book_path, opf_real)

    cover_path = None
    for cover_name in COVER_NAMES:
        real = by_lower.get(cover_name)
        if real:
            cover_path = path.join(book_path, real)
            break

    skip_names = {opf_real.lower()}
    if cover_path:
        skip_names.add(path.basename(cover_path).lower())

    other_files = []
    for c in children:
        full = path.join(book_path, c)
        if not path.isfile(full):
            continue
        if c.lower() in skip_names:
            continue
        other_files.append(c)

    return AudiobookFolder(
        root_path, name, book_path, opf_path, cover_path, sorted(other_files))


def scan_book_folders(book_paths, log_fn=None):
    results = []
    for book_path in (book_paths or []):
        folder = scan_book_folder(book_path, log_fn=log_fn)
        if folder is not None:
            results.append(folder)
    return results


def scan_all(root_paths, log_fn=None):
    all_folders = []
    for root_path in (root_paths or []):
        if not root_path:
            continue
        all_folders.extend(scan_root_folder(root_path, log_fn=log_fn))
    return all_folders
