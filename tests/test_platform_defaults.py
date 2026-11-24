"""Default QQ/KWM profiles exercised with generated tones and public key vectors.

QMC constants: davidxuang/MusicDecrypto, revision
b97bedc0a88fe827e088e3dafae3fbc4fd73612c, MusicDecrypto.Library (LGPL-2.1-or-later).
https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Tencent/QmcDecrypto.cs
Public known-answer key pairs below retain their exact upstream bytes. No upstream
cipher implementation is copied; existing libtakiyasha performs the decryption.
All media payloads are generated sine waves, never commercial recordings.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
import warnings
from pathlib import Path

from fastapi.testclient import TestClient

from core import platforms, probe
from server.api import create_app
from server.config import ServerConfig
from tests.fixtures import synthetic, write_audio
from tests.test_server import CONVERT, HAS_MEDIA_TOOLS, INSPECT, ServerTestCase, packet_hash


# QMCv1 mask: presburger/qmc-decoder (MIT), fixed revision:
# https://github.com/presburger/qmc-decoder/tree/fb1e04b03246db406e9fc15c4426b79ed321e4f5
QMC_MASK = bytes.fromhex(
    "c34ad6ca9067f752d8a166629f5b0900c35e95239f13117ed8923fbc90bb740ec"
    "347743d90aa3f51d8f411849fde951dc3c609d59ffa66f9d8f0f7a090a1d6f3c"
    "3f3d6a190a0f7f0d8f966fa9fd509c6c31d95de9f8411f4d8513faa903d7447c"
    "30e74bb90bc3f92d87e11139f23955ec300095b9f6266a1d852f76790cad64a"
)
QMC_CORE = bytes.fromhex("695646382b20150b")
QMC_GARBLE = (b"386ZJY!@#*$%^&)(", b"**#!(#$%&^a1cZ,T")
# KWM root/32-bit seed: LGPL-2.1-or-later, same fixed original revision:
# https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kuwo/Decrypto.cs
KWM_ROOT = b"MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"

# Each pair is (base64 of exact .key.enc file bytes, base64 of .key bytes).
# Source: https://github.com/davidxuang/MusicDecrypto/tree/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library.Tests/DataSets
# License: GNU LGPL v3 (the test-data project's own LICENSE, distinct from Library).
# https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library.Tests/LICENSE
PUBLIC_KEY_VECTORS = {
    'mflac-map': (
        (
            'ZVhjM2VGZFBlVTYrM2Y3R1ZlRjM1Yk1wSUVJUWo1SldPV3Q3Rytqc1I2OEh4M0JVRkJhdmtUUThkcFBkUDBYQkl3UGUrT2Zk'
            'c25UR1ZRcVB5ZzNHQ3RRU3JrZ0EwbXdTUWRyNERQektMa0VaRlgrQ2YxVjZDaHlpcE91QzZLVDM3ZUF4V01kVjFVSGY5L09D'
            'dnlkcjFkYzZTV0sxaWpSVWNQNklBSFFoaUIrbVpMYXk3WFhyU1BvMzJXamRCa245YzlzYTJTTHRJNDhhdGo1a2ZaNG9PcTZR'
            'R2VsZDJKQTNaKzN3d0NlNnVUSHRoS2FFSFk4dWZEWW9kRWUzcXFyallwemtkeDU1cEN0eENRYTFKaU5xRm1KaWdXbTRtM0NE'
            'emh1SjdZcW5qYkQrbVh4TGk3QlAxK3o0TDZuY2NFMmgrREdIVnFwR2pSOSs0TEJwZTRXSEI0RHJBelZwMnFRUlJRSnhlSGQx'
            'djg4PQ=='
        ),
        (
            'eXc3eFdPeU5RODU4NUp3eDNoakI0OVFMUEtpMzhGODlhd25yUTBmcTY2TlQ5VERxMXBwSE5yRnFoYURyVTVBRms5MTZENThJ'
            'NTNoODYzMDRHcU9GQ0N5RnpCZW02OERxaVhKODFiSUxFUXdHM1AzTU9ub056TTgyMGtOVzlMdjlJSkdObjlYbzQ5N3A4MkJM'
            'VG00aEFYOEpMQnMwVDJwaWxLdlQ0MjlzSzlqZmc1MDhHU2s0ZDA0N0p4ZHo1Rm91NGFhMzNPa3lGUkJVM3g0MzBtZ05CbjA0'
            'TGM5QnpYVUkySUdZWHYzRkdhOXFFNFZiNTRrU2pWdjhvZ2JnNDdqMw=='
        ),
    ),
    'mflac0-rc4': (
        (
            'WkZKNldETndOVnJqRUpaQjFvNlFqa1FWMlpiSFN3LzJFYjAwcTErNHo5U1ZXWXlGV08xUGNTUXJKNTMyNnViTGtsbWsyYWIz'
            'QUV5SUtOVXU4REZvQW9BYzlkcHpwVG1jK3Bka0JIak0vYlcyald4K2RDeUM4dk1USEUrREh3YUsxNFVFRUdXNDdaWE1EaTdQ'
            'UkNRMkpwbS9vWFZkSFRJbHlyYytiUm1LZk1pdGgwTDJsRlErblc4Q0NqVjZhbzV5ZHdrWmhoTk9tUmRyQ0RjVVhTSkg5UHZl'
            'WXdyYTkvd0FtR0tXU3M5bmVtdU1XS25ianAxUGtjeE5RZXhpY2lyVlRsTFg3UFZnUnlGeXpOeVVYZ3UrUjJTNFdUbUx3amQ4'
            'VXNPeVcvZGMybUVvWXQrdlkybHExWDRoRkJ0Y1FHT0FaRGVDK214ck4wRWNXOHRqUzZQNFRqT2ppT0tOTXhJZk1HU1drU0tM'
            'M0g3ejVLN25SMUFUaFcyMEgyYlAvTGNwc2RhTDB1Wi9qczF3RkdwZElmRng5cm5MQzc4aXRMMFd3RGxlSXFwOVRCTVgvTndh'
            'a0dnSVBJYmpCd2ZneUQ4ZDhYS1l1TEVzY0lIMFpHZGpzYWRCNVhqeWJnZEUzcHBmZUZFY1FpcXBub2RsVGFRUm0zS0RJRjlB'
            'VENsUDBtVGw4WGxzU29qc1o0Njh4c2VTMUliMmlpbngvMFNrSzNVdEpEd3A4REgzLytFTGlzZ1hkNjlCZjBwdmU3d2JyUXp6'
            'TVVzOS9PZ3Z2bzZVTHNJa1FmQXBKOGNTZWdEWWtsekdYaUxOSDdoWlluWERMTFNOZWpEN052UW91VUxTbUdzQmJHemhaNUlm'
            'ME5QLzZBaFNicHpxV0xEbGFiVERnZVdXbkZlWnBCbmxLNlNNeG8rWUZGazFZMFhMS3NkNjkramo='
        ),
        (
            'ZFJ6WDNwNVpZcUFscDdsTFNzOVpyMHJ3MWlFWnkyM2JCNjcweDRjaDJ3OTd4MTRad3BrMVVYYktVNEMyc09TN3VaME5CNVFN'
            'N3ZlOUduU3JyMkpIeFA3NGhWTk9Od1ZWNzdDZE9PVmI4MDczMTdLdnRJNVlkNmgwOGQwYzVXODhyZFY0NkMyMzVZR0RqVVNa'
            'ajUzMTRZVHp5MGI2dmdoNDEwMlA3RTI3M3I5MTFObDQ2NFhWODNIcjAwcmtBSGtrNzkxaU1HU0pIOTVHenROMjh1Mk52NXM5'
            'WHgzOFY2OW80YThhSVh4YngwZzFFTTA2MjNPRXRidE85enNxQ0pmajZNaFU3VDhpVlM2TTNxMTl4aHE2NzA3RTZyN3d6UE82'
            'WXA0QndCbWdnNEY5NUxmbDB2eUY3WU82Njk5dGI1TE1ucjdpRngyOW85OGhvaDNPM1JkOGg5SnV1OFAxd0c3dmRuTzVZdFJs'
            'eWtoVWx1WVFibE5uN1h3akJKNTNIQXlLVnJhV041ZEc3cHY3T01sMXMwUnlrUGgwcDIzcWZZekFBTWtaMU00MjJwRWQwN1RB'
            'OU9DS0QxaXliWXhXSDA2eGo2QThtem1jbllHVDlQMWE1WXRnMkVGNUxHM0lrbkwycjNBVXo5OVk3NTFhdTZDcjQwMW1mQVdL'
            'NjhXeUVCZTU='
        ),
    ),
    'mflac_v2-map': (
        (
            'VVZGTmRYTnBZeUJGYm1OV01peExaWGs2ZjMyeVNOMlRDM0dzalBDbFVnQWV2K0cyaVdQSHAxUEdvNmx3YXc5ODlFVGN4MENh'
            'TnlwcTVvcjZMejN6aHRUcndXT3UyN2FmWGt4NGQ5cXRuTXNVUGJSK1Y2d08rallDdFBNZEZUdkk4bUQyZWZYT0RYME0vK0ln'
            'eWJCMzc2L3c2SFdwQ1dyYlhVNmJaSXNYNW9oT2h6M0t1ejdWY2lPRTEyQytoeldEVjRlRVdRWlkvWW5IdU51cGdiYTRLTitY'
            'SlpsMzBxZ0ZPOTBvU1ZLaGVZTXFwMi93cldkMUtiazFiWUZ0LytrbzRpNFJOenYyNWhscFBnTFZCazVJSkxFVnZaeTlWYmVz'
            'WEZ1bDE1dkQyeGVGRXNMaGVnL1RxNTB4akZHOEdMLzFVWHFaSmZUQkdBdWtrc0o2OUNPUm9lYktLWkJ6Q2VnR1o5RENUbVIy'
            'SGxBbkkyY2cwN0ZySkQxVnZRdG9WaEtkYXVVOVNYV3c3Y3ZXdG9UMDdqaDVxT0dOcnlsSlRjOWowT2RNUFZNaU5SY1Zndjl4'
            'K2Z1SDMyRVJDUnltYy90amxGNXVTcTlBcnc1bjJtcWRqbUNrVGlFUG9QVlpmaG5PTWFUUzAwbFIrWm5KZmxEOERyMGlWV3VJ'
            'UlNTQUt6R2VBbjdLaDBGamtMNWhLZmZkRVVmY3k3VkN3TEhFV1pEV0lMTT0A'
        ),
        (
            'TjMyOUZvZHRYZGplMlNVVnBZNzQ4TFFpY085UGg5NTBnMHVWZ1lrTndCVm5aRmt2Q0RQcmVVeGdLcXptSUUzTmNrcnJTb1Zu'
            'NzVMZTJQazM1MEtsbDU4cDRFTHVXOHFWVzVCazkyM04xRjFSNzF6cm42aTU4STMzNlV6MnhUYXQ1STZtMnVWMDlPZWQ5MWw1'
            'U2tDNTNoeTF5MU4wRDNCbGcwOWZBbFdONkJ2RlMxTU9ERDFVMWoydDV4Rm55U05mQlM2MHY0RTEzOVUxQ0NUSTFkUTc2T3Fa'
            'dnIwSHJUNWhFNGpOdXNJTnlVb2NxMWFlbURBSFQwcTRCckY4VUpTYQ=='
        ),
    ),
}


def require_backend():
    try:
        return platforms._backend()
    except platforms.AdapterError as exc:
        if os.environ.get("REQUIRE_ADAPTERS"):
            raise
        raise unittest.SkipTest(str(exc)) from exc


def independent_kwm(audio: bytes, recipe: bytes, seed_bits: int, codec: str) -> bytes:
    """Encode KWM with integer/decimal/XOR only, independent of adapter ciphers.

    Both historical low-32-bit and whole-64-bit decimal seed variants use a
    32-byte repeated decimal string XORed with the published root. Payload
    starts at byte 1024; its mask index starts at zero there.
    """
    if len(recipe) != 8 or seed_bits not in (32, 64):
        raise ValueError("expected an 8-byte recipe and a 32/64-bit seed")
    seed = int.from_bytes(recipe[:seed_bits // 8], "little")
    decimal = str(seed).encode("ascii")
    repeated = (decimal * (32 // len(decimal) + 1))[:32]
    mask = bytes(a ^ b for a, b in zip(repeated, KWM_ROOT))
    header = bytearray(1024)
    header[:len(platforms.KWM_MAGIC)] = platforms.KWM_MAGIC
    header[24:32] = recipe
    label = ("128" + codec).encode("ascii")
    header[48:48 + len(label)] = label
    return bytes(header) + bytes(value ^ mask[i % 32] for i, value in enumerate(audio))


class BuiltinPlatformProfileTests(ServerTestCase):
    def test_default_configuration_contains_verified_public_values(self):
        config = ServerConfig(temp_dir=self.jobs, profile_path=None)
        self.assertTrue(config.enable_builtin_platforms)
        profile = config.load_profile()
        self.assertEqual(profile["qmc1"]["mask"], QMC_MASK)
        self.assertEqual(profile["qmc2"]["core_key"], QMC_CORE)
        self.assertEqual(tuple(profile["qmc2"]["garble_keys"]), QMC_GARBLE)
        self.assertEqual(profile["kwm"]["core_key"], KWM_ROOT)
        self.assertEqual(len(QMC_MASK), 128)
        self.assertEqual(len(KWM_ROOT), 32)
        self.assertNotIn("master_key", profile["qmc2"])
        self.assert_clean()

    def test_merge_profile_layers_overrides_without_mutating_defaults(self):
        from core.default_profiles import merge_profile

        baseline = merge_profile(platforms.EMPTY_PROFILE)
        self.assertEqual(set(baseline), {"ncm", "qmc1", "qmc2", "kwm", "kgm"})
        replacement = b"\x01" * 16
        merged = merge_profile({"ncm": {"tag_key": replacement}})
        self.assertEqual(merged["ncm"]["tag_key"], replacement)
        self.assertEqual(merged["ncm"]["core_key"], baseline["ncm"]["core_key"])
        # A later call must start from the published constants again.
        self.assertNotEqual(merge_profile(platforms.EMPTY_PROFILE)["ncm"]["tag_key"], replacement)
        self.assertFalse(merge_profile(platforms.EMPTY_PROFILE, include_defaults=False))
        self.assert_clean()

    def test_disabled_platform_defaults_preserve_explicit_local_overrides(self):
        disabled = ServerConfig(temp_dir=self.jobs, profile_path=None, enable_builtin_platforms=False)
        for family in ("qmc1", "qmc2", "kwm"):
            self.assertFalse(disabled.load_profile().get(family))
        local_key = synthetic("explicit-qmc-core-with-defaults-disabled", 8)
        path = self.work / "explicit-platform-profile.json"
        path.write_text(json.dumps({"qmc2": {"core_key": local_key.hex()}}), encoding="utf-8")
        profile = ServerConfig(temp_dir=self.jobs, profile_path=path,
                               enable_builtin_platforms=False).load_profile()
        self.assertEqual(dict(profile["qmc2"]), {"core_key": local_key})
        fresh = ServerConfig(temp_dir=self.jobs, profile_path=None).load_profile()
        self.assertEqual(fresh["qmc2"]["core_key"], QMC_CORE)
        self.assertEqual(tuple(fresh["qmc2"]["garble_keys"]), QMC_GARBLE)
        self.assert_clean()


class PublicQMCKeyVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_backend()

    def test_published_key_packages_match_upstream_plain_keys(self):
        from libtakiyasha.qmc._qmckeyciphers import QMCv2KeyEncryptV1, QMCv2KeyEncryptV2

        self.assertEqual(set(PUBLIC_KEY_VECTORS), {"mflac-map", "mflac_v2-map", "mflac0-rc4"})
        for name, (encoded_file, plain_file) in PUBLIC_KEY_VECTORS.items():
            with self.subTest(vector=name):
                package = base64.b64decode(base64.b64decode(encoded_file).rstrip(b"\0"), validate=True)
                expected = base64.b64decode(plain_file)
                is_v2 = package.startswith(b"QQMusic EncV2,Key:")
                self.assertEqual(is_v2, name == "mflac_v2-map")
                if is_v2:
                    actual = QMCv2KeyEncryptV2(QMC_CORE, QMC_GARBLE).decrypt(package[18:])
                else:
                    actual = QMCv2KeyEncryptV1(QMC_CORE).decrypt(package)
                self.assertEqual(len(expected), 512 if name == "mflac0-rc4" else 256)
                self.assertEqual(actual, expected)


@unittest.skipUnless(HAS_MEDIA_TOOLS, "requires FFmpeg and ffprobe")
class BuiltinPlatformUploadTests(ServerTestCase):
    def client(self, **overrides):
        # Deliberately use ServerConfig defaults, not ServerTestCase.client(),
        # whose default-profile switches are disabled for its legacy fixtures.
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
        lib = require_backend()
        from libtakiyasha.qmc import QMCv2QTag, QMCv2STag

        fixtures = tempfile.TemporaryDirectory(prefix="music-builtin-platform-fixtures-")
        cls.addClassCleanup(fixtures.cleanup)
        directory = Path(fixtures.name)
        tone, song, _ = write_audio(directory)
        cls.audio = {"flac": tone, "mp3": song}
        cls.payloads = []
        output = directory / "default-v1.qmcflac"
        with lib.QMCv1.new(mask=QMC_MASK) as encoder:
            encoder.write(tone.read_bytes())
            encoder.save(output)
        cls.payloads.append(("qmc1", output.name, "flac", output.read_bytes()))
        # Cover map and RC4 with both ordinary and EncV2-wrapped ekeys.
        for name, tail, method, codec in (
            ("default-length.mflac", "length", "map", "flac"),
            ("default-length-rc4.mflac0", "length", "rc4", "mp3"),
            ("default-QTag.mgg0", "qtag", "map", "flac"),
            ("default-QTag-rc4.mflac0", "qtag", "rc4", "mp3"),
            ("default-EncV2.mgg", "encv2", "map", "flac"),
            ("default-EncV2-rc4.mflac0", "encv2", "rc4", "mp3"),
            ("missing-key-STag.mflac0", "stag", "map", "flac"),
        ):
            output = directory / name
            with lib.QMCv2.new(method) as encoder:
                encoder.write(cls.audio[codec].read_bytes())
                opts = {"core_key": QMC_CORE}
                if tail == "qtag":
                    encoder.extra_info = QMCv2QTag()
                elif tail == "stag":
                    encoder.extra_info = QMCv2STag()
                    opts = {}
                elif tail == "encv2":
                    opts["garble_keys"] = QMC_GARBLE
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", lib.LibTakiyashaWarning)
                    encoder.save(filething=output, with_extra_info=tail in {"qtag", "stag"}, **opts)
            if tail == "stag":
                cls.stag = output.read_bytes()
            else:
                encoded = output.read_bytes()
                cls.payloads.append(("qmc2", name, codec, encoded))
                if tail in {"length", "encv2"}:
                    # The published EncV2 vector and original reader allow trailing NULs.
                    key_length = int.from_bytes(encoded[-4:], "little")
                    padded = encoded[:-4] + b"\0" + (key_length + 1).to_bytes(4, "little")
                    cls.payloads.append(("qmc2", "padded-" + name, codec, padded))
        for high_word in (0, 0x10203040):
            recipe = (0x12345678).to_bytes(4, "little") + high_word.to_bytes(4, "little")
            for bits in (32, 64):
                for codec, source in cls.audio.items():
                    name = f"independent-seed{bits}-high{high_word}-{codec}.kwm"
                    cls.payloads.append(("kwm", name, codec,
                                         independent_kwm(source.read_bytes(), recipe, bits, codec)))

    def test_inspect_all_builtin_wrappers_without_uploaded_keys_or_profile(self):
        with self.client() as client:
            for family, filename, codec, payload in self.payloads:
                with self.subTest(filename=filename):
                    # Native headers, plausible key tails or the static decoded header select the reader.
                    upload_name = "renamed.mp3"
                    response = self.upload(client, INSPECT, payload, filename=upload_name)
                    self.assert_inspection(response, state="ready", adapter=family, wrapped=True,
                                           can_convert=True, size=len(payload))
        self.assert_clean()

    def test_source_downloads_preserve_audio_packet_hashes(self):
        expected = {codec: packet_hash(path) for codec, path in self.audio.items()}
        with self.client() as client:
            for index, (family, filename, codec, payload) in enumerate(self.payloads):
                with self.subTest(filename=filename):
                    response = self.upload(client, CONVERT, payload, filename="renamed.bin", format="source")
                    self.assert_attachment(response, "." + codec)
                    output = self.work / f"source-{index}.{codec}"
                    output.write_bytes(response.content)
                    self.assertEqual(packet_hash(output), expected[codec])
                    self.assertEqual(probe.diagnose(output).verdict, probe.VERDICT_OK)
        self.assert_clean()

    def test_mp3_downloads_have_valid_audio_and_requested_bitrate(self):
        original_mp3_hash = packet_hash(self.audio["mp3"])
        with self.client() as client:
            for index, (family, filename, codec, payload) in enumerate(self.payloads):
                with self.subTest(filename=filename):
                    response = self.upload(client, CONVERT, payload, filename=filename,
                                           format="mp3", bitrate="128k")
                    self.assert_attachment(response, ".mp3")
                    output = self.work / f"mp3-{index}.mp3"
                    output.write_bytes(response.content)
                    info = probe.diagnose(output)
                    self.assertEqual((info.verdict, info.codec, info.bitrate),
                                     (probe.VERDICT_OK, "mp3", 128000))
                    if codec == "mp3":
                        copied = self.upload(client, CONVERT, payload, filename=filename, format="mp3")
                        self.assert_attachment(copied, ".mp3")
                        output.write_bytes(copied.content)
                        self.assertEqual(packet_hash(output), original_mp3_hash)
        self.assert_clean()

    def test_stag_still_requires_a_per_file_key(self):
        with self.client() as client:
            response = self.upload(client, INSPECT, self.stag, filename="missing-key.mflac0")
            self.assert_inspection(response, state="requires_file_key", adapter="qmc2", wrapped=True,
                                   can_convert=False, size=len(self.stag))
            for fmt in ("source", "mp3"):
                response = self.upload(client, CONVERT, self.stag, filename="missing-key.mflac0", format=fmt)
                self.assert_error(response, 422, "requires_file_key")
        self.assert_clean()

    def test_unknown_qmc_and_musicex_do_not_return_audio(self):
        unknown = synthetic("unknown-new-qmc-format", 1024) + b"\xff\xff\xff\xff"
        musicex = synthetic("unsupported-musicex-payload", 1024) + b"musicex\0"
        with self.client() as client:
            for filename, payload in (("unknown.mgg0", unknown), ("new-version.mflac0", musicex)):
                with self.subTest(filename=filename):
                    response = self.upload(client, INSPECT, payload, filename=filename)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["state"], "unsupported_format")
                    self.assertFalse(response.json()["can_convert"])
                    for fmt in ("source", "mp3"):
                        response = self.upload(client, CONVERT, payload, filename=filename, format=fmt)
                        self.assert_error(response, 422, "unsupported_format")
        self.assert_clean()


@unittest.skipUnless(HAS_MEDIA_TOOLS, "requires FFmpeg and ffprobe")
class BuiltinKugouUploadTests(ServerTestCase):
    client = BuiltinPlatformUploadTests.client

    @classmethod
    def setUpClass(cls):
        from tests.test_vendor_readers import fixture
        fixtures = tempfile.TemporaryDirectory(prefix="music-builtin-kugou-fixtures-")
        cls.addClassCleanup(fixtures.cleanup)
        tone, song, _ = write_audio(Path(fixtures.name))
        cls.audio = {"flac": tone, "mp3": song}
        cls.payloads = []
        for kind in (2, 3):
            for ext, magic in (("kgm", 0), ("kgma", 0), ("vpr", 1)):
                for codec, audio in cls.audio.items():
                    blob = fixture(audio.read_bytes(), magic_index=magic, kind=kind, offset=128)
                    cls.payloads.append(("vpr" if magic else "kgm", f"type{kind}.{ext}", codec, blob))

    def test_content_identifies_kugou_without_original_suffix(self):
        with self.client() as client:
            for family, filename, codec, blob in self.payloads:
                with self.subTest(filename=filename, codec=codec):
                    info = self.upload(client, INSPECT, blob, filename="renamed.mp3")
                    self.assert_inspection(info, state="ready", adapter=family, wrapped=True,
                                           can_convert=True, size=len(blob))

    def test_source_and_mp3_output_for_both_types_and_magics(self):
        expected = {codec: packet_hash(path) for codec, path in self.audio.items()}
        with self.client() as client:
            for index, (family, filename, codec, blob) in enumerate(self.payloads):
                with self.subTest(filename=filename, codec=codec):
                    source = self.upload(client, CONVERT, blob, filename="renamed.bin", format="source")
                    self.assert_attachment(source, "." + codec)
                    output = self.work / f"kg-source-{index}.{codec}"
                    output.write_bytes(source.content)
                    self.assertEqual(packet_hash(output), expected[codec])
                    mp3 = self.upload(client, CONVERT, blob, filename=filename, format="mp3", bitrate="128k")
                    self.assert_attachment(mp3, ".mp3")
                    output = self.work / f"kg-transcoded-{index}.mp3"
                    output.write_bytes(mp3.content)
                    parsed = probe.diagnose(output)
                    self.assertEqual((parsed.codec, parsed.bitrate), ("mp3", 128000))

    def test_bad_signature_unknown_slot_and_new_type_do_not_produce_audio(self):
        original = self.payloads[0][3]
        with self.client() as client:
            for offset, value, state in ((20, 4, "unsupported_format"), (24, 9, "unsupported_format"),
                                         (28, original[28] ^ 1, "invalid_audio")):
                with self.subTest(offset=offset):
                    broken = bytearray(original)
                    if offset == 28:
                        broken[offset] = value
                    else:
                        broken[offset:offset + 4] = value.to_bytes(4, "little")
                    info = self.upload(client, INSPECT, bytes(broken), filename="variant.kgma")
                    self.assertEqual(info.json()["state"], state)
                    result = self.upload(client, CONVERT, bytes(broken), filename="variant.kgma")
                    self.assert_error(result, 422, state)


if __name__ == "__main__":
    unittest.main()
