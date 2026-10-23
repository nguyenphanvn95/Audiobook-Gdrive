# -*- coding: utf-8 -*-
"""
audiobook_check_dialog.py
============================

Dialog tiến trình cho "Kiểm tra Audiobooks trên Drive...".
Luôn chạy trong cửa sổ (không chạy dưới nền) để theo dõi log/tiến trình.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPlainTextEdit, QDialogButtonBox,
)

from audiobookgdrive.audiobook_sync.worker import AudiobookCheckThread
from audiobookgdrive.help_utils import maybe_show_help_from_event


class AudiobookCheckProgressDialog(QDialog):
    def __init__(self, gui, access_token, logger=None, library_name=None):
        QDialog.__init__(self, gui)
        lib = library_name or 'Audiobooks'
        self.setWindowTitle('Kiểm tra trên Drive — %s' % lib)
        self.resize(560, 420)
        self._cancel_requested = False

        outer = QVBoxLayout()
        self.setLayout(outer)

        info = QLabel(
            'So sánh nội dung thật trong thư mục thư viện trên Google Drive '
            'với metadata_public.json. Sửa id mồ côi/thiếu, phát hiện cuốn '
            'không có audio, và thêm cuốn đã có trên Drive nhưng chưa có trong json. '
            'Không xóa file/thư mục thật trên Drive.\n\n'
            'Nếu thư viện đã đồng bộ trước đó (app khác), bước này sẽ tự gắn '
            'thư mục + nạp metadata vào bộ nhớ đệm local.'
        )
        info.setWordWrap(True)
        outer.addWidget(info)

        self.status_label = QLabel('Đang chuẩn bị...')
        outer.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(1)
        outer.addWidget(self.progress_bar)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        outer.addWidget(self.log_view)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self._close_or_cancel)
        self.buttons.accepted.connect(self._close_or_cancel)
        self.buttons.button(QDialogButtonBox.Close).setEnabled(True)
        self.buttons.button(QDialogButtonBox.Close).setText('Hủy')
        outer.addWidget(self.buttons)

        self.thread = AudiobookCheckThread(
            access_token, parent=self, logger=logger, library_name=library_name)
        self.thread.progress.connect(self._on_progress)
        self.thread.scan_progress.connect(self._on_scan_progress)
        self.thread.log.connect(self._on_log)
        self.thread.finished_ok.connect(self._on_finished)
        self.thread.failed.connect(self._on_failed)

    def _on_scan_progress(self, done, total, current_folder_name):
        if self._cancel_requested:
            return
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(done)
        self.status_label.setText(
            'Đang quét thư mục trên Drive... (%d/%d, đang xem: %s)' % (done, total, current_folder_name))

    def _on_progress(self, done, total):
        if self._cancel_requested:
            return
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(done)
        self.status_label.setText('Đang kiểm tra... (%d/%d cuốn)' % (done, total))

    def _on_log(self, message):
        self.log_view.appendPlainText(message)

    def _on_finished(self, stats):
        no_audio = stats.get('no_audio_books') or []
        added = stats.get('books_added', 0)
        imported = stats.get('imported_from_existing', 0)
        created = stats.get('metadata_created', False)
        if not stats.get('changed'):
            if created:
                base = 'Hoàn tất. Chưa tạo metadata_public.json vì không tìm thấy thư mục audiobook hợp lệ.'
            else:
                base = 'Hoàn tất. Đã kiểm tra %d cuốn -- không có sai lệch nào.' % stats.get('books_checked', 0)
        else:
            base = ('Hoàn tất. Đã kiểm tra %d cuốn -- %d cuốn bị xóa khỏi json, '
                     '%d file đã sửa id, %d cuốn mới thêm từ Drive.') % (
                stats.get('books_checked', 0), stats.get('books_removed', 0),
                stats.get('files_fixed', 0), added)
            if created:
                base += ' Đã tạo mới metadata_public.json.'
        if imported:
            base += ' (đã nạp %d cuốn từ metadata có sẵn vào bộ nhớ đệm).' % imported
        if no_audio:
            base += ' %d cuốn KHÔNG CÓ AUDIO (xem log).' % len(no_audio)
        self.status_label.setText(base)
        self.buttons.button(QDialogButtonBox.Close).setEnabled(True)
        self.buttons.button(QDialogButtonBox.Close).setText('Đóng')

    def _on_failed(self, message):
        if self._cancel_requested or message == 'Đã huỷ.':
            self.status_label.setText('Đã hủy kiểm tra.')
            self.log_view.appendPlainText('Đã hủy theo yêu cầu.')
            self.buttons.button(QDialogButtonBox.Close).setEnabled(True)
            self.buttons.button(QDialogButtonBox.Close).setText('Đóng')
            self.accept()
            return
        self.status_label.setText('Thất bại: %s' % message)
        self.log_view.appendPlainText('LỖI: %s' % message)
        self.buttons.button(QDialogButtonBox.Close).setEnabled(True)
        self.buttons.button(QDialogButtonBox.Close).setText('Đóng')

    def _request_cancel(self):
        if self._cancel_requested:
            return
        self._cancel_requested = True
        if self.thread.isRunning():
            self.thread.request_abort()
            self.status_label.setText('Đang hủy kiểm tra...')
            self.log_view.appendPlainText('Đang hủy theo yêu cầu...')
            self.buttons.button(QDialogButtonBox.Close).setEnabled(False)

    def _close_or_cancel(self):
        if self.thread.isRunning():
            self._request_cancel()
            return
        self.accept()

    def reject(self):
        self._close_or_cancel()

    def closeEvent(self, event):
        if self.thread.isRunning():
            self._request_cancel()
            event.ignore()
            return
        QDialog.closeEvent(self, event)

    def event(self, event):
        if maybe_show_help_from_event(self, event):
            return True
        return QDialog.event(self, event)

    def exec_(self, run_in_background=False):
        self.thread.start()
        return QDialog.exec_(self)
