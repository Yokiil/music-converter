"""Common bounded file-backed reader for simple local audio wrappers.

SPDX-License-Identifier: LGPL-2.1-or-later
The Kuwo recipe/mask layout is translated from MusicDecrypto.Library,
Vendor/Kuwo/{Decrypto,Cipher}.cs at b97bedc0a88fe827e088e3dafae3fbc4fd73612c.
See THIRD_PARTY.md and licenses/MusicDecrypto-LGPL-2.1.txt.
Python modifications dated 2026-11-23: bounded streaming and two seed candidates.
This library is free software under LGPL-2.1-or-later, WITHOUT ANY WARRANTY.
The alternative 64-bit recipe interpretation is documented by the separately
installed libtakiyasha 2.1.1.post1 (MIT); candidates are selected by audio data,
not by inventing a platform version distinction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Callable


class MaskReaderError(ValueError):
    pass


def audio_prefix(data: bytes) -> bool:
    if data.startswith((b"fLaC", b"OggS", b"MAC ", b"wvpk", b"TTA1", b"DSD ", b"FRM8")):
        return True
    if len(data) >= 10 and data[:3] == b"ID3":
        return data[3] in (2, 3, 4) and all(byte < 128 for byte in data[6:10])
    if len(data) >= 12:
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return True
        if data[:4] == b"FORM" and data[8:12] in (b"AIFF", b"AIFC"):
            return True
        if data[4:8] == b"ftyp":
            return True
    if len(data) >= 4 and data[0] == 255 and data[1] & 0xe0 == 0xe0:
        version, layer = (data[1] >> 3) & 3, (data[1] >> 1) & 3
        if layer == 0:  # ADTS
            return (data[1] & 0xf6) == 0xf0 and ((data[2] >> 2) & 15) < 13
        return version != 1 and ((data[2] >> 4) & 15) not in (0, 15) and ((data[2] >> 2) & 3) != 3
    return False


def xor_at(data: bytes, mask: bytes, offset: int = 0) -> bytes:
    return bytes(value ^ mask[(offset + index) % len(mask)] for index, value in enumerate(data))


@dataclass(frozen=True)
class KWMInfo:
    cipher_data_offset: int
    cipher_data_len: int
    recipe: bytes
    opener: Callable


class _MaskedReader:
    def __init__(self, reader: BinaryIO, info: KWMInfo, mask: bytes):
        self.reader, self.info, self.mask, self.position = reader, info, mask, 0
        reader.seek(info.cipher_data_offset)

    def read(self, size: int = -1) -> bytes:
        remaining = self.info.cipher_data_len - self.position
        size = remaining if size < 0 else min(size, remaining)
        block = self.reader.read(size)
        output = xor_at(block, self.mask, self.position)
        self.position += len(block)
        return output

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _open_kwm(pair, *, core_key: bytes):
    reader, info = pair
    if len(core_key) != 32:
        raise MaskReaderError("KWM 核心参数应为 32 字节。")
    reader.seek(info.cipher_data_offset)
    encrypted_head = reader.read(min(64, info.cipher_data_len))
    masks = []
    for width in (8, 4):
        digits = str(int.from_bytes(info.recipe[:width], "little")).encode("ascii")
        repeated = (digits * (32 // len(digits) + 1))[:32]
        mask = bytes(left ^ right for left, right in zip(repeated, core_key))
        if mask not in masks:
            masks.append(mask)
        else:
            continue
        if audio_prefix(xor_at(encrypted_head, mask)):
            return _MaskedReader(reader, info, mask)
    raise MaskReaderError("KWM 解码结果不是受支持的音频，文件版本或参数可能不匹配。")


def probe_kwm(reader: BinaryIO, total_size: int) -> KWMInfo:
    reader.seek(0)
    head = reader.read(32)
    if len(head) < 32 or not head.startswith(b"yeelion-kuwo") or total_size <= 1024:
        raise MaskReaderError("KWM 文件头或音频长度无效。")
    return KWMInfo(1024, total_size - 1024, head[24:32], _open_kwm)
