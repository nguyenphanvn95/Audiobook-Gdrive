# -*- coding: utf-8 -*-
"""
login_dialog.py
================

Small modal progress dialog + QThread worker that drives
``oauth.interactive_login`` (which is blocking) off the GUI thread, then
saves the resulting tokens. Pattern cloned from Public GDrive Library's
``OAuthLoginWorker`` (a QThread subclass with ``finished``/``failed``
signals) in its ``core.py``.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

from PyQt5.Qt import QThread, pyqtSignal, QDialog, QVBoxLayout, QLabel, QDialogButtonBox

from audiobookgdrive import oauth


class LoginWorker(QThread):
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, client_id, client_secret, parent=None, scopes=None):
        QThread.__init__(self, parent)
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes

    def run(self):
        try:
            tokens = oauth.interactive_login(
                self.client_id, self.client_secret,
                log=lambda msg: self.status.emit(msg),
                scopes=self.scopes,
            )
            self.succeeded.emit(tokens)
        except Exception as e:
            self.failed.emit(str(e))


class LoginDialog(QDialog):
    def __init__(self, parent, client_id, client_secret, scopes=None, flow='library'):
        QDialog.__init__(self, parent)
        self.flow = flow
        self.setWindowTitle('Sign in with Google')
        self.l = QVBoxLayout()
        self.setLayout(self.l)
        self.label = QLabel('Opening your browser to sign in to Google...')
        self.label.setWordWrap(True)
        self.l.addWidget(self.label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._cancel)
        self.l.addWidget(self.buttons)

        self.worker = LoginWorker(client_id, client_secret, self, scopes=scopes)
        self.worker.status.connect(self.label.setText)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)

    def _cancel(self):
        self.worker.terminate()
        self.reject()

    def _succeeded(self, tokens):
        oauth.save_tokens(tokens, flow=self.flow)
        self.accept()

    def _failed(self, msg):
        self.label.setText('Sign-in failed: %s' % msg)
        self.buttons.setStandardButtons(QDialogButtonBox.Close)

    def exec_(self):
        self.worker.start()
        return QDialog.exec_(self)


def run_login_flow(parent_widget, client_id, client_secret, scopes=None, flow='library'):
    """``scopes``/``flow`` default to the existing "Calibre Library"
    drive.file flow; pass ``oauth.DEVICE_SYNC_SCOPES``/``'device_sync'``
    for the Device Library Sync sign-in instead (see ``oauth.py``)."""
    dialog = LoginDialog(parent_widget, client_id, client_secret, scopes=scopes, flow=flow)
    dialog.exec_()
