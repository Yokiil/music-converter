# SPDX-License-Identifier: MIT
"""输出原编码或转码；成功前绝不发布最终文件。"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from . import platforms, probe
from .locate import find_ffmpeg
from .process import MEDIA_INPUT, ProcessResult, run_ffmpeg


@dataclass(frozen=True)
class Target:
    label: str
    ext: str
    encoder: str
    codec: str
    muxer: str
    cover: bool = False
    bitrates: tuple[str, ...] = ()


TARGETS: dict[str, Target] = {
    "source": Target("原始音频（不转码）", "", "", "", ""),
    "mp3": Target("MP3", ".mp3", "libmp3lame", "mp3", "mp3", True, ("320k", "256k", "192k", "128k")),
    "flac": Target("FLAC", ".flac", "flac", "flac", "flac", True),
    "m4a": Target("M4A / AAC", ".m4a", "aac", "aac", "ipod", True, ("256k", "192k", "128k")),
    "wav": Target("WAV / PCM 16 位", ".wav", "pcm_s16le", "pcm_s16le", "wav"),
    "ogg": Target("Ogg / Vorbis", ".ogg", "libvorbis", "vorbis", "ogg", False, ("192k", "128k", "96k")),
}
_COPY_TARGETS = {
    "mp3": TARGETS["mp3"], "flac": TARGETS["flac"], "aac": TARGETS["m4a"],
    "alac": Target("ALAC", ".m4a", "", "alac", "ipod", True),
    "vorbis": TARGETS["ogg"], "opus": Target("Opus", ".opus", "", "opus", "ogg"),
    **{codec: Target("WAV", ".wav", "", codec, "wav") for codec in (
        "pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le")},
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *(f"{p}{i}" for p in ("COM", "LPT") for i in "123456789¹²³")}


def safe_name(name: str) -> str:
    cleaned = _BAD_CHARS.sub("_", name).strip(" .")
    cleaned = cleaned.encode("utf-8", "replace")[:180].decode("utf-8", "ignore").rstrip(" .")
    if cleaned.partition(".")[0].rstrip(" ").upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned or "未命名"


@dataclass(frozen=True)
class ConvertResult:
    ok: bool
    src: Path
    dst: Path | None = None
    message: str = ""
    copied: bool = False
    cancelled: bool = False
    error_code: str = ""


Progress = Callable[[float], None]


def _publish(temporary: Path, out_dir: Path, stem: str, ext: str) -> Path:
    """原子发布且不覆盖；查重和发布由同一个文件系统操作完成。"""
    for index in range(10000):
        name = f"{stem} ({index})" if index else stem
        candidate = out_dir / (name + ext)
        try:
            if os.name == "nt":
                temporary.rename(candidate)  # Windows rename 不覆盖现有文件
            else:
                os.link(temporary, candidate)  # POSIX rename 会覆盖，故使用同盘硬链接
            return candidate
        except FileExistsError:
            continue
        except PermissionError:
            # Windows 的既有同名目录/符号链接有时返回拒绝访问。
            if os.path.lexists(candidate):
                continue
            raise
    raise OSError("同名输出太多，请换一个输出目录。")


def _problem(result: ProcessResult) -> str:
    if result.cancelled:
        return "已停止。"
    if result.timed_out:
        return "FFmpeg 处理超时。"
    return (result.error.splitlines()[-1] if result.error else f"FFmpeg 退出码 {result.code}")[:380]


def needs_lossy_warning(info: probe.AudioInfo, fmt: str, bitrate: str | None = None) -> bool:
    target = TARGETS[fmt]
    if info.wrapped:
        return bool(target.bitrates or fmt == "wav")
    if fmt == "wav":
        return info.lossless and info.bit_depth > 16
    return bool(target.bitrates and (bitrate is not None or info.codec != target.codec))


def convert_to_directory(info: probe.AudioInfo, out_dir: Path, fmt: str,
                         bitrate: str | None = None, on_progress: Progress | None = None,
                         *, profile: platforms.Profile = platforms.EMPTY_PROFILE,
                         cancel: Event | None = None, max_output_bytes: int | None = None,
                         verify_copy: bool = False) -> ConvertResult:
    source = info.path.absolute()

    def failed(message, *, cancelled=False, code="conversion_failed") -> ConvertResult:
        return ConvertResult(False, source, message=message, cancelled=cancelled, error_code=code)

    if cancel is not None and cancel.is_set():
        return failed("已停止。", cancelled=True)
    if fmt not in TARGETS or (bitrate is not None and bitrate not in TARGETS[fmt].bitrates):
        return failed("目标格式或码率无效。")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return failed("找不到 ffmpeg。")
    out_dir = Path(out_dir).absolute()
    try:
        info = probe.diagnose(source, profile)
        if not probe.is_convertible(info):
            return failed(info.note)
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".music-", dir=out_dir) as tmp:
            work = Path(tmp)
            media = info
            extracted = None
            progress_from = 0.0
            if info.wrapped:
                extracted = platforms.extract(source, work, profile, cancel=cancel,
                                              on_progress=(lambda ratio: on_progress(ratio * 0.4)) if on_progress else None)
                media = probe.diagnose(extracted.path, detect_wrapped=False)
                if media.verdict != probe.VERDICT_OK or media.probe_score < 25:
                    return failed("没能验证导出的音频；参数可能不正确，或文件已损坏。")
                progress_from = 0.4

            if fmt == "source":
                target = _COPY_TARGETS.get(media.codec)
                if target is None:
                    return failed(f"暂不支持原样输出 {media.codec_text}，请选择 MP3、FLAC 等目标格式。")
                copied = True
            else:
                target = TARGETS[fmt]
                copied = media.codec == target.codec and bitrate is None

            common = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-nostats"]
            source_args = [*MEDIA_INPUT, "-err_detect", "explode", "-i", str(media.path)]
            # 防止仅有看似有效文件头的错误输出通过 stream copy。
            if copied and (extracted is not None or verify_copy):
                check = run_ffmpeg(common + source_args + ["-map", f"0:{media.audio_index}", "-f", "null", "-"],
                                   work, cancel=cancel, timeout=600)
                if check.code:
                    text = _problem(check) if check.cancelled or check.timed_out else "导出的音频未通过完整解码校验。"
                    return failed(text, cancelled=check.cancelled)

            temp_output = work / ("result" + target.ext)
            cmd = common + ["-progress", "pipe:1", "-n"] + source_args
            cover_map = None
            if target.cover:
                if extracted is not None and extracted.cover is not None:
                    cmd += ["-protocol_whitelist", "file", "-i", str(extracted.cover)]
                    cover_map = "1:v:0"
                elif media.cover_index >= 0 and media.cover_codec in {"mjpeg", "png"}:
                    cover_map = f"0:{media.cover_index}"
            cmd += ["-map", f"0:{media.audio_index}", "-map_metadata", "0", "-map_chapters", "-1"]
            if cover_map:
                cmd += ["-map", cover_map, "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            if extracted is not None:
                for name, value in extracted.tags.items():
                    cmd += ["-metadata", f"{name}={value}"]
            cmd += ["-c:a", "copy" if copied else target.encoder]
            if not copied and target.bitrates:
                cmd += ["-b:a", bitrate or target.bitrates[0]]
                if target.codec == "mp3" and media.channels > 2:
                    cmd += ["-ac", "2"]
            if target.codec == "mp3":
                cmd += ["-id3v2_version", "3"]
            output_limit = (temp_output, max_output_bytes) if max_output_bytes is not None else None
            if output_limit is not None:
                # 超出对外限额即丢弃。额外设置 FFmpeg 硬上限，限制轮询之间的磁盘占用。
                cmd += ["-fs", str(max_output_bytes + 65536)]
            cmd += ["-f", target.muxer, str(temp_output)]
            progress = (lambda fraction: on_progress(progress_from + (1 - progress_from) * fraction)) if on_progress else None
            result = run_ffmpeg(cmd, work, cancel=cancel, timeout=1800, duration=media.duration,
                                on_progress=progress, output_limit=output_limit)
            if result.output_too_large:
                return failed("输出超过允许的大小。", code="output_too_large")
            if result.code != 0:
                return failed(_problem(result), cancelled=result.cancelled)
            verified = probe.diagnose(temp_output, detect_wrapped=False)
            if verified.verdict != probe.VERDICT_OK or verified.codec != target.codec:
                return failed("输出没有通过音轨和编码校验。")
            if cancel is not None and cancel.is_set():
                return failed("已停止。", cancelled=True)
            dst = _publish(temp_output, out_dir, safe_name(source.stem), target.ext)
    except platforms.AdapterError as exc:
        return failed(str(exc), cancelled=exc.code == "cancelled")
    except OSError as exc:
        return failed(f"处理本地文件或启动工具失败：{exc}")
    if on_progress:
        on_progress(1)
    return ConvertResult(True, source, dst, "保留原编码" if copied else "已重新编码", copied)
