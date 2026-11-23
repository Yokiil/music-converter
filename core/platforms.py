"""可选的本地封装读取适配器。参数由调用方提供，可来自公开格式默认值或本机配置。"""

from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import BinaryIO, Callable, Mapping

NCM_MAGIC = b"CTENFDAM"
KGM_MAGIC = bytes.fromhex("7cd532eb86027f4ba8afa68e0fff9914")
VPR_MAGIC = bytes.fromhex("0528bc96e9e45a4391aabdd07af53631")
KWM_MAGIC = b"yeelion-kuwo"
QMC1_EXTS = {*(f".qmc{i}" for i in range(10)), ".qmcflac", ".qmcogg", ".qmcra"}
QMC2_EXTS = {".mflac", ".mflac0", ".mgg", ".mgg0", ".mgg1", ".mmp4"}
_HINTS = {
    ".ncm": "网易云音乐", ".uc": "网易云音乐", ".kgm": "酷狗音乐",
    ".kgma": "酷狗音乐", ".vpr": "酷狗音乐", ".kwm": "酷我音乐",
    ".mgalbum": "QQ 音乐", ".tkm": "QQ 音乐",
    **dict.fromkeys(QMC1_EXTS | QMC2_EXTS, "QQ 音乐"),
}
PLATFORM_EXTS = frozenset(_HINTS)
MAX_WRAPPED_SIZE = 256 * 1024 ** 2  # 后端会把音频载荷读入内存
MAX_PROFILE_SIZE = 64 * 1024
MAX_COVER_SIZE = 16 * 1024 ** 2
MAX_QMC_FOOTER = 4096
CHUNK = 256 * 1024
_HEADER_LABELS = {"ncm": "网易云 · NCM", "kgm": "酷狗 · KGM", "vpr": "酷狗 · VPR", "kwm": "酷我 · KWM"}


class AdapterError(Exception):
    def __init__(self, code: str, message: str, *, family: str = "", label: str = "",
                 requires_file_key: bool = False):
        super().__init__(message)
        self.code = code
        self.family = family
        self.label = label
        self.requires_file_key = requires_file_key


@dataclass(frozen=True)
class WrappedInfo:
    family: str
    label: str
    state: str
    note: str
    requires_file_key: bool = False


@dataclass(frozen=True)
class ExtractedAudio:
    path: Path
    tags: dict[str, str]
    cover: Path | None


Profile = Mapping[str, Mapping[str, bytes | tuple[bytes, ...]]]
EMPTY_PROFILE: Profile = MappingProxyType({})
_FIELDS = {
    "ncm": {"core_key": (16,), "tag_key": (16,)},
    "qmc1": {"mask": (44, 128, 256)},
    "qmc2": {"core_key": (8,), "master_key": (128, 256, 512), "garble_keys": (16,)},
    "kgm": {"table1": (272,), "table2": (272,), "tablev2": (272,), "vpr_key": (17,), "slot_key": (4,)},
    "kwm": {"core_key": (32,)},
}


def load_profile(path: str | Path) -> Profile:
    """读取本地 JSON；错误中只报告字段，不回显输入值。"""
    def decode(value, field: str, lengths: tuple[int, ...]) -> bytes:
        if (not isinstance(value, str) or len(value) not in {n * 2 for n in lengths}
                or re.fullmatch(r"[0-9a-fA-F]+", value) is None):
            allowed = "/".join(map(str, lengths))
            raise ValueError(f"{field} 必须是 {allowed} 字节的十六进制字符串。")
        return bytes.fromhex(value)

    with Path(path).open("rb") as stream:
        data = stream.read(MAX_PROFILE_SIZE + 1)
    if len(data) > MAX_PROFILE_SIZE:
        raise ValueError("参数文件超过 64 KiB。")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        raise ValueError("参数文件不是有效的 UTF-8 JSON。") from None
    if not isinstance(raw, dict) or not raw.keys() <= _FIELDS.keys():
        raise ValueError("参数文件只接受 ncm、qmc1、qmc2、kgm、kwm 顶层对象。")
    values = {}
    for family, fields in raw.items():
        schema = _FIELDS[family]
        if not isinstance(fields, dict) or not fields.keys() <= schema.keys():
            raise ValueError(f"{family} 中含有不支持的字段。")
        decoded = {}
        for key, value in fields.items():
            name = f"{family}.{key}"
            if key == "garble_keys":
                if not isinstance(value, list) or not 1 <= len(value) <= 16:
                    raise ValueError(f"{name} 必须是一个非空的十六进制字符串数组。")
                decoded[key] = tuple(decode(x, name, schema[key]) for x in value)
            else:
                decoded[key] = decode(value, name, schema[key])
        values[family] = MappingProxyType(decoded)
    return MappingProxyType(values)


@lru_cache(maxsize=1)
def _backend():
    deps = Path(__file__).resolve().parent.parent / ".deps"
    if deps.is_dir() and str(deps) not in sys.path:
        sys.path.insert(0, str(deps))
    try:
        backend = importlib.import_module("libtakiyasha")
        from importlib.metadata import version
        if version("libtakiyasha") != "2.1.1.post1":
            raise ImportError("unsupported adapter version")
        return backend
    except (ImportError, OSError, ValueError, EOFError) as exc:
        raise AdapterError("dependency", "需要安装指定版本的平台适配器，见 README。") from exc


def exact_family(head: bytes) -> str:
    if head.startswith(NCM_MAGIC):
        return "ncm"
    if head.startswith(KGM_MAGIC):
        return "kgm"
    if head.startswith(VPR_MAGIC):
        return "vpr"
    if head.startswith(KWM_MAGIC):
        return "kwm"
    return ""


def _invalid(message: str = "封装头或长度字段无效，可能是文件不完整。") -> AdapterError:
    return AdapterError("broken", message)


def _validate_ncm(stream: BinaryIO, size: int) -> None:
    # 第三方解析器按文件的长度字段分配内存，先校验上限和文件边界。
    def u32() -> int:
        data = stream.read(4)
        if len(data) != 4:
            raise _invalid()
        return int.from_bytes(data, "little")

    stream.seek(10)
    key_len = u32()
    if not 16 <= key_len <= 4096 or key_len % 16 or stream.tell() + key_len > size:
        raise _invalid()
    stream.seek(key_len, 1)
    tag_len = u32()
    if tag_len > 1024 ** 2 or stream.tell() + tag_len + 13 >= size:
        raise _invalid()
    stream.seek(tag_len + 5, 1)
    cover_slot, cover_len = u32(), u32()
    if cover_len > min(cover_slot, MAX_COVER_SIZE) or stream.tell() + cover_slot >= size:
        raise _invalid()


def _qmc_footer(stream: BinaryIO, size: int) -> str:
    if size < 9:
        return ""
    stream.seek(-8, 2)
    trailer = stream.read(8)
    mark = trailer[-4:]
    if mark in (b"QTag", b"STag"):
        length = int.from_bytes(trailer[:4], "big")
        if not 0 < length <= min(MAX_QMC_FOOTER, size - 9):
            raise _invalid("QMC 文件尾长度无效。")
        return mark.decode("ascii")
    length = int.from_bytes(mark, "little")
    if 32 <= length <= min(MAX_QMC_FOOTER, size - 5):
        return "长度文件尾"
    return ""


def _tagged_qmc(stream: BinaryIO, size: int) -> bool:
    if size < 9:
        return False
    stream.seek(-4, 2)
    return stream.read(4) in (b"QTag", b"STag")


def _qmc_candidate(stream: BinaryIO, size: int, profile: Profile) -> str:
    """Only bounded, structurally plausible candidates; final audio is still validated."""
    if size < 16:
        return ""
    stream.seek(-8, 2)
    ending = stream.read(8)
    if ending in (b"musicex\x00",) or ending[-4:] in (b"QTag", b"STag"):
        return "qmc2"
    length = int.from_bytes(ending[-4:], "little")
    if 32 <= length <= min(MAX_QMC_FOOTER, size - 5):
        stream.seek(-(length + 4), 2)
        key_data = stream.read(length).rstrip(b"\x00")
        if len(key_data) % 4 == 0 and re.fullmatch(rb"[A-Za-z0-9+/]+={0,2}", key_data):
            return "qmc2"
    mask = profile.get("qmc1", {}).get("mask")
    if mask is not None:
        try:
            from .mask_reader import audio_prefix
            head_reader = _backend().QMCv1.new(mask=mask)
            with head_reader:
                stream.seek(0)
                head_reader.write(stream.read(64), nocryptlayer=True)
                head_reader.seek(0)
                if audio_prefix(head_reader.read()):
                    return "qmc1"
        except (AdapterError, ValueError, TypeError):
            pass
    return ""


def _open_spec(stream: BinaryIO, path: Path, head: bytes, size: int, profile: Profile):
    family = exact_family(head)
    candidate = _qmc_candidate(stream, size, profile) if not family else ""
    ext = path.suffix.lower()
    label = _HEADER_LABELS.get(family, _HINTS.get(ext, "平台封装"))
    requires_file_key = False
    if size > MAX_WRAPPED_SIZE:
        raise AdapterError("unsupported", "平台封装首版每个文件最大 256 MiB。")
    if family == "ncm":
        _validate_ncm(stream, size)
        backend = _backend()
        info = backend.probeinfo_ncm(stream)
        params = dict(profile.get("ncm", {}))
        required = ("core_key",)
    elif family in ("kgm", "vpr"):
        params = dict(profile.get("kgm", {}))
        tables = ("table1", "table2", "tablev2")
        if any(name in params for name in tables):
            # Retain the legacy table reader for explicit, custom test/operator profiles.
            if len(head) < 60 or not 60 <= int.from_bytes(head[16:20], "little") < size:
                raise _invalid()
            if int.from_bytes(head[20:24], "little") != 3:
                raise AdapterError("unsupported", "显式三表读取器仅支持酷狗 type 3。")
            info = _backend().probeinfo_kgmvpr(stream)
            params.pop("slot_key", None)
            required = tables + (("vpr_key",) if family == "vpr" else ())
            if family != "vpr":
                params.pop("vpr_key", None)
            label += " v3 (显式参数)"
        else:
            from .vendor_readers import ReaderError, probe_kgm
            if "slot_key" not in params:
                raise AdapterError("profile", f"{label}：缺少服务器固定读取参数。", family=family, label=label)
            try:
                info = probe_kgm(stream, size, params["slot_key"])
            except ReaderError as exc:
                raise AdapterError(exc.code, str(exc), family=family, label=label) from exc
            label = info.label
            required = ("slot_key",)
    elif family == "kwm":
        from .mask_reader import probe_kwm
        info = probe_kwm(stream, size)
        params = dict(profile.get("kwm", {}))
        required = ("core_key",)
    elif ext in QMC1_EXTS | QMC2_EXTS or candidate:
        stream.seek(max(0, size - 8))
        if stream.read(8) == b"musicex\x00":
            raise AdapterError("unsupported", "QQ musicex 新版封装暂不支持。", family="qmc2", label="QQ · musicex")
        footer = _qmc_footer(stream, size)
        if footer:
            backend = _backend()
            try:
                info = backend.probeinfo_qmcv2(stream)
            except (ValueError, TypeError, IndexError):
                if footer != "长度文件尾":
                    raise _invalid("QMC v2 文件尾解析失败。") from None
                info = None
            if (info is not None and info.cipher_data_len > 0
                    and (info.master_key_encrypted or footer == "STag")):
                family, label = "qmc2", f"QQ · QMC v2 ({footer})"
                params = dict(profile.get(family, {}))
                requires_file_key = footer == "STag"
                required = (("master_key",) if requires_file_key or "master_key" in params
                            else info.opener_kwargs_required)
            elif footer != "长度文件尾":
                raise _invalid("QMC v2 文件尾解析失败。")
        if not family:
            if ext not in QMC1_EXTS and candidate != "qmc1":
                raise AdapterError("unsupported", "没有识别到受支持的 QMC v2 文件尾；此版本暂不支持。")
            family, label = "qmc1", "QQ · QMC v1 候选"
            info = _backend().probeinfo_qmcv1(stream, skip_qmcv1_probe=candidate == "qmc1")
            params = dict(profile.get(family, {}))
            required = ("mask",)
    else:
        raise AdapterError("unsupported", "仅后缀像平台封装，未识别到受支持的文件结构。")

    if info is None or not 0 < info.cipher_data_len <= size:
        raise _invalid()
    absent = [field for field in required if field not in params]
    if absent:
        section = "kgm" if family == "vpr" else family
        raise AdapterError("profile", f"{label}：缺少 " + "、".join(f"{section}.{k}" for k in absent) + "。",
                           family=family, label=label, requires_file_key=requires_file_key)
    return family, label, info, params


def inspect(path: Path, head: bytes, size: int, profile: Profile = EMPTY_PROFILE,
            *, structural_only: bool = False) -> WrappedInfo | None:
    """只解析文件结构，不宣称已验证密钥或音频内容。"""
    exact = exact_family(head)
    ext = path.suffix.lower()
    label = _HEADER_LABELS.get(exact, _HINTS.get(ext, "平台封装"))
    try:
        with path.open("rb") as reader:
            candidate = _qmc_candidate(reader, size, profile) if not exact else ""
            if candidate:
                exact, label = candidate, "QQ · QMC"
            if not exact and (structural_only or ext not in PLATFORM_EXTS):
                return None
            family, label, _, _ = _open_spec(reader, path, head, size, profile)
    except AdapterError as exc:
        return WrappedInfo(exc.family or exact, exc.label or label, exc.code, str(exc), exc.requires_file_key)
    except (OSError, TypeError, ValueError, IndexError):
        return WrappedInfo(exact, label, "broken", "读取文件封装失败。")
    return WrappedInfo(family, label, "ready", "参数齐全；导出时校验原始音频。")


def _open_audio(reader, info, family, params):
    if (family == "qmc2" and info.cipher_ctor is None and info.master_key_encrypted
            and "master_key" not in params):
        # 后端按少数固定密文长度猜编码，EncV2 的额外层数变化会让猜测失败。
        # 让同一版本的后端先读主密钥，再由它按真正的主密钥类型打开。
        from libtakiyasha.qmc._qmckeyciphers import QMCv2KeyEncryptV1, QMCv2KeyEncryptV2

        if info.master_key_encryption_ver == 2:
            key_reader = QMCv2KeyEncryptV2(params["core_key"], params["garble_keys"])
        else:
            key_reader = QMCv2KeyEncryptV1(params["core_key"])
        params = {"master_key": key_reader.decrypt(info.master_key_encrypted)}
    return info.opener((reader, info), **params)


def extract(path: Path, directory: Path, profile: Profile, *, cancel: Event | None = None,
            on_progress: Callable[[float], None] | None = None) -> ExtractedAudio:
    """提取到任务临时目录；由调用方用 ffprobe 验证后再发布。"""
    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise AdapterError("cancelled", "已停止。")

    check_cancel()
    tags: dict[str, str] = {}
    cover_path = None
    payload = directory / "payload.audio"
    with path.open("rb") as reader:
        head = reader.read(64)
        size = reader.seek(0, 2)
        family, _, info, params = _open_spec(reader, path, head, size, profile)
        try:
            decoded = _open_audio(reader, info, family, params)
        except Exception as exc:
            raise AdapterError("decode", "无法打开封装；请核对参数和文件版本。") from exc
        with decoded, payload.open("xb") as writer:
            if family == "ncm":
                tag = decoded.ncm_tag
                artists = tag.artist if isinstance(tag.artist, (tuple, list)) else ()
                artist = "/".join(a[0] for a in artists if isinstance(a, (tuple, list)) and a and isinstance(a[0], str))
                for name, value in (("title", tag.musicName), ("album", tag.album), ("artist", artist)):
                    if isinstance(value, str) and value.strip():
                        tags[name] = re.sub(r"[\x00-\x1f]", " ", value[:4096]).strip()
                image = decoded.cover_data
                if image.startswith(b"\x89PNG\r\n\x1a\n"):
                    cover_path = directory / "cover.png"
                elif image.startswith(b"\xff\xd8\xff"):
                    cover_path = directory / "cover.jpg"
                if cover_path is not None:
                    cover_path.write_bytes(image)
            remaining = info.cipher_data_len
            while remaining:
                check_cancel()
                try:
                    block = decoded.read(min(CHUNK, remaining))
                except Exception as exc:
                    raise AdapterError("decode", "读取音频失败；请核对参数和文件。") from exc
                if not block or len(block) > remaining:
                    raise _invalid("音频数据不完整。")
                writer.write(block)
                remaining -= len(block)
                if on_progress is not None:
                    on_progress(1 - remaining / info.cipher_data_len)
    check_cancel()
    return ExtractedAudio(payload, tags, cover_path)
