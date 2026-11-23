"""Synthetic inverse-transform fixtures; no client data or media tools required."""
from __future__ import annotations

import hashlib
import io
import struct
import unittest

from core.vendor_readers import ReaderError, probe_kgm


SLOT = b"l,/'"
MAGICS = (bytes.fromhex("7cd532eb86027f4ba8afa68e0fff9914"),
          bytes.fromhex("0528bc96e9e45a4391aabdd07af53631"))
SIGNATURES = (bytes.fromhex("3885ed92795ff84cb303614116a01d47"),
              bytes.fromhex("1d5a05340c418d429c83926cae16fe56"))
FILE_KEY = bytes(range(16))


def encode_fixture(data, kind, slot_key=SLOT):
    # Independent test encoder: permute digest using an explicit index table;
    # invert the byte transform by recovering low and high nibbles separately.
    def digest(key):
        raw = hashlib.md5(key).digest()
        return bytes(raw[i] for i in (14, 15, 12, 13, 10, 11, 8, 9,
                                      6, 7, 4, 5, 2, 3, 0, 1))

    skey = slot_key if kind == 2 else digest(slot_key)
    fkey = digest(FILE_KEY) + b"\x6b"
    result = bytearray()
    for j, value in enumerate(data):
        mask = 0 if kind == 2 else sum(((j >> shift) & 255) << shift
                                     for shift in (0, 8, 16, 24))
        if kind == 3:
            # XOR the individual four position bytes; do not invoke reader code.
            mask = (mask & 255) ^ ((mask >> 8) & 255) ^ ((mask >> 16) & 255) ^ ((mask >> 24) & 255)
        target = value ^ skey[j % len(skey)] ^ mask
        low = target & 15
        high = ((target >> 4) & 15) ^ low
        encrypted = (high << 4) | low
        if kind == 3:
            encrypted ^= fkey[j % 17]
        result.append(encrypted)
    return bytes(result)


def fixture(payload, magic_index=0, kind=3, offset=93, slot_key=SLOT):
    return (MAGICS[magic_index] + struct.pack("<iii", offset, kind, 1)
            + encode_fixture(SIGNATURES[magic_index], kind, slot_key) + FILE_KEY
            + bytes(offset - 60) + encode_fixture(payload, kind, slot_key))


class NativeKugouTests(unittest.TestCase):
    def assert_error(self, blob, code, **kwargs):
        with self.assertRaises(ReaderError) as caught:
            probe_kgm(io.BytesIO(blob), kwargs.get("size", len(blob)), kwargs.get("key", SLOT))
        self.assertEqual(caught.exception.code, code)

    def test_both_magics_and_types_stream_across_position_boundaries(self):
        payload = bytearray((j * 37 + (j >> 7) + 13) & 255 for j in range(131119))
        for magic in (0, 1):
            for kind in (2, 3):
                blob = fixture(payload, magic, kind)
                for chunk in (1, 17, 255, 256, 257, 65535, 65536, 65537):
                    with self.subTest(magic=magic, kind=kind, chunk=chunk):
                        source = io.BytesIO(blob)
                        source.seek(11)
                        info = probe_kgm(source, len(blob), SLOT)
                        self.assertEqual(source.tell(), 11)
                        self.assertEqual(info.family, "kgm" if magic == 0 else "vpr")
                        self.assertEqual(info.cipher_data_offset, 93)
                        self.assertEqual(info.cipher_data_len, len(payload))
                        decoded = bytearray()
                        with info.opener((source, info), slot_key=b"ignored") as stream:
                            self.assertEqual(stream.read(0), b"")
                            while block := stream.read(chunk):
                                decoded.extend(block)
                            self.assertEqual(stream.read(), b"")
                        self.assertEqual(decoded, payload)
                        self.assertFalse(source.closed)
                        with self.assertRaises(ValueError):
                            stream.read(1)

    def test_read_all_and_minimum_header_offset(self):
        payload = bytes(range(256))
        blob = fixture(payload, offset=60)
        source = io.BytesIO(blob)
        info = probe_kgm(source, len(blob), SLOT)
        with info.opener((source, info)) as stream:
            self.assertEqual(stream.read(7) + stream.read(), payload)

    def test_unsupported_type_and_slot(self):
        original = fixture(b"data")
        for field, values in ((20, (0, 1, 4, 5, -1)), (24, (0, 2, -1))):
            for value in values:
                with self.subTest(field=field, value=value):
                    blob = bytearray(original)
                    struct.pack_into("<i", blob, field, value)
                    self.assert_error(blob, "unsupported")

    def test_broken_signature_magic_and_header_bounds(self):
        original = fixture(b"data")
        for index in (0, 28, 43):
            blob = bytearray(original)
            blob[index] ^= 1
            self.assert_error(blob, "broken")
        for offset in (-1, 0, 59, len(original), len(original) + 1):
            blob = bytearray(original)
            struct.pack_into("<i", blob, 16, offset)
            self.assert_error(blob, "broken")
        self.assert_error(original[:59], "broken")
        self.assert_error(original, "broken", key=b"incorrect")
        self.assert_error(original, "profile", key=b"")

    def test_caller_supplied_slot_key_is_captured(self):
        key = b"custom-slot-one-key"
        for kind in (2, 3):
            blob = fixture(b"synthetic bytes", kind=kind, slot_key=key)
            source = io.BytesIO(blob)
            info = probe_kgm(source, len(blob), key)
            with info.opener((source, info), slot_key=SLOT) as stream:
                self.assertEqual(stream.read(), b"synthetic bytes")

    def test_truncated_payload_is_reported_without_closing_source(self):
        blob = fixture(b"12345")
        source = io.BytesIO(blob)
        info = probe_kgm(source, len(blob), SLOT)
        source.truncate(len(blob) - 2)
        with info.opener((source, info)) as stream:
            self.assertEqual(stream.read(), b"123")
            with self.assertRaises(ReaderError) as caught:
                stream.read()
            self.assertEqual(caught.exception.code, "broken")
        self.assertFalse(source.closed)


if __name__ == "__main__":
    unittest.main()
