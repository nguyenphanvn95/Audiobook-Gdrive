# -*- coding: utf-8 -*-
"""
audiobook_sync/state_store.py
===============================

Bookkeeping "cái gì đã có trên Drive" cho Audiobook Sync -- hỗ trợ
NHIỀU thư viện (library) trên Drive. Mỗi thư viện là 1 thư mục gốc
(mặc định ``Audiobooks``, có thể thêm tên custom) và có state riêng:

    {
      "libraries": {
        "Audiobooks": {
          "audiobooks_root_folder_id": "...",
          "metadata_json_file_id": "...",
          "books": { "<root_path>|<ten>": {...} }
        },
        "MyLib": { ... }
      }
    }

Tương thích ngược: nếu file state cũ chỉ có
``audiobooks_root_folder_id`` / ``books`` ở cấp gốc (bản single-library),
lần đọc đầu sẽ tự migrate sang ``libraries["Audiobooks"]``.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from threading import RLock

from audiobookgdrive.jsonconfig import JSONConfig

DEFAULT_LIBRARY_NAME = 'Audiobooks'

_store = JSONConfig('plugins/gdrive_sync_audiobook_state')
_store.defaults['libraries'] = {}
# Giữ defaults cũ để migrate
_store.defaults['audiobooks_root_folder_id'] = ''
_store.defaults['metadata_json_file_id'] = ''
_store.defaults['books'] = {}
_lock = RLock()


def _migrate_legacy_if_needed():
    """Chuyển state single-library cũ sang ``libraries[Audiobooks]``."""
    libraries = _store.get('libraries') or {}
    if libraries:
        return
    root_id = _store.get('audiobooks_root_folder_id') or ''
    meta_id = _store.get('metadata_json_file_id') or ''
    books = _store.get('books') or {}
    if root_id or meta_id or books:
        libraries[DEFAULT_LIBRARY_NAME] = {
            'audiobooks_root_folder_id': root_id,
            'metadata_json_file_id': meta_id,
            'books': books,
        }
        _store['libraries'] = libraries
        # Xoá field cũ để tránh nhầm lẫn
        _store['audiobooks_root_folder_id'] = ''
        _store['metadata_json_file_id'] = ''
        _store['books'] = {}


def _lib_entry(library_name):
    _migrate_legacy_if_needed()
    name = (library_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    libraries = _store.get('libraries') or {}
    if name not in libraries:
        libraries[name] = {
            'audiobooks_root_folder_id': '',
            'metadata_json_file_id': '',
            'books': {},
        }
        _store['libraries'] = libraries
    return libraries[name], name


def rename_library_state(old_name, new_name):
    old_name = (old_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    new_name = (new_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME
    if old_name == new_name:
        return
    with _lock:
        _migrate_legacy_if_needed()
        libraries = _store.get('libraries') or {}
        entry = libraries.pop(old_name, {
            'audiobooks_root_folder_id': '',
            'metadata_json_file_id': '',
            'books': {},
        })
        libraries[new_name] = entry
        _store['libraries'] = libraries


class AudiobookState:
    """State gắn với 1 thư viện (library folder name trên Drive)."""

    def __init__(self, library_name=None):
        self.library_name = (library_name or DEFAULT_LIBRARY_NAME).strip() or DEFAULT_LIBRARY_NAME

    def get_root_folder_id(self):
        with _lock:
            entry, _ = _lib_entry(self.library_name)
            return entry.get('audiobooks_root_folder_id') or ''

    def set_root_folder_id(self, folder_id):
        with _lock:
            entry, name = _lib_entry(self.library_name)
            entry['audiobooks_root_folder_id'] = folder_id
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def reset_root(self):
        """Xoá sạch mọi id đã cache của thư viện này."""
        with _lock:
            entry, name = _lib_entry(self.library_name)
            entry['audiobooks_root_folder_id'] = ''
            entry['metadata_json_file_id'] = ''
            entry['books'] = {}
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def get_metadata_json_id(self):
        with _lock:
            entry, _ = _lib_entry(self.library_name)
            return entry.get('metadata_json_file_id') or ''

    def set_metadata_json_id(self, file_id):
        with _lock:
            entry, name = _lib_entry(self.library_name)
            entry['metadata_json_file_id'] = file_id
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def get_book(self, key):
        with _lock:
            entry, _ = _lib_entry(self.library_name)
            return (entry.get('books') or {}).get(key)

    def update_book(self, key, **kwargs):
        with _lock:
            entry, name = _lib_entry(self.library_name)
            books = entry.setdefault('books', {})
            book_entry = books.setdefault(key, {})
            book_entry.update(kwargs)
            entry['books'] = books
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def update_file(self, key, filename, **kwargs):
        with _lock:
            entry, name = _lib_entry(self.library_name)
            books = entry.setdefault('books', {})
            book_entry = books.setdefault(key, {})
            files = book_entry.setdefault('files', {})
            file_entry = files.setdefault(filename, {})
            file_entry.update(kwargs)
            book_entry['files'] = files
            books[key] = book_entry
            entry['books'] = books
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def remove_stale_files(self, key, current_filenames):
        with _lock:
            entry, name = _lib_entry(self.library_name)
            books = entry.setdefault('books', {})
            book_entry = books.setdefault(key, {})
            files = book_entry.get('files') or {}
            for fname in list(files.keys()):
                if fname not in current_filenames:
                    files.pop(fname, None)
            book_entry['files'] = files
            books[key] = book_entry
            entry['books'] = books
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def all_keys(self):
        with _lock:
            entry, _ = _lib_entry(self.library_name)
            return list((entry.get('books') or {}).keys())

    def remove_book(self, key):
        with _lock:
            entry, name = _lib_entry(self.library_name)
            books = entry.setdefault('books', {})
            books.pop(key, None)
            entry['books'] = books
            libraries = _store['libraries']
            libraries[name] = entry
            _store['libraries'] = libraries

    def import_books_bulk(self, books_map):
        """Nạp hàng loạt ``{key: book_entry}`` chỉ ghi đĩa 1 lần.

        ``book_entry`` có thể chứa ``drive_folder_id``, ``root_path``, ``name``,
        ``added_at``, ``origin``, ``files`` (dict filename -> {file_id, size, ...}).
        """
        if not books_map:
            return 0
        with _lock:
            _store.begin_batch()
            try:
                entry, name = _lib_entry(self.library_name)
                books = entry.setdefault('books', {})
                for key, book_entry in books_map.items():
                    if not isinstance(book_entry, dict):
                        continue
                    existing = books.setdefault(key, {})
                    for k in ('drive_folder_id', 'root_path', 'name', 'added_at', 'origin'):
                        if book_entry.get(k) is not None:
                            existing[k] = book_entry[k]
                    files_in = book_entry.get('files') or {}
                    if files_in:
                        files = existing.setdefault('files', {})
                        for fname, finfo in files_in.items():
                            if not isinstance(finfo, dict):
                                continue
                            fe = files.setdefault(fname, {})
                            fe.update({kk: vv for kk, vv in finfo.items()
                                       if kk in ('file_id', 'size', 'mtime', 'digest') and vv is not None})
                        existing['files'] = files
                    books[key] = existing
                entry['books'] = books
                libraries = _store['libraries']
                libraries[name] = entry
                _store['libraries'] = libraries
            finally:
                _store.end_batch()
        return len(books_map)
