# -*- coding: utf-8 -*-
"""Small helpers for opening the app help from dialog title-bar '?' buttons."""

from PyQt5.Qt import QEvent


def maybe_show_help_from_event(parent, event):
    if event.type() != QEvent.EnterWhatsThisMode:
        return False
    try:
        from PyQt5.Qt import QWhatsThis
        QWhatsThis.leaveWhatsThisMode()
    except Exception:
        pass
    try:
        from audiobookgdrive.main_window import HelpDialog
        dlg = HelpDialog(parent)
        dlg.exec_()
    except Exception:
        # Help should never break the dialog that requested it.
        return True
    return True
