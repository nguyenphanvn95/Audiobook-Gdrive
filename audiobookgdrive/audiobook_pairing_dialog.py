# -*- coding: utf-8 -*-
"""
audiobook_pairing_dialog.py
=============================

Hộp thoại **"Thêm thư viện sách nói vào Voice..."** (Audiobook Sync
upgrade) -- cùng nguyên lý với ``pairing_dialog.PairingDialog`` ("Ghép
nối với Calibre Sync (Android)...") nhưng trỏ tới
``metadata_public.json`` của **Audiobook Sync** (``audiobook_sync/``,
khác hẳn ``metadata_public.json``/``AudiobookState`` của "Calibre
Library") để ghép nối với app nghe sách nói **Voice** trên Android:
hiển thị link chia sẻ trực tiếp của file, kèm nút Copy, mã QR để quét
bằng điện thoại, và hướng dẫn ngắn gọn.

Mã QR dùng lại ``pairing_dialog._render_qr_pixmap`` (vẽ hoàn toàn cục
bộ bằng module thuần Python vendor sẵn ``qrcodegen``, không gọi dịch vụ
QR nào qua mạng) -- không viết lại logic vẽ QR ở đây.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import (QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
                       QHBoxLayout, QApplication, Qt)

from .pairing_dialog import _render_qr_pixmap


class AudiobookPairingDialog(QDialog):
    """metadata_json_file_id: File ID (không phải folder ID) của
    ``metadata_public.json`` thuộc Audiobook Sync -- lấy từ
    ``audiobook_sync.state_store.AudiobookState.get_metadata_json_id()``.

    ``share_warning``: thông báo ngắn (hoặc ``None``) nếu bước tự động
    chia sẻ "Anyone with the link" cho **file** ``metadata_public.json``
    (làm ngay trước khi mở dialog này) bị lỗi -- hiển thị cảnh báo cho
    người dùng thay vì âm thầm đưa ra 1 link có thể chưa dùng được,
    nhưng KHÔNG chặn việc hiển thị link/QR (người dùng vẫn có thể tự
    bấm "Share" thủ công trên drive.google.com nếu cần)."""

    def __init__(self, metadata_json_file_id, book_count, share_warning=None, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle('Thêm thư viện sách nói vào Voice')
        self.setMinimumWidth(520)

        link = ('https://drive.google.com/file/d/%s/view?usp=sharing'
                % metadata_json_file_id)

        layout = QVBoxLayout(self)

        if share_warning:
            warn = QLabel('⚠ %s' % share_warning)
            warn.setWordWrap(True)
            warn.setStyleSheet('color: #b00;')
            layout.addWidget(warn)

        intro = QLabel(
            'Đã tìm thấy <b>metadata_public.json</b> và chia sẻ công khai '
            'riêng file này (không share cả thư mục thư viện). Trên điện thoại, '
            'mở app <b>Voice</b> → menu thư viện → <b>"Thêm thư viện sách nói"</b> '
            '(quét mã QR), hoặc dán link/ID bên dưới vào đó, để lấy %d '
            'cuốn sách nói (và mọi lần đồng bộ sau sẽ tự cập nhật khi app '
            'làm mới lại thư viện đã ghép nối).' % book_count)
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

        qr_hint = QLabel('Mở app Voice trên điện thoại, chọn quét mã QR ở màn hình thêm thư '
                          'viện, rồi quét mã trên để nhập thư viện, khỏi phải gõ hay copy tay.')
        qr_hint.setWordWrap(True)
        qr_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(qr_hint)

        close_btn = QPushButton('Đóng')
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _copy_link(self):
        QApplication.clipboard().setText(self.link_field.text())
