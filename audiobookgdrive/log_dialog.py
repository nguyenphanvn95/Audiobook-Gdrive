# -*- coding: utf-8 -*-
"""
log_dialog.py
=============

Cửa sổ "Xem Log" -- hiện toàn bộ file log trên đĩa
(``<config_dir>/audiobookgdrive.log``) cộng với các dòng log gần nhất
trong bộ nhớ (ring buffer) của :class:`~audiobookgdrive.logger.Logger`
đang chạy (nếu có), cùng tinh thần ``log_dialog.py`` gốc.
"""

__copyright__ = '2026, AudiobookGdrive Authors'
__license__ = 'GPL v3'

from os import path

from PyQt5.Qt import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox, QPushButton, QHBoxLayout

from audiobookgdrive.logger import LOG_PATH


class LogDialog(QDialog):
    def __init__(self, parent=None, active_logger=None):
        QDialog.__init__(self, parent)
        self.active_logger = active_logger
        self.setWindowTitle('AudiobookGdrive - Log')
        self.resize(720, 480)

        self.l = QVBoxLayout()
        self.setLayout(self.l)

        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        try:
            no_wrap = QPlainTextEdit.LineWrapMode.NoWrap
        except AttributeError:
            no_wrap = QPlainTextEdit.NoWrap
        self.text.setLineWrapMode(no_wrap)
        self.l.addWidget(self.text)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton('Làm mới')
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        btn_row.addWidget(buttons)
        self.l.addLayout(btn_row)

        self.refresh()

    def refresh(self):
        lines = []
        if self.active_logger:
            lines.extend(self.active_logger.recent())
        elif path.exists(LOG_PATH):
            try:
                with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
                    lines.extend(f.readlines()[-2000:])
            except OSError:
                pass
        self.text.setPlainText(''.join(l if l.endswith('\n') else l + '\n' for l in lines))
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
