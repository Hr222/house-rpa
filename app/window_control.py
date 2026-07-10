# -*- coding: utf-8 -*-
"""Windows 浏览器窗口控制。"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

SW_RESTORE = 9


@dataclass(slots=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str


def _get_window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def find_browser_window(pid: int) -> Optional[WindowInfo]:
    """按浏览器进程 PID 找可见顶层窗口。"""
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value != pid:
            return True

        title = _get_window_text(hwnd)
        if not title:
            return True

        windows.append(WindowInfo(hwnd=hwnd, pid=pid, title=title))
        return True

    user32.EnumWindows(enum_proc, 0)
    if not windows:
        return None

    for item in windows:
        if "Edge" in item.title or "贝壳" in item.title or "链家" in item.title:
            return item
    return windows[0]


def focus_window(hwnd: int) -> bool:
    """恢复并置前窗口。"""
    if not hwnd:
        return False

    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_RESTORE)

        foreground_hwnd = user32.GetForegroundWindow()
        current_thread_id = user32.GetCurrentThreadId()
        foreground_thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None)
        target_thread_id = user32.GetWindowThreadProcessId(hwnd, None)

        if foreground_thread_id and foreground_thread_id != current_thread_id:
            user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
        if target_thread_id and target_thread_id != current_thread_id:
            user32.AttachThreadInput(target_thread_id, current_thread_id, True)

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)

        if foreground_thread_id and foreground_thread_id != current_thread_id:
            user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)
        if target_thread_id and target_thread_id != current_thread_id:
            user32.AttachThreadInput(target_thread_id, current_thread_id, False)
        return True
    except Exception as exc:
        log.warning("窗口置前失败: %s", exc)
        return False


def ensure_browser_foreground(pid: int) -> bool:
    """按浏览器 PID 查找窗口并置前。"""
    window = find_browser_window(pid)
    if window is None:
        log.warning("未找到浏览器窗口, pid=%s", pid)
        return False
    ok = focus_window(window.hwnd)
    if ok:
        log.info("浏览器窗口已置前: pid=%s title=%s", pid, window.title)
    return ok
