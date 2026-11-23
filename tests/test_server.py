from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core import locate, platforms, probe
from server.api import create_app
from server.config import ServerConfig
from tests.fixtures import ffmpeg, generate, synthetic, write_audio


INSPECT = "/api/v1/inspect"
CONVERT = "/api/v1/convert"
OCTET_STREAM = "application/octet-stream"
HAS_FFMPEG = locate.ffmpeg_available()
HAS_MEDIA_TOOLS = HAS_FFMPEG and locate.probe_available()


def packet_hash(path: Path) -> bytes:
    return ffmpeg("-i", path, "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-")


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="music-server-test-")
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.jobs = self.work / "private-jobs"
        self.jobs.mkdir()

    def client(self, **overrides):
        config = {
            "temp_dir": self.jobs,
            "profile_path": None,
            "enable_builtin_ncm": False,  # This suite exercises explicit operator configuration.
            "enable_builtin_platforms": False,
            "max_upload_bytes": 1024 * 1024,
            "max_output_bytes": 1024 * 1024,
            "concurrency": 1,
            "upload_timeout": 10.0,
            "processing_timeout": 30.0,
        }
        config.update(overrides)
        return TestClient(create_app(ServerConfig(**config)), raise_server_exceptions=False)

    def upload(self, client, route, content, *, filename=None, media_type=OCTET_STREAM, **options):
        if filename is not None:
            options["filename"] = filename
        return client.post(route, params=options, content=content, headers={"Content-Type": media_type})

    def assert_clean(self):
        self.assertFalse(list(self.jobs.iterdir()), "request left a job directory or temporary file behind")

    def assert_private(self, text, *values):
        for path in (self.work, self.jobs):
            self.assertNotIn(str(path).casefold(), text.casefold())
            self.assertNotIn(path.as_posix().casefold(), text.casefold())
        for value in values:
            self.assertNotIn(value.casefold(), text.casefold())

    def assert_error(self, response, status, code, *private_values):
        self.assertEqual(response.status_code, status, response.text)
        self.assertNotIn("content-disposition", response.headers)
        self.assertEqual(response.headers["content-type"].split(";", 1)[0], "application/json")
        body = response.json()
        self.assertEqual(set(body), {"error"})
        error = body["error"]
        self.assertEqual(set(error), {"code", "message"})
        self.assertEqual(error["code"], code)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"].strip())
        self.assert_private(error["message"], *private_values)
        self.assert_clean()

    def assert_inspection(self, response, *, state, adapter, wrapped, can_convert, size):
        self.assertEqual(response.status_code, 200, response.text)
        info = response.json()
        self.assertEqual(info["state"], state)
        self.assertEqual(info["adapter"], adapter)
        self.assertIs(info["wrapped"], wrapped)
        self.assertIs(info["can_convert"], can_convert)
        self.assertEqual(info["size_bytes"], size)
        self.assertIn("container", info)
        self.assertIn("codec", info)
        self.assertIsInstance(info["message"], str)
        self.assert_private(info["message"])
        self.assert_clean()
        return info

    def assert_attachment(self, response, suffix):
        self.assertEqual(response.status_code, 200, response.text[:400])
        self.assertTrue(response.content)
        self.assertFalse(response.headers["content-type"].startswith("application/json"))
        disposition = Message()
        disposition["Content-Disposition"] = response.headers["content-disposition"]
        self.assertEqual(disposition.get_content_disposition(), "attachment")
        name = disposition.get_filename()
        self.assertIsNotNone(name)
        self.assertTrue(name.lower().endswith(suffix), name)
        for separator in ("/", "\\", ":"):
            self.assertNotIn(separator, name)
        self.assert_private(name)
        self.assert_clean()


class WebsiteTests(ServerTestCase):
    def test_website_and_assets_are_served_without_exposing_configuration(self):
        with self.client() as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("音乐格式转换", page.text)
            self.assertIn("script-src 'self'", page.headers["content-security-policy"])
            self.assertNotIn("server-profile.local.json", page.text)
            for name, kind in (("app.js", "javascript"), ("zip.js", "javascript"),
                               ("styles.css", "text/css"), ("favicon.svg", "image/svg+xml")):
                with self.subTest(asset=name):
                    asset = client.get("/assets/" + name)
                    self.assertEqual(asset.status_code, 200)
                    self.assertIn(kind, asset.headers["content-type"])
            self.assertEqual(client.get("/assets/../server-profile.local.json").status_code, 404)
            self.assertEqual(client.get("/server-profile.local.json").status_code, 404)
            self.assertIn("/api/v1/convert", client.get("/openapi.json").json()["paths"])


@unittest.skipUnless(HAS_MEDIA_TOOLS, "requires FFmpeg and ffprobe")
class HealthTests(ServerTestCase):
    def test_healthz(self):
        with self.client() as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assert_clean()


@unittest.skipUnless(HAS_MEDIA_TOOLS, "requires FFmpeg and ffprobe")
class PlainAudioTests(ServerTestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = tempfile.TemporaryDirectory(prefix="music-server-fixtures-")
        cls.addClassCleanup(fixtures.cleanup)
        cls.tone, cls.song, _ = write_audio(Path(fixtures.name))

    def test_inspect_plain_audio_with_optional_or_misleading_filename(self):
        with self.client() as client:
            for filename, source, media_type, codec in (
                (None, self.song, OCTET_STREAM, "mp3"),
                ("tone.flac", self.tone, "audio/flac", "flac"),
                ("tone.ncm", self.tone, OCTET_STREAM, "flac"),
            ):
                with self.subTest(filename=filename):
                    payload = source.read_bytes()
                    response = self.upload(client, INSPECT, payload, filename=filename, media_type=media_type)
                    info = self.assert_inspection(response, state="ready", adapter=None, wrapped=False,
                                                  can_convert=True, size=len(payload))
                    self.assertEqual(info["codec"].lower(), codec)
                    self.assertEqual(info["container"].lower(), codec)

    def test_convert_default_mp3_and_explicit_bitrate(self):
        with self.client() as client:
            response = self.upload(client, CONVERT, self.song.read_bytes(), filename="tone.mp3", media_type="audio/mpeg")
            self.assert_attachment(response, ".mp3")
            result = self.work / "default.mp3"
            result.write_bytes(response.content)
            self.assertEqual(packet_hash(result), packet_hash(self.song))

            response = self.upload(client, CONVERT, self.tone.read_bytes(), filename="tone.ncm", format="mp3",
                                   bitrate="128k")
            self.assert_attachment(response, ".mp3")
            result = self.work / "transcoded.mp3"
            result.write_bytes(response.content)
            info = probe.diagnose(result)
            self.assertEqual((info.verdict, info.codec, info.bitrate), (probe.VERDICT_OK, "mp3", 128000))

    def test_browser_binary_download_preserves_audio(self):
        with self.client() as client:
            response = client.post(CONVERT, params={"filename": "tone.mp3", "format": "mp3"},
                                   content=self.song.read_bytes(),
                                   headers={"Content-Type": OCTET_STREAM, "Accept": OCTET_STREAM})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], OCTET_STREAM)
            self.assertEqual(response.headers["x-audio-filename"], "tone.mp3")
            self.assertNotIn("content-disposition", response.headers)
            self.assert_clean()
            result = self.work / "browser-download.mp3"
            result.write_bytes(response.content)
            self.assertEqual(packet_hash(result), packet_hash(self.song))

    def test_filename_cannot_escape_job_directory(self):
        payload = self.tone.read_bytes()
        canary = self.work / "outside.flac"
        canary.write_bytes(b"existing file, not an upload")
        with self.client() as client:
            for filename in ("../outside.flac", "..\\outside.flac", str(canary)):
                with self.subTest(filename=filename):
                    with client.stream("POST", CONVERT, params={"filename": filename}, content=payload,
                                       headers={"Content-Type": OCTET_STREAM}) as response:
                        response.read()
                    self.assert_attachment(response, ".mp3")
                    self.assertEqual(canary.read_bytes(), b"existing file, not an upload")
                    self.assertEqual(set(self.work.iterdir()), {self.jobs, canary})

    def test_rejects_invalid_conversion_options(self):
        payload = self.song.read_bytes()
        with self.client() as client:
            for options in ({"format": "not-a-format"}, {"bitrate": "not-a-bitrate"},
                            {"profile": str(self.work / "private-profile.json")}, {"platform": "ncm"}):
                with self.subTest(options=options):
                    response = self.upload(client, CONVERT, payload, filename="tone.mp3", **options)
                    self.assert_error(response, 400, "invalid_options", "private-profile.json")

    def test_upload_limit_with_content_length_and_chunked_body(self):
        with self.client(max_upload_bytes=1024) as client:
            for route in (INSPECT, CONVERT):
                with self.subTest(route=route, body="length"):
                    response = self.upload(client, route, b"a" * 1200, filename="tone.mp3")
                    self.assertEqual(response.request.headers["content-length"], "1200")
                    self.assert_error(response, 413, "upload_too_large")
                with self.subTest(route=route, body="chunked"):
                    # Each chunk is under the cap, but the cumulative size exceeds it.
                    chunks = iter((b"a" * 600, b"b" * 600))
                    response = self.upload(client, route, chunks, filename="tone.mp3")
                    self.assertNotIn("content-length", response.request.headers)
                    self.assertEqual(response.request.headers["transfer-encoding"], "chunked")
                    self.assert_error(response, 413, "upload_too_large")

    def test_output_limit_does_not_return_attachment_or_leave_job(self):
        with self.client(max_output_bytes=1024) as client:
            response = self.upload(client, CONVERT, self.tone.read_bytes(), filename="tone.flac", bitrate="128k")
            self.assert_error(response, 422, "output_too_large")

    def test_processing_deadline_stops_work_and_cleans_up(self):
        from core import convert

        stopped = threading.Event()

        def slow_converter(info, *_args, cancel, **_kwargs):
            if cancel.wait(4):
                stopped.set()
            return convert.ConvertResult(False, info.path, cancelled=True)

        with patch.object(convert, "convert_to_directory", slow_converter), self.client(processing_timeout=0.4) as client:
            response = self.upload(client, CONVERT, self.song.read_bytes(), filename="tone.mp3")
            self.assert_error(response, 504, "processing_timeout")
            self.assertTrue(stopped.is_set(), "worker did not observe cancellation")

    def test_capacity_rejects_second_job_while_first_runs(self):
        from core import convert

        entered, unblock = threading.Event(), threading.Event()

        def slow_converter(info, *_args, **_kwargs):
            entered.set()
            unblock.wait(6)
            return convert.ConvertResult(False, info.path)

        content = self.song.read_bytes()
        with patch.object(convert, "convert_to_directory", slow_converter), self.client(concurrency=1) as client:
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(self.upload, client, CONVERT, content, filename="one.mp3")
                try:
                    self.assertTrue(entered.wait(5))
                    crowded = self.upload(client, INSPECT, content, filename="two.mp3")
                    self.assertEqual(crowded.status_code, 429)
                    self.assertEqual(crowded.json()["error"]["code"], "busy")
                    self.assertIn("retry-after", crowded.headers)
                finally:
                    unblock.set()
                self.assert_error(running.result(timeout=5), 422, "conversion_failed")
            self.assertEqual(self.upload(client, INSPECT, content).json()["state"], "ready")


@unittest.skipUnless(HAS_MEDIA_TOOLS, "requires FFmpeg and ffprobe")
class WrappedAudioTests(ServerTestCase):
    @classmethod
    def setUpClass(cls):
        try:
            platforms._backend()
        except platforms.AdapterError as exc:
            if os.environ.get("REQUIRE_ADAPTERS"):
                raise
            raise unittest.SkipTest(str(exc)) from exc
        fixtures = tempfile.TemporaryDirectory(prefix="music-server-wrappers-")
        cls.addClassCleanup(fixtures.cleanup)
        work = Path(fixtures.name)
        cls.tone, cls.song, cover = write_audio(work)
        cls.media, cls.profile, cls.stag_profile = generate(work / "inputs", cls.tone, cls.song, cover)
        cls.ncm = next(src for src in cls.media if src.suffix == ".NCM")
        cls.stag = next(src for src in cls.media if "STag" in src.name)

    def test_inspect_and_convert_synthetic_wrappers(self):
        adapters = {
            ".ncm": "ncm", ".qmcflac": "qmc1", ".mflac": "qmc2", ".mflac0": "qmc2",
            ".mgg": "qmc2", ".kgm": "kgm", ".vpr": "vpr", ".kwm": "kwm",
        }
        packet_hashes = {"flac": packet_hash(self.tone), "mp3": packet_hash(self.song)}
        with self.client(profile_path=self.profile) as client:
            for i, (source, original) in enumerate(self.media.items()):
                if source == self.stag:
                    continue
                with self.subTest(filename=source.name):
                    payload = source.read_bytes()
                    # Header (NCM) and footer (QTag) identification survive a misleading extension.
                    filename = "renamed.mp3" if source == self.ncm or "QTag" in source.name else source.name
                    response = self.upload(client, INSPECT, payload, filename=filename)
                    self.assert_inspection(response, state="ready", adapter=adapters[source.suffix.lower()],
                                           wrapped=True, can_convert=True, size=len(payload))
                    fmt = "flac" if original.startswith(b"fLaC") else "mp3"
                    response = self.upload(client, CONVERT, payload, filename=filename, format=fmt)
                    self.assert_attachment(response, "." + fmt)
                    result = self.work / f"wrapped-{i}.{fmt}"
                    result.write_bytes(response.content)
                    self.assertEqual(packet_hash(result), packet_hashes[fmt])

    def test_no_profile_reports_configuration_required_without_output(self):
        payload = self.ncm.read_bytes()
        with self.client() as client:
            response = self.upload(client, INSPECT, payload, filename=self.ncm.name)
            self.assert_inspection(response, state="needs_server_config", adapter="ncm", wrapped=True,
                                   can_convert=False, size=len(payload))
            response = self.upload(client, CONVERT, payload, filename=self.ncm.name)
            self.assert_error(response, 422, "needs_server_config")

    def test_stag_requires_a_file_specific_key(self):
        payload = self.stag.read_bytes()
        with self.client(profile_path=self.profile) as client:
            response = self.upload(client, INSPECT, payload, filename=self.stag.name)
            self.assert_inspection(response, state="requires_file_key", adapter="qmc2", wrapped=True,
                                   can_convert=False, size=len(payload))
            response = self.upload(client, CONVERT, payload, filename=self.stag.name)
            self.assert_error(response, 422, "requires_file_key")

    def test_unsupported_wrapper_does_not_produce_output(self):
        source = next(src for src in self.media if src.suffix == ".kgm")
        data = bytearray(source.read_bytes())
        data[20:24] = (5).to_bytes(4, "little")  # Unsupported KGM header version.
        payload = bytes(data)
        with self.client(profile_path=self.profile) as client:
            response = self.upload(client, INSPECT, payload, filename=source.name)
            self.assert_inspection(response, state="unsupported_format", adapter="kgm", wrapped=True,
                                   can_convert=False, size=len(payload))
            response = self.upload(client, CONVERT, payload, filename=source.name)
            self.assert_error(response, 422, "unsupported_format")

    def test_wrong_profile_parameters_do_not_produce_output_or_leak_details(self):
        profile = self.work / "private-wrong-profile.json"
        fields = json.loads(self.profile.read_text(encoding="utf-8"))
        wrong_key = synthetic("server-wrong-ncm-key", 16).hex()
        fields["ncm"]["core_key"] = wrong_key
        profile.write_text(json.dumps(fields), encoding="utf-8")
        with self.client(profile_path=profile) as client:
            response = self.upload(client, CONVERT, self.ncm.read_bytes(), filename=self.ncm.name)
            self.assert_error(response, 422, "conversion_failed", profile.name, wrong_key)

    def test_shared_profile_with_per_file_master_key_is_refused_at_startup(self):
        key = json.loads(self.stag_profile.read_text(encoding="utf-8"))["qmc2"]["master_key"]
        client = self.client(profile_path=self.stag_profile)
        with self.assertRaises((ValueError, RuntimeError)) as rejected:
            with client:
                self.fail("lifespan accepted a per-file key as shared server configuration")
        self.assertNotIn(key.casefold(), str(rejected.exception).casefold())
        self.assert_clean()

    def test_profile_is_loaded_once_in_lifespan(self):
        profile = self.work / "private-startup-profile.json"
        profile.write_bytes(self.profile.read_bytes())
        payload = self.ncm.read_bytes()
        with self.client(profile_path=profile) as client:
            response = self.upload(client, INSPECT, payload, filename=self.ncm.name)
            self.assert_inspection(response, state="ready", adapter="ncm", wrapped=True,
                                   can_convert=True, size=len(payload))
            profile.write_text("{}", encoding="utf-8")
            response = self.upload(client, CONVERT, payload, filename=self.ncm.name, format="flac")
            self.assert_attachment(response, ".flac")
            result = self.work / "cached-profile.flac"
            result.write_bytes(response.content)
            self.assertEqual(packet_hash(result), packet_hash(self.tone))
        with self.client(profile_path=profile) as client:
            response = self.upload(client, CONVERT, payload, filename=self.ncm.name)
            self.assert_error(response, 422, "needs_server_config")


if __name__ == "__main__":
    unittest.main()
