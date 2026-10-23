# -*- coding: utf-8 -*-
"""
audiobook_sync/opf_parser.py
=============================

Đọc file ``metadata.opf`` THẬT nằm sẵn trong từng thư mục audiobook (khác
hẳn với ``metadata.py`` ở thư mục gốc plugin, dùng để TỰ SINH opf từ đối
tượng ``Metadata`` của Calibre cho "Calibre Library" sync). Ở đây file opf
đã tồn tại sẵn trên đĩa và có thể chứa các thẻ ``<meta property="...">``
tuỳ biến theo nguồn phát hành audiobook (ví dụ ``voiz:chapter``,
``voiz:url``, ``voiz:cover``) mà bộ đọc OPF của Calibre
(``calibre.ebooks.metadata.opf2``) không giữ lại nguyên vẹn -- nên tự
parse bằng ``xml.etree.ElementTree`` để không mất trường nào, rồi gộp vào
``metadata_public.json`` của Audiobook Sync (xem ``uploader.py``).

Best-effort: một file opf hỏng/khác cấu trúc mong đợi chỉ trả về một dict
gần như rỗng thay vì raise, để 1 cuốn lỗi không làm hỏng cả lượt đồng bộ.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import xml.etree.ElementTree as ET

NS = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
}


def _text(el):
    return (el.text or '').strip() if el is not None else ''


def parse_opf_file(opf_path):
    """Trả về dict phẳng, dễ đưa thẳng vào JSON:

    ``title``, ``creators`` (list ``{"name", "role"}``), ``publisher``,
    ``language``, ``description``, ``identifiers`` (list
    ``{"id", "scheme", "value"}``), ``modified`` (từ
    ``meta property="dcterms:modified"``), ``chapters`` (list
    ``{"id", "title"}``, giữ đúng thứ tự xuất hiện, từ
    ``meta property="voiz:chapter"``), ``extra_meta`` (dict, mọi
    ``<meta property="...">`` khác không thuộc 2 loại trên -- ví dụ
    ``voiz:url``/``voiz:cover`` -- giá trị là chuỗi nếu chỉ xuất hiện 1
    lần, hoặc list nếu property đó lặp lại nhiều lần).
    """
    result = {
        'title': None, 'creators': [], 'publisher': None, 'language': None,
        'description': None, 'identifiers': [], 'modified': None,
        'chapters': [], 'extra_meta': {},
    }
    try:
        tree = ET.parse(opf_path)
    except Exception:
        return result

    root = tree.getroot()
    metadata_el = root.find('opf:metadata', NS)
    if metadata_el is None:
        metadata_el = root.find('metadata')  # opf files without an explicit prefix
    if metadata_el is None:
        return result

    title_el = metadata_el.find('dc:title', NS)
    result['title'] = _text(title_el) or None

    for creator in metadata_el.findall('dc:creator', NS):
        role = creator.attrib.get('{%s}role' % NS['opf']) or creator.attrib.get('role')
        name = _text(creator)
        if name:
            result['creators'].append({'name': name, 'role': role or None})

    result['publisher'] = _text(metadata_el.find('dc:publisher', NS)) or None
    result['language'] = _text(metadata_el.find('dc:language', NS)) or None
    result['description'] = _text(metadata_el.find('dc:description', NS)) or None

    for ident in metadata_el.findall('dc:identifier', NS):
        scheme = ident.attrib.get('{%s}scheme' % NS['opf']) or ident.attrib.get('scheme')
        ident_id = ident.attrib.get('id')
        value = _text(ident)
        if value:
            result['identifiers'].append({'id': ident_id or None, 'scheme': scheme or None, 'value': value})

    meta_elements = metadata_el.findall('opf:meta', NS)
    if not meta_elements:
        meta_elements = metadata_el.findall('meta')  # namespace-less fallback
    for meta in meta_elements:
        prop = meta.attrib.get('property')
        if not prop:
            continue
        text = _text(meta)
        if prop == 'dcterms:modified':
            result['modified'] = text or None
        elif prop == 'voiz:chapter':
            result['chapters'].append({'id': meta.attrib.get('id'), 'title': text})
        else:
            bucket = result['extra_meta'].setdefault(prop, [])
            if text:
                bucket.append(text)

    # Gọn lại: property chỉ xuất hiện 1 lần thì lưu là chuỗi thay vì list 1 phần tử.
    for key, values in list(result['extra_meta'].items()):
        if len(values) == 1:
            result['extra_meta'][key] = values[0]
        elif not values:
            del result['extra_meta'][key]

    return result
