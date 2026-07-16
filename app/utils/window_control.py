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
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_RESTORE = 9
HWND_TOP = 0
SWP_NOZORDER = 0x0004


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
        if "Chrome" in item.title or "贝壳" in item.title or "链家" in item.title:
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
        current_thread_id = kernel32.GetCurrentThreadId()
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


def enumerate_browser_windows() -> list[WindowInfo]:
    """枚举所有可见的 Edge 顶层窗口（过滤小弹窗）。"""
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetParent(hwnd):
            return True

        title = _get_window_text(hwnd)
        if not title or "Chrome" not in title:
            return True

        # 过滤掉小弹窗（验证码/提示窗口通常 < 400px 宽 或 < 300px 高）
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 400 or h < 300:
            return True

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        windows.append(WindowInfo(hwnd=hwnd, pid=process_id.value, title=title))
        return True

    user32.EnumWindows(enum_proc, 0)
    return windows


def tile_browser_windows(pids: list[int] | None = None, margin: int = 0):
    """将多个浏览器窗口平铺填满屏幕。

    5 个窗口：2 列 × 3 行网格，右下角空出留给终端。
      ┌──────┬──────┐
      │ 窗口0│ 窗口3│  ← 左上、右上
      ├──────┼──────┤
      │ 窗口1│ 窗口4│  ← 左中、右中
      ├──────┼──────┤
      │ 窗口2│ 终端 │  ← 左下、右下(空)
      └──────┴──────┘

    Args:
        pids: 浏览器进程 PID 列表（保留兼容，未使用；改为自动枚举）
        margin: 窗口间边距（像素），默认给任务栏留 60
    """
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    windows = enumerate_browser_windows()
    n = len(windows)
    if n == 0:
        log.warning("未发现 Chrome 窗口")
        return

    if n == 5:
        # 2列×3行，右下角空出给终端
        cols = 2
        rows = 3
        col_w = screen_w // cols
        row_h = (screen_h - margin) // rows
        # 格子顺序：左上→左中→左下→右上→右中（跳过右下）
        positions = [
            (0, 0), (0, 1), (0, 2),  # 左列：上、中、下
            (1, 0), (1, 1),          # 右列：上、中（右下空）
        ]
        for idx, (col, row) in enumerate(positions):
            if idx >= n:
                break
            win = windows[idx]
            x = col * col_w
            y = row * row_h
            user32.SetWindowPos(win.hwnd, HWND_TOP, x, y, col_w, row_h, SWP_NOZORDER)
            log.info("窗口平铺 [%d] %s → (%d,%d %dx%d)", idx, win.title[:40], x, y, col_w, row_h)
        return

    # 其余数量走通用行式布局
    if n <= 3:
        layout = [(0, 0, n)]
        rows = 1
    else:
        rows = min(n, 2 + (n > 4))
        per_row = (n + rows - 1) // rows
        layout = []
        remaining = n
        for r in range(rows):
            cols = min(per_row, remaining)
            layout.append((r, 0, cols))
            remaining -= cols

    row_h = (screen_h - margin) // rows
    idx = 0
    for row_idx, _left_off, cols in layout:
        col_w = screen_w // cols
        for col in range(cols):
            if idx >= n:
                break
            win = windows[idx]
            idx += 1
            x = col * col_w
            y = row_idx * row_h
            user32.SetWindowPos(win.hwnd, HWND_TOP, x, y, col_w, row_h, SWP_NOZORDER)
            log.info("窗口平铺 [%d] %s → (%d,%d %dx%d)", idx, win.title[:40], x, y, col_w, row_h)
