# SPDX-License-Identifier: LGPL-2.1-or-later
"""Python reader port of MusicDecrypto.Library's Kugou T2/T3 implementation.

Upstream: davidxuang/MusicDecrypto, revision
b97bedc0a88fe827e088e3dafae3fbc4fd73612c (David Huang and contributors).
Original files:
https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kugou/Decrypto.cs
https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kugou/T2Cipher.cs
https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kugou/T3Cipher.cs
License:
https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/LICENSE

This Python translation (modified 2026-11-23) replaces SIMD/padded buffers with byte operations,
adds bounded streaming and header validation, and omits encryption and tags.
This library is free software under LGPL-2.1-or-later and is provided WITHOUT
ANY WARRANTY, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
The supplied stream remains owned by the caller. Signature validation checks
cipher parameters; callers must separately validate the resulting audio.
"""
from __future__ import annotations

import hashlib
import operator
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Callable


KGM_MAGIC = bytes.fromhex("7cd532eb86027f4ba8afa68e0fff9914")
VPR_MAGIC = bytes.fromhex("0528bc96e9e45a4391aabdd07af53631")
_KGM_SIGNATURE = bytes.fromhex("3885ed92795ff84cb303614116a01d47")
_VPR_SIGNATURE = bytes.fromhex("1d5a05340c418d429c83926cae16fe56")


class ReaderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NativeInfo:
    cipher_data_offset: int
    cipher_data_len: int
    family: str
    label: str
    opener: Callable
    _decrypt: Callable[[bytes, int], bytes] = field(repr=False, compare=False)


def _hash16(data: bytes) -> bytes:
    digest = hashlib.md5(data).digest()
    return b"".join(digest[i:i + 2] for i in range(14, -1, -2))


def _cipher(kind: int, slot_key: bytes, file_key: bytes) -> Callable[[bytes, int], bytes]:
    if kind == 2:
        def decrypt(data: bytes, offset: int) -> bytes:
            return bytes((value ^ (value << 4) ^ slot_key[(offset + i) % len(slot_key)]) & 255
                         for i, value in enumerate(data))
    else:
        slot = _hash16(slot_key)
        key = _hash16(file_key) + b"\x6b"

        def decrypt(data: bytes, offset: int) -> bytes:
            output = bytearray(len(data))
            for i, value in enumerate(data):
                j = offset + i
                x = value ^ key[j % 17]
                mask = j ^ (j >> 8) ^ (j >> 16) ^ (j >> 24)
                output[i] = (x ^ (x << 4) ^ slot[j % 16] ^ mask) & 255
            return bytes(output)
    return decrypt


class _PayloadReader:
    def __init__(self, reader: BinaryIO, info: NativeInfo):
        self._reader = reader
        self._info = info
        self._position = 0
        self.closed = False
        reader.seek(info.cipher_data_offset)

    def __enter__(self):
        if self.closed:
            raise ValueError("I/O operation on closed reader")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def close(self) -> None:
        # Do not close the caller's underlying stream.
        self.closed = True

    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed reader")
        n = operator.index(n)
        remaining = self._info.cipher_data_len - self._position
        count = remaining if n < 0 else min(n, remaining)
        if count == 0:
            return b""
        data = self._reader.read(count)
        if not data or len(data) > count:
            raise ReaderError("broken", "酷狗音频载荷不完整。")
        result = self._info._decrypt(data, self._position)
        self._position += len(data)
        return result


def _open_native(source, **kwargs) -> _PayloadReader:
    # Parameters (including slot_key) were captured and validated by probe_kgm.
    reader, info = source
    return _PayloadReader(reader, info)


def probe_kgm(reader: BinaryIO, total_size: int, slot_key: bytes) -> NativeInfo:
    """Parse and validate KGM/KGMA/VPR type 2/3, slot 1, preserving position."""
    position = reader.tell()
    try:
        reader.seek(0)
        head = reader.read(60)
    finally:
        reader.seek(position)
    if len(head) != 60:
        raise ReaderError("broken", "酷狗文件头不完整。")
    if head[:16] == KGM_MAGIC:
        family, label, signature = "kgm", "酷狗 · KGM", _KGM_SIGNATURE
    elif head[:16] == VPR_MAGIC:
        family, label, signature = "vpr", "酷狗 · VPR", _VPR_SIGNATURE
    else:
        raise ReaderError("broken", "酷狗文件标识无效。")
    offset, kind, slot = struct.unpack_from("<iii", head, 16)
    if not 60 <= offset < total_size:
        raise ReaderError("broken", "酷狗音频偏移或长度无效。")
    if kind not in (2, 3):
        raise ReaderError("unsupported", f"{label} 加密类型 {kind} 暂不支持；支持 type 2/3。")
    if slot != 1:
        raise ReaderError("unsupported", f"{label} 密钥槽 {slot} 暂不支持；支持 slot 1。")
    if not isinstance(slot_key, bytes) or not slot_key:
        raise ReaderError("profile", "酷狗 slot_key 必须是非空字节串。")
    decrypt = _cipher(kind, slot_key, head[44:60])
    if decrypt(head[28:44], 0) != signature:
        raise ReaderError("broken", "酷狗文件签名校验失败，文件损坏或参数不匹配。")
    return NativeInfo(offset, total_size - offset, family, f"{label} type {kind}",
                      _open_native, decrypt)
