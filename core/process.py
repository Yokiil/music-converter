# SPDX-License-Identifier: MIT
"""工具子进程统一处理进度、超时和停止；不经 shell 执行。"""
from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# 不让输入伪装成播放列表、拼接脚本或 URL。输出格式不受此输入列表限制。
MEDIA_DEMUXERS = ("aac,ac3,aiff,amr,ape,asf,avi,dsf,iff,flac,flv,matroska,webm,"
                  "mov,mp4,m4a,3gp,3g2,mj2,mp3,ogg,wav,wv,tta,tak,mpc,mpc8,dts")
MEDIA_INPUT = ("-protocol_whitelist", "file", "-format_whitelist", MEDIA_DEMUXERS)


@dataclass(frozen=True)
class ProcessResult:
    code: int
    error: str = ""
    cancelled: bool = False
    timed_out: bool = False
    output_too_large: bool = False


def run_ffmpeg(cmd: list[str], work: Path, *, timeout: float, cancel: Event | None = None,
               on_progress: Callable[[float], None] | None = None, duration: float = 0,
               output_limit: tuple[Path, int] | None = None) -> ProcessResult:
    def exceeds_limit() -> bool:
        if output_limit is None:
            return False
        path, limit = output_limit
        try:
            return path.stat().st_size > limit
        except FileNotFoundError:
            return False
    if cancel is not None and cancel.is_set():
        return ProcessResult(-1, cancelled=True)
    progress_path = work / "ffmpeg-progress.txt"
    with progress_path.open("wb") as progress_out, progress_path.open("rb") as progress_in, tempfile.TemporaryFile(dir=work) as errors:
        with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=progress_out,
                              stderr=errors, creationflags=NO_WINDOW) as process:
            try:
                end = time.monotonic() + timeout
                while True:
                    if cancel is not None and cancel.is_set():
                        return ProcessResult(-1, cancelled=True)
                    if time.monotonic() >= end:
                        return ProcessResult(-1, timed_out=True)
                    if exceeds_limit():
                        return ProcessResult(-1, output_too_large=True)
                    if on_progress is not None and duration > 0:
                        while True:
                            start = progress_in.tell()
                            line = progress_in.readline()
                            if not line.endswith(b"\n"):
                                progress_in.seek(start)
                                break
                            if line.startswith(b"out_time_us="):
                                try:
                                    microseconds = int(line.split(b"=", 1)[1])
                                except ValueError:
                                    continue
                                on_progress(max(0, min(microseconds / 1_000_000 / duration, 0.99)))
                    try:
                        code = process.wait(timeout=0.12)
                    except subprocess.TimeoutExpired:
                        continue
                    if exceeds_limit():
                        return ProcessResult(-1, output_too_large=True)
                    errors.seek(max(0, errors.seek(0, 2) - 4096))
                    detail = errors.read().decode("utf-8", "replace").strip()
                    return ProcessResult(code, detail)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
