"""Builtin NCM defaults, using synthetic files encoded independently of libtakiyasha."""
from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core import locate, platforms, probe
from core.process import NO_WINDOW
from server.api import create_app
from server.config import ServerConfig
from tests.fixtures import ffmpeg, synthetic, write_audio
from tests.test_server import CONVERT, HAS_MEDIA_TOOLS, INSPECT, ServerTestCase, packet_hash


CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")
TAG_KEY = bytes.fromhex("2331346c6a6b5f215c5d2630553c2728")
TAGS = {"title": "独立合成音调", "artist": "合成歌手甲/合成歌手乙", "album": "中文测试专辑"}


def aes_ecb_pkcs7(data: bytes, key: bytes) -> bytes:
    # _backend() adds the already-installed adapter dependencies to sys.path.
    # Only pyaes provides encryption; no libtakiyasha encoder/cipher is used.
    import pyaes

    padding = 16 - len(data) % 16
    padded = data + bytes([padding]) * padding
    cipher = pyaes.AESModeOfOperationECB(key)
    return b"".join(cipher.encrypt(padded[i:i + 16]) for i in range(0, len(padded), 16))


def ncmdump_fixture(audio: bytes, cover: bytes, codec: str, *, core_key: bytes = CORE_KEY,
                    tag_key: bytes = TAG_KEY) -> bytes:
    """Implement ncmdump's on-disk layout and buildKeyBox, for generated tones only."""
    key = synthetic("ncmdump-independent-audio-key", 113)
    key_data = aes_ecb_pkcs7(b"neteasecloudmusic" + key, core_key)
    key_data = bytes(value ^ 0x64 for value in key_data)
    metadata = {
        "musicName": TAGS["title"],
        "artist": [["合成歌手甲", 0], ["合成歌手乙", 1]],
        "album": TAGS["album"],
        "format": codec,
    }
    tag_data = aes_ecb_pkcs7(b"music:" + json.dumps(metadata, ensure_ascii=False).encode("utf-8"), tag_key)
    tag_data = b"163 key(Don't modify):" + base64.b64encode(tag_data)
    tag_data = bytes(value ^ 0x63 for value in tag_data)

    box = list(range(256))
    last = key_offset = 0
    for i in range(256):
        swap = box[i]
        c = (swap + last + key[key_offset]) & 0xff
        box[i] = box[c]
        box[c] = swap
        last = c
        key_offset = (key_offset + 1) % len(key)
    encrypted = bytearray(len(audio))
    for offset, value in enumerate(audio):
        j = (offset + 1) & 0xff
        encrypted[offset] = value ^ box[(box[j] + box[(box[j] + j) & 0xff]) & 0xff]

    # Leave unused cover allocation to exercise the reader's allocation/length distinction.
    allocation = len(cover) + 23
    return (b"CTENFDAM\x00\x00" + struct.pack("<I", len(key_data)) + key_data
            + struct.pack("<I", len(tag_data)) + tag_data + bytes(5)
            + struct.pack("<II", allocation, len(cover)) + cover + bytes(allocation - len(cover))
            + encrypted)


class BuiltinNCMProfileTests(ServerTestCase):
    def test_profile_without_local_file_contains_builtin_ncm_keys(self):
        profile = ServerConfig(temp_dir=self.jobs, profile_path=None).load_profile()
        self.assertEqual(dict(profile["ncm"]), {"core_key": CORE_KEY, "tag_key": TAG_KEY})
        self.assert_clean()

    def test_local_profile_overrides_individual_fields_and_preserves_other_families(self):
        for field, default_field, default_value in (("core_key", "tag_key", TAG_KEY),
                                                     ("tag_key", "core_key", CORE_KEY)):
            with self.subTest(field=field):
                local_key = synthetic("local-override-" + field, 16)
                mask = synthetic("local-qmc-mask", 128)
                path = self.work / "synthetic-profile.json"
                path.write_text(json.dumps({"ncm": {field: local_key.hex()},
                                            "qmc1": {"mask": mask.hex()}}), encoding="utf-8")
                profile = ServerConfig(temp_dir=self.jobs, profile_path=path).load_profile()
                self.assertEqual(dict(profile["ncm"]), {field: local_key, default_field: default_value})
                self.assertEqual(profile["qmc1"]["mask"], mask)
                # A local override must not mutate the defaults used by the next application.
                fresh = ServerConfig(temp_dir=self.jobs, profile_path=None).load_profile()
                self.assertEqual(dict(fresh["ncm"]), {"core_key": CORE_KEY, "tag_key": TAG_KEY})
                self.assert_clean()

    def test_disabled_defaults_keep_explicit_local_configuration(self):
        config = ServerConfig(temp_dir=self.jobs, profile_path=None, enable_builtin_ncm=False)
        self.assertFalse(config.load_profile().get("ncm"))
        key = synthetic("explicit-core-with-defaults-disabled", 16)
        path = self.work / "explicit-profile.json"
        path.write_text(json.dumps({"ncm": {"core_key": key.hex()}}), encoding="utf-8")
        profile = ServerConfig(temp_dir=self.jobs, profile_path=path, enable_builtin_ncm=False).load_profile()
        self.assertEqual(dict(profile["ncm"]), {"core_key": key})
        self.assert_clean()


@unittest.skipUnless(HAS_MEDIA_TOOLS, "requires FFmpeg and ffprobe")
class BuiltinNCMUploadTests(ServerTestCase):
    def client(self, **overrides):
        config = {
            "temp_dir": self.jobs,
            "profile_path": None,
            "max_upload_bytes": 1024 * 1024,
            "max_output_bytes": 1024 * 1024,
            "concurrency": 1,
            "upload_timeout": 10.0,
            "processing_timeout": 30.0,
        }
        config.update(overrides)
        return TestClient(create_app(ServerConfig(**config)), raise_server_exceptions=False)

    @classmethod
    def setUpClass(cls):
        try:
            platforms._backend()
        except platforms.AdapterError as exc:
            if os.environ.get("REQUIRE_ADAPTERS"):
                raise
            raise unittest.SkipTest(str(exc)) from exc
        fixtures = tempfile.TemporaryDirectory(prefix="music-ncm-independent-fixtures-")
        cls.addClassCleanup(fixtures.cleanup)
        tone, song, cover = write_audio(Path(fixtures.name))
        cls.audio = {"flac": tone, "mp3": song}
        cls.cover = cover.read_bytes()
        cls.payloads = {codec: ncmdump_fixture(path.read_bytes(), cls.cover, codec)
                        for codec, path in cls.audio.items()}

    def assert_media_tags_and_cover(self, path: Path, codec: str):
        result = subprocess.run([locate.find_ffprobe(), "-v", "error", "-show_format", "-show_streams",
                                 "-of", "json", str(path)], check=True, capture_output=True,
                                timeout=30, creationflags=NO_WINDOW)
        info = json.loads(result.stdout)
        audio = [stream for stream in info["streams"] if stream["codec_type"] == "audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]["codec_name"], codec)
        if codec == "mp3":
            self.assertEqual(int(audio[0]["bit_rate"]), 128000)
        tags = {name.casefold(): value for name, value in info["format"].get("tags", {}).items()}
        for name, value in TAGS.items():
            self.assertEqual(tags.get(name), value, info)
        pictures = [stream for stream in info["streams"] if stream["codec_type"] == "video"]
        self.assertEqual(len(pictures), 1)
        self.assertEqual(pictures[0]["codec_name"], "png")
        self.assertEqual(pictures[0]["disposition"]["attached_pic"], 1)
        self.assertEqual(ffmpeg("-i", path, "-map", "0:v:0", "-c:v", "copy", "-f", "image2pipe", "-"),
                         self.cover)

    def test_inspect_renamed_ncm_is_ready_without_profile_path(self):
        with self.client() as client:
            for codec, payload in self.payloads.items():
                with self.subTest(inner_codec=codec):
                    response = self.upload(client, INSPECT, payload, filename="renamed.mp3")
                    self.assert_inspection(response, state="ready", adapter="ncm", wrapped=True,
                                           can_convert=True, size=len(payload))
        self.assert_clean()

    def test_convert_to_mp3_has_correct_codec_chinese_tags_and_png_cover(self):
        with self.client() as client:
            for codec, payload in self.payloads.items():
                with self.subTest(inner_codec=codec):
                    response = self.upload(client, CONVERT, payload, filename="renamed.mp3",
                                           format="mp3", bitrate="128k")
                    self.assert_attachment(response, ".mp3")
                    output = self.work / f"from-{codec}.mp3"
                    output.write_bytes(response.content)
                    self.assertEqual(probe.diagnose(output).verdict, probe.VERDICT_OK)
                    self.assert_media_tags_and_cover(output, "mp3")
                    self.assert_clean()

    def test_source_preserves_original_audio_packets_tags_and_cover(self):
        with self.client() as client:
            for codec, payload in self.payloads.items():
                with self.subTest(inner_codec=codec):
                    response = self.upload(client, CONVERT, payload, filename="renamed.mp3", format="source")
                    self.assert_attachment(response, "." + codec)
                    output = self.work / f"source.{codec}"
                    output.write_bytes(response.content)
                    self.assertEqual(packet_hash(output), packet_hash(self.audio[codec]))
                    self.assert_media_tags_and_cover(output, codec)
                    self.assert_clean()

    def test_disabled_builtin_keys_require_server_configuration(self):
        with self.client(enable_builtin_ncm=False) as client:
            for codec, payload in self.payloads.items():
                with self.subTest(inner_codec=codec):
                    response = self.upload(client, INSPECT, payload, filename="renamed.mp3")
                    self.assert_inspection(response, state="needs_server_config", adapter="ncm", wrapped=True,
                                           can_convert=False, size=len(payload))
                    response = self.upload(client, CONVERT, payload, filename="renamed.mp3", format="source")
                    self.assert_error(response, 422, "needs_server_config")
        self.assert_clean()

    def test_wrong_core_key_and_truncated_ncm_never_return_attachments(self):
        wrong_key = synthetic("ncm-wrong-core-key", 16)
        with self.client() as client:
            for codec, source in self.audio.items():
                wrong = ncmdump_fixture(source.read_bytes(), self.cover, codec, core_key=wrong_key)
                for label, payload, code in (("wrong-key", wrong, "conversion_failed"),
                                              ("truncated-header", self.payloads[codec][:12], "invalid_audio")):
                    with self.subTest(inner_codec=codec, corruption=label):
                        for fmt in ("mp3", "source"):
                            with self.subTest(format=fmt):
                                response = self.upload(client, CONVERT, payload, filename="renamed.mp3", format=fmt)
                                self.assert_error(response, 422, code, wrong_key.hex())
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
