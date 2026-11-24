# SPDX-License-Identifier: MIT
"""Windows Explorer Unicode drag-and-drop for the Tk main window."""
from __future__ import annotations

import os


def register(window, accept):
    if os.name != "nt":
        return lambda: None
    import ctypes as c
    from ctypes import wintypes as w

    user, shell = c.WinDLL("user32", use_last_error=True), c.WinDLL("shell32", use_last_error=True)
    get_root = user.GetAncestor
    get_root.argtypes, get_root.restype = (w.HWND, w.UINT), w.HWND
    hwnd = get_root(window.winfo_id(), 2)  # GA_ROOT
    set_proc = user.SetWindowLongPtrW if c.sizeof(c.c_void_p) == 8 else user.SetWindowLongW
    set_proc.argtypes, set_proc.restype = (w.HWND, c.c_int, c.c_void_p), c.c_void_p
    call_proc = user.CallWindowProcW
    notify = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)
    call_proc.argtypes = (c.c_void_p, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)
    call_proc.restype = c.c_ssize_t
    drag = shell.DragAcceptFiles
    drag.argtypes, drag.restype = (w.HWND, w.BOOL), None
    query = shell.DragQueryFileW
    query.argtypes, query.restype = (w.HANDLE, w.UINT, w.LPWSTR, w.UINT), w.UINT
    finish = shell.DragFinish
    finish.argtypes, finish.restype = (w.HANDLE,), None

    @notify
    def on_message(handle, msg, wparam, lparam):
        if msg != 0x233:  # WM_DROPFILES
            return call_proc(previous, handle, msg, wparam, lparam)
        paths = []
        try:
            for i in range(query(wparam, 0xFFFFFFFF, None, 0)):
                length = query(wparam, i, None, 0)
                buf = c.create_unicode_buffer(length + 1)
                if query(wparam, i, buf, len(buf)):
                    paths.append(buf.value)
            if paths:
                window.after_idle(accept, paths)
        except Exception:
            pass
        finally:
            finish(wparam)
        return 0

    previous = set_proc(hwnd, -4, c.cast(on_message, c.c_void_p))  # GWLP_WNDPROC
    if not previous:
        raise c.WinError(c.get_last_error())
    drag(hwnd, True)

    def unregister():
        # Keep callback alive until the previous procedure is restored.
        if on_message:
            drag(hwnd, False)
            set_proc(hwnd, -4, previous)

    return unregister
