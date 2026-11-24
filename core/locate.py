# SPDX-License-Identifier: MIT
"""定位 ffmpeg / ffprobe 可执行文件。

查找顺序：
1. 项目自带的 bin 目录（推荐，随包分发，不污染系统）
2. 系统 PATH
3. imageio_ffmpeg 附带的 ffmpeg（仅 ffmpeg，没有 ffprobe）
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "bin"


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def find_tool(name: str) -> str | None:
    """按优先级返回工具绝对路径，找不到返回 None。"""
    local = BIN_DIR / _exe(name)
    if local.is_file():
        return str(local)

    on_path = shutil.which(name)
    if on_path:
        return on_path

    if name == "ffmpeg":
        try:  # 极简兜底：某些环境只装了 pip 包
            import imageio_ffmpeg  # type: ignore

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass

    return None


def find_ffmpeg() -> str | None:
    return find_tool("ffmpeg")


def find_ffprobe() -> str | None:
    return find_tool("ffprobe")


def probe_available() -> bool:
    return find_ffprobe() is not None


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def missing_message() -> str:
    return (
        "没有找到 ffmpeg / ffprobe。\n\n"
        f"请把 ffmpeg.exe 和 ffprobe.exe 放进：\n{BIN_DIR}\n\n"
        "获取方式：https://www.gyan.dev/ffmpeg/builds/ 下载 essentials 压缩包，"
        "解压后从 bin 目录里把这两个文件拷过来。"
    )
