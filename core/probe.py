"""识别本地文件，区分普通音频、可尝试导出的封装和不支持的版本。"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms
from .locate import find_ffprobe
from .process import MEDIA_INPUT, NO_WINDOW

VERDICT_OK = "ok"
VERDICT_WRAPPED = "wrapped"
VERDICT_PROFILE = "profile"
VERDICT_DEPENDENCY = "dependency"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_BROKEN = "broken"
VERDICT_NOT_AUDIO = "not_audio"
VERDICT_UNKNOWN = "unknown"
VERDICT_TEXT = {
    VERDICT_OK: "可转换", VERDICT_WRAPPED: "可尝试导出", VERDICT_PROFILE: "缺导出参数",
    VERDICT_DEPENDENCY: "缺依赖", VERDICT_UNSUPPORTED: "暂不支持", VERDICT_BROKEN: "文件异常",
    VERDICT_NOT_AUDIO: "无音轨", VERDICT_UNKNOWN: "无法识别",
}
AUDIO_EXTS = frozenset(".mp3 .flac .wav .m4a .aac .ogg .opus .ape .wma .aiff .aif .dsf .dff .wv .tta .amr .ac3 .mka .mp4 .mkv .mov .webm .avi .flv .tak .mpc".split())
INPUT_EXTS = AUDIO_EXTS | platforms.PLATFORM_EXTS
LOSSLESS_CODECS = {"flac", "alac", "ape", "wavpack", "tta", "truehd", "mlp", "shorten"}
CODEC_LABEL = {
    "vorbis": "Vorbis", "opus": "Opus", "pcm_s16le": "PCM 16bit", "pcm_s24le": "PCM 24bit",
    "pcm_s32le": "PCM 32bit", "pcm_f32le": "PCM float", "pcm_f64le": "PCM float64",
}
CONTAINER_LABEL = {
    "mp3": "MP3", "aac": "AAC (ADTS)", "flac": "FLAC", "wav": "WAV", "ogg": "Ogg",
    "asf": "ASF / WMA", "mov": "MP4 / M4A / MOV", "matroska": "Matroska / WebM",
    "aiff": "AIFF", "ape": "APE", "iff": "IFF / DSD", "dsf": "DSF",
}


@dataclass
class AudioInfo:
    path: Path
    size: int = 0
    container: str = "未知"
    format_name: str = ""
    codec: str = ""
    duration: float = 0.0
    bitrate: int = 0
    sample_rate: int = 0
    channels: int = 0
    bit_depth: int = 0
    lossless: bool = False
    audio_index: int = -1
    cover_index: int = -1
    cover_codec: str = ""
    probe_score: int = 0
    verdict: str = VERDICT_UNKNOWN
    note: str = ""
    wrapped: bool = False
    tags: dict[str, str] = field(default_factory=dict)
    adapter: str = ""
    requires_file_key: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def has_cover(self) -> bool:
        return self.cover_index >= 0

    @property
    def verdict_text(self) -> str:
        return VERDICT_TEXT.get(self.verdict, self.verdict)

    @property
    def duration_text(self) -> str:
        if self.duration <= 0:
            return "—"
        total = max(1, round(self.duration))
        return f"{total // 60}:{total % 60:02d}"

    @property
    def bitrate_text(self) -> str:
        return f"{self.bitrate / 1000:.0f} kbps" if self.bitrate > 0 else "—"

    @property
    def size_text(self) -> str:
        value = float(self.size)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return "—"

    @property
    def codec_text(self) -> str:
        return CODEC_LABEL.get(self.codec, self.codec.upper() if self.codec else "—")

    @property
    def quality_text(self) -> str:
        if self.lossless:
            depth = f"{self.bit_depth}/" if self.bit_depth else ""
            rate = f"{self.sample_rate / 1000:g}k" if self.sample_rate else ""
            return f"无损 {depth}{rate}".rstrip("/").strip()
        return "有损" if self.codec and self.sample_rate else "—"


def _number(value) -> float:
    try:
        n = float(value)
        return n if math.isfinite(n) and 0 <= n < 1e16 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _index(stream: dict) -> int:
    value = stream.get("index")
    return value if isinstance(value, int) and value >= 0 else -1


def _probe(info: AudioInfo) -> AudioInfo:
    ffprobe = find_ffprobe()
    if not ffprobe:
        info.verdict, info.note = VERDICT_DEPENDENCY, "找不到 ffprobe，无法读取音轨。"
        return info
    cmd = [ffprobe, "-v", "error", *MEDIA_INPUT, "-show_streams", "-show_format",
           "-of", "json", "-i", str(info.path)]
    try:
        result = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                                timeout=15, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        info.note = "探测超时。"
        return info
    except OSError:
        info.verdict, info.note = VERDICT_DEPENDENCY, "ffprobe 无法启动。"
        return info
    if result.returncode:
        info.note = "ffprobe 未识别为受支持的本地音视频。"
        return info
    try:
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        cover = next((s for s in streams if s.get("codec_type") == "video"
                      and s.get("disposition", {}).get("attached_pic")), None)
        info.format_name = str(fmt.get("format_name", "")).split(",")[0]
        info.container = CONTAINER_LABEL.get(info.format_name,
                                             fmt.get("format_long_name", "未知"))
        info.probe_score = int(_number(fmt.get("probe_score")))
        if audio is None:
            info.verdict = VERDICT_NOT_AUDIO
            info.note = "这个文件没有音轨。"
            return info
        info.codec = str(audio.get("codec_name", ""))
        info.audio_index = _index(audio)
        info.cover_index = _index(cover) if cover else -1
        info.cover_codec = str(cover.get("codec_name", "")) if cover else ""
        info.sample_rate = int(_number(audio.get("sample_rate")))
        info.channels = int(_number(audio.get("channels")))
        info.bit_depth = int(_number(audio.get("bits_per_raw_sample")) or _number(audio.get("bits_per_sample")))
        info.bitrate = int(_number(audio.get("bit_rate")) or _number(fmt.get("bit_rate")))
        info.duration = _number(audio.get("duration")) or _number(fmt.get("duration"))
        info.lossless = info.codec in LOSSLESS_CODECS or (info.codec.startswith("pcm_")
                         and info.codec not in {"pcm_alaw", "pcm_mulaw"})
        info.tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    except (ValueError, TypeError, AttributeError, UnicodeError):
        info.note = "无法读取 ffprobe 的音轨信息。"
        return info
    if info.audio_index < 0 or not info.codec or not info.sample_rate or not info.channels:
        info.verdict, info.note = VERDICT_BROKEN, "音轨的编码、采样率或声道信息不完整。"
        return info
    # 某些截断的 MP3 仍报告原文件时长；仅用于 MP3 的显著不匹配。
    expected = info.bitrate * info.duration / 8
    if info.format_name == "mp3" and expected >= 16 * 1024 and info.size < expected * 0.4:
        info.verdict, info.note = VERDICT_BROKEN, "按 MP3 的时长与码率估算，文件可能被截断。"
        return info
    info.verdict = VERDICT_OK
    info.note = "读取第一条音轨" + (" · 含封面" if info.has_cover else "")
    return info


def diagnose(path: str | Path, profile: platforms.Profile = platforms.EMPTY_PROFILE,
             *, detect_wrapped: bool = True) -> AudioInfo:
    path = Path(path).absolute()
    info = AudioInfo(path)
    try:
        if not path.is_file():
            raise OSError("路径不是普通文件")
        info.size = path.stat().st_size
        if not info.size:
            info.verdict, info.note = VERDICT_BROKEN, "空文件。"
            return info
        with path.open("rb") as handle:
            head = handle.read(64)
    except OSError as exc:
        info.verdict, info.note = VERDICT_BROKEN, f"读不到文件：{exc}"
        return info

    wrapped = platforms.inspect(path, head, info.size, profile, structural_only=True) if detect_wrapped else None
    if wrapped is None:
        _probe(info)
        if info.verdict in (VERDICT_OK, VERDICT_NOT_AUDIO) or not detect_wrapped:
            return info
        wrapped = platforms.inspect(path, head, info.size, profile) if detect_wrapped else None
    if wrapped is not None:
        info.wrapped, info.container, info.note = True, wrapped.label, wrapped.note
        info.verdict = VERDICT_WRAPPED if wrapped.state == "ready" else wrapped.state
        info.adapter, info.requires_file_key = wrapped.family, wrapped.requires_file_key
    elif info.verdict == VERDICT_UNKNOWN and (path.suffix.lower() in AUDIO_EXTS or head.startswith((b"ID3", b"fLaC", b"RIFF"))):
        info.verdict = VERDICT_BROKEN
    return info


def is_convertible(info: AudioInfo) -> bool:
    return info.verdict in (VERDICT_OK, VERDICT_WRAPPED)
