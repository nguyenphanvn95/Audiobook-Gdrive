# -*- coding: utf-8 -*-
"""
drive_api.py
============

Google Drive API v3 REST helpers (requirement 16: "Use Drive API v3, no
legacy API").

``parse_folder_id``, ``list_folder_children`` and ``find_child_folder``
are cloned near-verbatim from Public GDrive Library's ``gdrive_api.py``
(same pagination-safe ``files.list`` calls, same query-escaping rules).
Everything else here (folder creation, simple + **resumable** upload,
update, delete, permissions/public-link management) is new: the source
plugin was strictly read-only (``drive.readonly`` scope), this plugin
needs full CRUD.

Every network call goes through :func:`_request`, which centralizes
error mapping (401/403/404/429/5xx) the same way BookFusion's
``upload_worker.complete_req`` centralizes its ``QNetworkReply`` error
handling, just adapted to ``urllib``/HTTP status codes instead of Qt
network error enums.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import json
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request

FOLDER_MIME = 'application/vnd.google-apps.folder'
FILES_ENDPOINT = 'https://www.googleapis.com/drive/v3/files'
UPLOAD_ENDPOINT = 'https://www.googleapis.com/upload/drive/v3/files'

FILE_FIELDS = 'id,name,mimeType,size,md5Checksum,webViewLink,webContentLink,modifiedTime,createdTime,parents'
LIST_FIELDS = 'nextPageToken,files(%s)' % FILE_FIELDS


class DriveApiError(Exception):
    """Raised for non-retryable Drive API errors (bad auth, not found, ...)."""


class DriveNotFoundError(DriveApiError):
    """Specifically a 404 -- lets callers special-case "this Drive object
    was deleted (e.g. manually, on drive.google.com) since we last cached
    its id" and recover, instead of just failing the whole sync."""


class DriveRetryableError(Exception):
    """Raised for transient errors (timeouts, 429, 5xx) callers should retry."""


def parse_folder_id(value):
    value = (value or '').strip()
    if not value:
        raise ValueError('No Google Drive folder link/ID given.')

    match = re.search(r'drive\.google\.com/drive/folders/([A-Za-z0-9_-]+)', value)
    if match:
        return match.group(1)
    match = re.search(r'drive\.google\.com/drive/u/\d+/folders/([A-Za-z0-9_-]+)', value)
    if match:
        return match.group(1)
    match = re.search(r'[?&]id=([A-Za-z0-9_-]{10,})', value)
    if match:
        return match.group(1)
    if re.fullmatch(r'[A-Za-z0-9_-]{10,}', value):
        return value
    raise ValueError('Could not recognize a folder id in: %r' % value)


def _headers(access_token, extra=None):
    headers = {'Authorization': 'Bearer %s' % access_token}
    if extra:
        headers.update(extra)
    return headers


def _request(method, url, access_token, data=None, headers=None, timeout=30, parse_json=True):
    req = urllib.request.Request(url, data=data, method=method, headers=_headers(access_token, headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if parse_json and body:
                return json.loads(body.decode('utf-8')), dict(resp.headers)
            return body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        if e.code == 401:
            raise DriveApiError('Google sign-in expired or was revoked (401). Please sign in again.')
        if e.code == 404:
            raise DriveNotFoundError('Not found (404): %s' % body[:300])
        if e.code == 403 and 'rateLimitExceeded' in body:
            raise DriveRetryableError('Drive API rate limit exceeded (403).')
        if e.code == 429 or e.code >= 500:
            raise DriveRetryableError('Drive API returned HTTP %d: %s' % (e.code, body[:300]))
        raise DriveApiError('Drive API HTTP error %d: %s' % (e.code, body[:400]))
    except urllib.error.URLError as e:
        raise DriveRetryableError('Could not connect to Google Drive API: %s' % e)
    except TimeoutError as e:
        raise DriveRetryableError('Timed out talking to Google Drive API: %s' % e)


def _api_get(url, access_token, timeout=30):
    data, _ = _request('GET', url, access_token, timeout=timeout)
    return data


def get_file_metadata(access_token, file_id, timeout=30):
    url = '%s/%s?%s' % (
        FILES_ENDPOINT,
        urllib.parse.quote(file_id),
        urllib.parse.urlencode({'fields': FILE_FIELDS, 'supportsAllDrives': 'true'}),
    )
    return _api_get(url, access_token, timeout=timeout)


def file_exists(access_token, file_id, timeout=30):
    """Cheap existence check, used to recover when a previously-cached
    Drive object (e.g. this library's root folder) was deleted directly
    on drive.google.com since we last cached its id -- rather than
    failing every subsequent sync with a 404."""
    try:
        get_file_metadata(access_token, file_id, timeout=timeout)
        return True
    except DriveNotFoundError:
        return False


def list_folder_children(access_token, folder_id, timeout=30, page_size=1000):
    """Fully paginated listing of the direct children of ``folder_id``."""
    items = []
    page_token = None
    while True:
        params = {
            'q': "'%s' in parents and trashed = false" % folder_id,
            'fields': LIST_FIELDS,
            'orderBy': 'folder,name',
            'pageSize': str(page_size),
            'supportsAllDrives': 'true',
            'includeItemsFromAllDrives': 'true',
            'corpora': 'allDrives',
        }
        if page_token:
            params['pageToken'] = page_token
        url = FILES_ENDPOINT + '?' + urllib.parse.urlencode(params)
        data = _api_get(url, access_token, timeout=timeout)
        items.extend(data.get('files', []))
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    return items


def list_root_folders(access_token, timeout=30, page_size=200):
    """List direct, non-trashed folders in the user's My Drive root.

    This mirrors the Chrome extension's folder picker: before Check/Manage
    we can discover audiobook libraries that already exist on Drive instead
    of relying only on locally configured names.
    """
    items = []
    page_token = None
    while True:
        params = {
            'q': "mimeType = '%s' and 'root' in parents and trashed = false" % FOLDER_MIME,
            'fields': 'nextPageToken,files(id,name,mimeType)',
            'orderBy': 'name',
            'pageSize': str(page_size),
            'supportsAllDrives': 'true',
            'includeItemsFromAllDrives': 'true',
            'corpora': 'allDrives',
        }
        if page_token:
            params['pageToken'] = page_token
        url = FILES_ENDPOINT + '?' + urllib.parse.urlencode(params)
        data = _api_get(url, access_token, timeout=timeout)
        items.extend(data.get('files', []))
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    return items


def _escape_query_name(name):
    return name.replace('\\', '\\\\').replace("'", "\\'")


def find_child_folder(access_token, parent_id, name, timeout=30):
    q = "'%s' in parents and name = '%s' and mimeType = '%s' and trashed = false" % (
        parent_id, _escape_query_name(name), FOLDER_MIME
    )
    params = {
        'q': q,
        'fields': 'files(id,name)',
        'pageSize': '10',
        'supportsAllDrives': 'true',
        'includeItemsFromAllDrives': 'true',
        'corpora': 'allDrives',
    }
    url = FILES_ENDPOINT + '?' + urllib.parse.urlencode(params)
    data = _api_get(url, access_token, timeout=timeout)
    files = data.get('files') or []
    return files[0]['id'] if files else None


def find_child_file(access_token, parent_id, name, timeout=30):
    q = "'%s' in parents and name = '%s' and mimeType != '%s' and trashed = false" % (
        parent_id, _escape_query_name(name), FOLDER_MIME
    )
    params = {
        'q': q,
        'fields': 'files(%s)' % FILE_FIELDS,
        'pageSize': '10',
        'supportsAllDrives': 'true',
        'includeItemsFromAllDrives': 'true',
        'corpora': 'allDrives',
    }
    url = FILES_ENDPOINT + '?' + urllib.parse.urlencode(params)
    data = _api_get(url, access_token, timeout=timeout)
    files = data.get('files') or []
    return files[0] if files else None


def download_file_bytes(access_token, file_id, timeout=60):
    """Download a small file's raw content into memory via ``?alt=media``,
    using the caller's OAuth Bearer token (unlike
    ``public_api.download_file_bytes_public``, which uses an unauthenticated
    ``?key=`` API-key request for public links). Used by
    ``device_sync/manifest.py`` to read ``calibre_sync_manifest.json``,
    which is always small (JSON text), so no resumable/ranged download is
    needed here."""
    url = '%s/%s?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({'alt': 'media', 'supportsAllDrives': 'true'}),
    )
    body, _ = _request('GET', url, access_token, timeout=timeout, parse_json=False)
    return body


def download_file_to_path(access_token, file_id, local_path, timeout=180, chunk_size=1024 * 1024):
    """Stream a Drive file's raw content to ``local_path``.

    Used for audiobook offline download where audio files may be too large to
    comfortably keep in memory.
    """
    url = '%s/%s?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({'alt': 'media', 'supportsAllDrives': 'true'}),
    )
    req = urllib.request.Request(url, method='GET', headers=_headers(access_token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(local_path, 'wb') as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        if e.code == 401:
            raise DriveApiError('Google sign-in expired or was revoked (401). Please sign in again.')
        if e.code == 404:
            raise DriveNotFoundError('Not found (404): %s' % body[:300])
        if e.code == 403 and 'rateLimitExceeded' in body:
            raise DriveRetryableError('Drive API rate limit exceeded (403).')
        if e.code == 429 or e.code >= 500:
            raise DriveRetryableError('Drive API returned HTTP %d: %s' % (e.code, body[:300]))
        raise DriveApiError('Drive API HTTP error %d: %s' % (e.code, body[:400]))
    except urllib.error.URLError as e:
        raise DriveRetryableError('Could not connect to Google Drive API: %s' % e)
    except TimeoutError as e:
        raise DriveRetryableError('Timed out talking to Google Drive API: %s' % e)


def create_folder(access_token, name, parent_id, timeout=30):
    metadata = {'name': name, 'mimeType': FOLDER_MIME}
    if parent_id:
        metadata['parents'] = [parent_id]
    url = FILES_ENDPOINT + '?' + urllib.parse.urlencode({'fields': 'id', 'supportsAllDrives': 'true'})
    data, _ = _request(
        'POST', url, access_token,
        data=json.dumps(metadata).encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=UTF-8'},
        timeout=timeout,
    )
    return data['id']


def find_or_create_folder(access_token, name, parent_id, timeout=30):
    existing = find_child_folder(access_token, parent_id, name, timeout=timeout)
    if existing:
        return existing
    return create_folder(access_token, name, parent_id, timeout=timeout)


def ensure_path(access_token, root_id, parts, folder_cache=None, timeout=30):
    """Resolve/create a nested folder path under ``root_id``, one segment
    at a time (e.g. ``["Jane Austen", "Pride and Prejudice (12)"]``).
    ``folder_cache`` (dict of (parent_id, name) -> id) lets callers share
    a cache across many books whose author folder is the same, so a
    library-wide sync does not re-resolve the same author folder for
    every book in it."""
    cache = folder_cache if folder_cache is not None else {}
    current = root_id
    for part in parts:
        key = (current, part)
        if key in cache:
            current = cache[key]
            continue
        current = find_or_create_folder(access_token, part, current, timeout=timeout)
        cache[key] = current
    return current


def has_children(access_token, folder_id, timeout=30):
    """Cheap existence check ("does this folder currently have any
    non-trashed direct child, file or folder?") used to decide whether a
    now-possibly-empty parent folder can be safely removed, without
    paying for a full :func:`list_folder_children` pagination."""
    params = {
        'q': "'%s' in parents and trashed = false" % folder_id,
        'fields': 'files(id)',
        'pageSize': '1',
        'supportsAllDrives': 'true',
        'includeItemsFromAllDrives': 'true',
        'corpora': 'allDrives',
    }
    url = FILES_ENDPOINT + '?' + urllib.parse.urlencode(params)
    data = _api_get(url, access_token, timeout=timeout)
    return bool(data.get('files'))


def delete_file(access_token, file_id, timeout=30):
    url = '%s/%s?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({'supportsAllDrives': 'true'}),
    )
    _request('DELETE', url, access_token, timeout=timeout, parse_json=False)


def cleanup_empty_ancestors(access_token, start_folder_id, root_id, log_fn=None, timeout=30):
    """Walk upward from ``start_folder_id`` (typically the folder that
    used to *contain* a book folder which was just deleted/moved away --
    e.g. an author folder), deleting each folder for as long as it is
    now empty, stopping at ``root_id`` (never deleted/inspected even if
    it happens to be empty) or at the first folder that still has
    content. This is what keeps an author/series folder from lingering
    forever on Drive after its last book was removed.

    Best-effort: any error (including the folder having already been
    deleted, e.g. by a concurrent run) simply stops the walk where it
    is -- it never raises, so a cleanup problem never breaks the delete
    operation that triggered it.

    Returns the list of folder ids that were removed, root-ward.
    """
    deleted = []
    current = start_folder_id
    while current and current != root_id:
        try:
            if has_children(access_token, current, timeout=timeout):
                break
            meta = get_file_metadata(access_token, current, timeout=timeout)
            parents = meta.get('parents') or []
            delete_file(access_token, current, timeout=timeout)
        except DriveNotFoundError:
            break
        except Exception as e:
            if log_fn:
                log_fn('WARN', 'Could not clean up empty Drive folder %s: %s' % (current, e))
            break
        deleted.append(current)
        if log_fn:
            log_fn('INFO', 'Removed now-empty Drive folder: %s' % current)
        current = parents[0] if parents else None
    return deleted


def move_file(access_token, file_id, new_parent_id, old_parent_id, timeout=30):
    url = '%s/%s?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({
            'addParents': new_parent_id,
            'removeParents': old_parent_id,
            'fields': 'id,parents',
            'supportsAllDrives': 'true',
        }),
    )
    _request('PATCH', url, access_token, data=b'{}',
              headers={'Content-Type': 'application/json'}, timeout=timeout)


def update_file_metadata(access_token, file_id, metadata, timeout=30):
    url = '%s/%s?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({'fields': 'id', 'supportsAllDrives': 'true'}),
    )
    data, _ = _request(
        'PATCH', url, access_token,
        data=json.dumps(metadata).encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=UTF-8'},
        timeout=timeout,
    )
    return data


# ---------------------------------------------------------------------
# Simple (non-resumable) upload -- used for small files: metadata.opf,
# metadata_public.json, cover.jpg. Uses multipart/related per the Drive
# API "simple and multipart upload" spec.
# ---------------------------------------------------------------------

def upload_small_file(access_token, name, parent_id, mime_type, content_bytes,
                       existing_file_id=None, timeout=60):
    boundary = 'gdrive_sync_boundary_%d' % int(time.time() * 1000)
    metadata = {'name': name}
    if parent_id and not existing_file_id:
        metadata['parents'] = [parent_id]

    body = bytearray()
    body += ('--%s\r\n' % boundary).encode('utf-8')
    body += b'Content-Type: application/json; charset=UTF-8\r\n\r\n'
    body += json.dumps(metadata).encode('utf-8')
    body += ('\r\n--%s\r\n' % boundary).encode('utf-8')
    body += ('Content-Type: %s\r\n\r\n' % mime_type).encode('utf-8')
    body += content_bytes
    body += ('\r\n--%s--' % boundary).encode('utf-8')

    if existing_file_id:
        url = '%s/%s?%s' % (
            UPLOAD_ENDPOINT, urllib.parse.quote(existing_file_id),
            urllib.parse.urlencode({'uploadType': 'multipart', 'fields': 'id', 'supportsAllDrives': 'true'}),
        )
        method = 'PATCH'
    else:
        url = UPLOAD_ENDPOINT + '?' + urllib.parse.urlencode(
            {'uploadType': 'multipart', 'fields': 'id', 'supportsAllDrives': 'true'})
        method = 'POST'

    data, _ = _request(
        method, url, access_token, data=bytes(body),
        headers={'Content-Type': 'multipart/related; boundary=%s' % boundary},
        timeout=timeout,
    )
    return data['id']


# ---------------------------------------------------------------------
# Resumable upload (requirement 15: large file, chunked, retry-per-chunk).
# ---------------------------------------------------------------------

def start_resumable_session(access_token, name, parent_id, mime_type, total_size,
                             existing_file_id=None, timeout=30):
    """Open a resumable upload session, returning the session URI (the
    ``Location`` header Google gives back). Follows Drive API's
    "Perform a resumable upload" protocol."""
    metadata = {'name': name}
    if parent_id and not existing_file_id:
        metadata['parents'] = [parent_id]

    if existing_file_id:
        url = '%s/%s?%s' % (
            UPLOAD_ENDPOINT, urllib.parse.quote(existing_file_id),
            urllib.parse.urlencode({'uploadType': 'resumable', 'supportsAllDrives': 'true'}),
        )
        method = 'PATCH'
    else:
        url = UPLOAD_ENDPOINT + '?' + urllib.parse.urlencode({'uploadType': 'resumable', 'supportsAllDrives': 'true'})
        method = 'POST'

    headers = {
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Type': mime_type,
        'X-Upload-Content-Length': str(total_size),
    }
    req = urllib.request.Request(
        url, data=json.dumps(metadata).encode('utf-8'), method=method,
        headers=_headers(access_token, headers),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            location = resp.headers.get('Location')
            if not location:
                raise DriveApiError('Drive API did not return a resumable session URI.')
            return location
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        if e.code == 401:
            raise DriveApiError('Google sign-in expired or was revoked (401). Please sign in again.')
        if e.code == 429 or e.code >= 500:
            raise DriveRetryableError('Drive API returned HTTP %d starting upload session: %s' % (e.code, body[:300]))
        raise DriveApiError('Could not start resumable upload session (HTTP %d): %s' % (e.code, body[:400]))
    except urllib.error.URLError as e:
        raise DriveRetryableError('Could not connect to Google Drive API: %s' % e)
    except TimeoutError as e:
        # Lỗi đọc timeout trong lúc chờ Google mở session upload -- KHÔNG
        # bị wrap thành urllib.error.URLError (khác với lỗi kết nối lúc
        # connect()), nên phải bắt riêng ở đây, giống hệt cách _request()
        # đã làm ở trên, nếu không nó sẽ thoát ra ngoài như 1 lỗi thường
        # (không retryable) và huỷ luôn cả file dù _with_retry() có sẵn.
        raise DriveRetryableError('Timed out starting upload session: %s' % e)


def query_resumable_offset(session_uri, total_size, timeout=30):
    """After a dropped connection, ask Google how many bytes it actually
    received so the upload can resume from there instead of restarting."""
    req = urllib.request.Request(
        session_uri, data=b'', method='PUT',
        headers={'Content-Range': 'bytes */%d' % total_size},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return total_size  # 200/201: server says it is already complete
    except urllib.error.HTTPError as e:
        if e.code == 308:
            range_header = e.headers.get('Range')
            if range_header:
                # "bytes=0-12345" -> next byte to send is 12346
                return int(range_header.split('-')[1]) + 1
            return 0
        if e.code in (200, 201):
            return total_size
        raise DriveRetryableError('Could not query upload offset (HTTP %d).' % e.code)
    except urllib.error.URLError as e:
        raise DriveRetryableError('Could not connect to Google Drive API while querying offset: %s' % e)
    except TimeoutError as e:
        raise DriveRetryableError('Timed out querying upload offset: %s' % e)


def upload_chunk(session_uri, chunk_bytes, start, total_size, timeout=120):
    """PUT one chunk at ``[start, start+len(chunk_bytes))`` of a
    resumable session. Returns ``(done, file_id_or_None)``: ``done`` is
    True once Google has the whole file (HTTP 200/201), in which case
    the parsed file id is also returned."""
    end = start + len(chunk_bytes) - 1
    headers = {
        'Content-Length': str(len(chunk_bytes)),
        'Content-Range': 'bytes %d-%d/%d' % (start, end, total_size),
    }
    req = urllib.request.Request(session_uri, data=chunk_bytes, method='PUT', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            file_id = None
            if body:
                try:
                    file_id = json.loads(body.decode('utf-8')).get('id')
                except ValueError:
                    pass
            return True, file_id
    except urllib.error.HTTPError as e:
        if e.code == 308:
            return False, None  # chunk accepted, more to come
        body = e.read().decode('utf-8', errors='ignore')
        if e.code == 401:
            raise DriveApiError('Google sign-in expired or was revoked (401). Please sign in again.')
        if e.code == 429 or e.code >= 500:
            raise DriveRetryableError('Drive API returned HTTP %d uploading chunk: %s' % (e.code, body[:300]))
        raise DriveApiError('Chunk upload failed (HTTP %d): %s' % (e.code, body[:400]))
    except urllib.error.URLError as e:
        raise DriveRetryableError('Connection dropped mid-chunk: %s' % e)
    except TimeoutError as e:
        # Cùng lý do như start_resumable_session() ở trên -- timeout khi
        # đang đọc response của 1 chunk (đây chính là lỗi "The read
        # operation timed out" thấy trong log) không tự động là
        # URLError, phải bắt riêng để _with_retry() thực sự retry được.
        raise DriveRetryableError('Timed out uploading chunk: %s' % e)


# ---------------------------------------------------------------------
# Sharing / public link (requirement 9/10).
# ---------------------------------------------------------------------

def create_public_permission(access_token, file_id, timeout=30):
    url = '%s/%s/permissions?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({'fields': 'id', 'supportsAllDrives': 'true'}),
    )
    body = json.dumps({'role': 'reader', 'type': 'anyone'}).encode('utf-8')
    _request('POST', url, access_token, data=body,
              headers={'Content-Type': 'application/json'}, timeout=timeout)


def list_permissions(access_token, file_id, timeout=30):
    url = '%s/%s/permissions?%s' % (
        FILES_ENDPOINT, urllib.parse.quote(file_id),
        urllib.parse.urlencode({'fields': 'permissions(id,type,role)', 'supportsAllDrives': 'true'}),
    )
    data = _api_get(url, access_token, timeout=timeout)
    return data.get('permissions', [])


def remove_public_permissions(access_token, file_id, timeout=30):
    for perm in list_permissions(access_token, file_id, timeout=timeout):
        if perm.get('type') == 'anyone':
            url = '%s/%s/permissions/%s?%s' % (
                FILES_ENDPOINT, urllib.parse.quote(file_id), urllib.parse.quote(perm['id']),
                urllib.parse.urlencode({'supportsAllDrives': 'true'}),
            )
            _request('DELETE', url, access_token, timeout=timeout, parse_json=False)


def build_links(file_data):
    """Return ``(view_link, download_link)`` for a Drive file dict,
    preferring the links the API returns directly and falling back to
    the well-known URL patterns otherwise (same fallback rule Public
    GDrive Library's ``build_links`` used)."""
    file_id = file_data['id']
    view_link = file_data.get('webViewLink') or ('https://drive.google.com/file/d/%s/view?usp=sharing' % file_id)
    download_link = file_data.get('webContentLink') or ('https://drive.google.com/uc?export=download&id=%s' % file_id)
    return view_link, download_link


def guess_mime_type(file_path, default='application/octet-stream'):
    mime, _ = mimetypes.guess_type(file_path)
    return mime or default
