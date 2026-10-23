# -*- coding: utf-8 -*-
"""
pairing_dialog.py
==================

Hộp thoại "Ghép nối với Calibre Sync (Android)" (Calibre Gdrive Sync
upgrade): hiển thị link chia sẻ trực tiếp của metadata_public.json --
đúng thứ mà AddGDriveOpdsLibraryDialogFragment bên app Android
(GDrivePublicFileClient.extractFileId) chấp nhận dán vào ("Add GDrive
OPDS Library") -- kèm nút Copy, mã QR để quét bằng điện thoại, và
hướng dẫn ngắn gọn.

Mã QR được vẽ hoàn toàn cục bộ bằng module thuần Python vendor sẵn
``qrcodegen`` (không gọi dịch vụ QR nào qua mạng -- tránh gửi link thư
viện riêng tư cho bên thứ ba, và vẫn hoạt động được khi máy không có
mạng ngay lúc mở hộp thoại này).
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import (QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
                       QHBoxLayout, QApplication, Qt, QImage, QPixmap,
                       QColor, QPainter)


def _render_qr_pixmap(text, box_size=6, border=4):
    """Render `text` as a QR code and return it as a QPixmap, using only
    the vendored ``qrcodegen`` module (no PIL, no network fetch)."""
    from audiobookgdrive import qrcodegen

    qr = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    n = qr.get_size()
    dim = (n + border * 2) * box_size

    image = QImage(dim, dim, QImage.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    try:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('black'))
        for y in range(n):
            for x in range(n):
                if qr.get_module(x, y):
                    painter.drawRect((x + border) * box_size, (y + border) * box_size,
                                      box_size, box_size)
    finally:
        painter.end()
    return QPixmap.fromImage(image)


class PairingDialog(QDialog):
    """metadata_json_file_id: File ID (không phải folder ID) của
    metadata_public.json vừa upload/refresh -- lấy từ
    state.get_root_metadata_json_id()."""

    def __init__(self, metadata_json_file_id, book_count, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle('Ghép nối với Calibre Sync (Android)')
        self.setMinimumWidth(520)

        link = ('https://drive.google.com/file/d/%s/view?usp=sharing'
                % metadata_json_file_id)

        layout = QVBoxLayout(self)

        intro = QLabel(
            'Thư viện đã đồng bộ lên Google Drive và được chia sẻ ở chế độ '
            '"Anyone with the link". Trên điện thoại, mở app <b>Calibre '
            'Sync</b> → menu thư viện thiết bị → <b>"Add GDrive OPDS '
            'Library"</b>, dán link/ID bên dưới vào đó (hoặc quét mã QR) '
            'để lấy %d cuốn sách vừa đồng bộ (và mọi lần đồng bộ sau này '
            'sẽ tự cập nhật khi bấm "Làm mới"/"Sync" trong app).' % book_count)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.link_field = QLineEdit(link)
        self.link_field.setReadOnly(True)
        self.link_field.setCursorPosition(0)
        row.addWidget(self.link_field)
        copy_btn = QPushButton('Copy link')
        copy_btn.clicked.connect(self._copy_link)
        row.addWidget(copy_btn)
        layout.addLayout(row)

        id_row = QHBoxLayout()
        id_row.addWidget(QLabel('Hoặc chỉ ID file:'))
        id_field = QLineEdit(metadata_json_file_id)
        id_field.setReadOnly(True)
        id_row.addWidget(id_field)
        copy_id_btn = QPushButton('Copy ID')
        copy_id_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(metadata_json_file_id))
        id_row.addWidget(copy_id_btn)
        layout.addLayout(id_row)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        try:
            qr_label.setPixmap(_render_qr_pixmap(link))
        except Exception as e:
            qr_label.setText('Không tạo được mã QR: %s' % e)
        layout.addWidget(qr_label)

        qr_hint = QLabel('Mở Camera hoặc app quét QR trên điện thoại và quét mã trên '
                          'để mở thẳng link này, khỏi phải gõ hay copy tay.')
        qr_hint.setWordWrap(True)
        qr_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(qr_hint)

        close_btn = QPushButton('Đóng')
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _copy_link(self):
        QApplication.clipboard().setText(self.link_field.text())

