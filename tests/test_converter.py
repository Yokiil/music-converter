from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from core import convert, locate, platforms, probe
from core.process import run_ffmpeg
from tests.fixtures import ffmpeg, generate, write_audio


def packet_hash(path: Path) -> bytes:
    return ffmpeg("-i", path, "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-")


class ProcessTests(unittest.TestCase):
    def test_stop_silent_process(self):
        with tempfile.TemporaryDirectory() as directory:
            cancel = threading.Event()
            timer = threading.Timer(0.25, cancel.set)
            timer.start()
            try:
                start = time.monotonic()
                result = run_ffmpeg([sys.executable, "-c", "import time; time.sleep(30)"], Path(directory),
                                    timeout=15, cancel=cancel)
                self.assertTrue(result.cancelled)
                self.assertLess(time.monotonic() - start, 4)
            finally:
                timer.cancel()
                timer.join()

    def test_profile_rejects_unknown_fields_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-profile.json"
            path.write_text('{"ncm":{"core_key":"SECRET"}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ncm.core_key") as ctx:
                platforms.load_profile(path)
            self.assertNotIn("SECRET", str(ctx.exception))
            path.write_text('{"ncm":{"command":"ffmpeg"}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                platforms.load_profile(path)


@unittest.skipUnless(locate.ffmpeg_available() and locate.probe_available(), "需要 FFmpeg / ffprobe")
class PlainAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        location = tempfile.TemporaryDirectory()
        cls.addClassCleanup(location.cleanup)
        cls.work = Path(location.name)
        cls.tone, cls.song, cls.cover = write_audio(cls.work)

    def test_misleading_suffix(self):
        wrong = self.work / "其实是 FLAC.ncm"
        shutil.copyfile(self.tone, wrong)
        info = probe.diagnose(wrong)
        self.assertEqual((info.verdict, info.codec, info.wrapped), (probe.VERDICT_OK, "flac", False))

    def test_copy_or_explicit_bitrate(self):
        with tempfile.TemporaryDirectory() as directory:
            info = probe.diagnose(self.song)
            keep = convert.convert_to_directory(info, Path(directory), "mp3")
            changed = convert.convert_to_directory(info, Path(directory), "mp3", "192k")
            self.assertTrue(keep.ok, keep.message)
            self.assertTrue(changed.ok, changed.message)
            self.assertTrue(keep.copied)
            self.assertFalse(changed.copied)
            self.assertEqual(packet_hash(self.song), packet_hash(keep.dst))
            self.assertEqual(probe.diagnose(changed.dst).bitrate, 192000)

    def test_concurrent_publish_and_same_directory(self):
        directory = self.work / "冲突测试"
        directory.mkdir()
        src = directory / "同名.flac"
        shutil.copyfile(self.tone, src)
        original = src.read_bytes()
        blocked = directory / "同名 (1).flac"
        blocked.write_bytes(b"Existing file")
        info = probe.diagnose(src)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(lambda _: convert.convert_to_directory(info, directory, "source"), range(2)))
        self.assertTrue(all(r.ok for r in outputs), [r.message for r in outputs])
        self.assertEqual({r.dst.name for r in outputs}, {"同名 (2).flac", "同名 (3).flac"})
        self.assertEqual(src.read_bytes(), original)
        self.assertEqual(blocked.read_bytes(), b"Existing file")
        self.assertFalse(list(directory.glob(".music-*")))

    def test_ffprobe_does_not_follow_playlist(self):
        fake = self.work / "playlist.mp3"
        fake.write_text(f"ffconcat version 1.0\nfile '{self.tone.as_posix()}'\n", encoding="utf-8")
        self.assertFalse(probe.is_convertible(probe.diagnose(fake)))

    def test_invalid_output_directory(self):
        result = convert.convert_to_directory(probe.diagnose(self.tone), self.tone, "mp3")
        self.assertFalse(result.ok)
        self.assertIsNone(result.dst)

    def test_other_formats_and_cover(self):
        directory = self.work / "formats"
        with_cover = self.work / "cover-source.mp3"
        ffmpeg("-i", self.song, "-i", self.cover, "-map", "0:a", "-map", "1:v", "-c", "copy",
               "-disposition:v:0", "attached_pic", with_cover)
        for dest, codec in (("m4a", "aac"), ("wav", "pcm_s16le"), ("ogg", "vorbis"), ("flac", "flac")):
            with self.subTest(dest=dest):
                result = convert.convert_to_directory(probe.diagnose(with_cover), directory, dest)
                self.assertTrue(result.ok, result.message)
                info = probe.diagnose(result.dst)
                self.assertEqual(info.codec, codec)
                self.assertEqual(info.has_cover, dest in {"m4a", "flac"})


@unittest.skipUnless(locate.ffmpeg_available() and locate.probe_available(), "需要 FFmpeg / ffprobe")
class PlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            platforms._backend()
        except platforms.AdapterError as exc:
            if os.environ.get("REQUIRE_ADAPTERS"):
                raise
            raise unittest.SkipTest(str(exc))
        location = tempfile.TemporaryDirectory()
        cls.addClassCleanup(location.cleanup)
        cls.work = Path(location.name)
        cls.tone, cls.song, cls.cover = write_audio(cls.work)
        cls.data, profile, stag = generate(cls.work / "输入", cls.tone, cls.song, cls.cover)
        cls.profile = platforms.load_profile(profile)
        cls.stag = platforms.load_profile(stag)

    def _profile_for(self, path):
        return self.stag if "STag" in path.name else self.profile

    def test_extract_all_verified_variants(self):
        for src, original in self.data.items():
            with self.subTest(variant=src.name), tempfile.TemporaryDirectory() as directory:
                profile = self._profile_for(src)
                state = probe.diagnose(src)
                self.assertEqual(state.verdict, probe.VERDICT_PROFILE, state.note)
                ready = probe.diagnose(src, profile)
                self.assertEqual(ready.verdict, probe.VERDICT_WRAPPED, ready.note)
                with patch.object(platforms, "CHUNK", 8197):
                    extracted = platforms.extract(src, Path(directory), profile)
                self.assertEqual(extracted.path.read_bytes(), original)

    def test_original_output_for_all_variants(self):
        for src, original in self.data.items():
            with self.subTest(variant=src.name), tempfile.TemporaryDirectory() as directory:
                profile = self._profile_for(src)
                info = probe.diagnose(src, profile)
                result = convert.convert_to_directory(info, Path(directory), "source", profile=profile)
                self.assertTrue(result.ok, result.message)
                self.assertTrue(result.copied)
                codec = "flac" if original.startswith(b"fLaC") else "mp3"
                out = probe.diagnose(result.dst)
                self.assertEqual(out.codec, codec)
                self.assertEqual(packet_hash(result.dst), packet_hash(self.tone if codec == "flac" else self.song))
                if src.suffix.lower() == ".ncm":
                    self.assertEqual(out.tags["artist"], "合成歌手")
                    self.assertEqual(out.tags["album"], "测试专辑")
                    self.assertTrue(out.has_cover)
                self.assertFalse(list(Path(directory).glob(".music-*")))

    def test_mp3_from_wrapped_flac(self):
        src = next(p for p in self.data if p.suffix == ".NCM")
        with tempfile.TemporaryDirectory() as directory:
            result = convert.convert_to_directory(probe.diagnose(src, self.profile), Path(directory), "mp3", "128k",
                                                  profile=self.profile)
            self.assertTrue(result.ok, result.message)
            info = probe.diagnose(result.dst)
            self.assertEqual((info.codec, info.bitrate), ("mp3", 128000))
            self.assertTrue(info.has_cover)

    def test_wrong_params_are_not_published(self):
        ncm = next(p for p in self.data if p.suffix == ".NCM")
        qmc = next(p for p in self.data if p.suffix == ".qmcflac")
        for src, profile in ((ncm, {"ncm": {"core_key": bytes(16)}}), (qmc, {"qmc1": {"mask": bytes(128)}})):
            with self.subTest(src=src.name), tempfile.TemporaryDirectory() as directory:
                result = convert.convert_to_directory(probe.diagnose(src, profile), Path(directory), "source", profile=profile)
                self.assertFalse(result.ok)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unsupported_and_malformed(self):
        kgm = next(p for p in self.data if p.suffix == ".kgm")
        future = self.work / "新版.kgm"
        data = bytearray(kgm.read_bytes())
        data[20:24] = (5).to_bytes(4, "little")
        future.write_bytes(data)
        self.assertEqual(probe.diagnose(future, self.profile).verdict, probe.VERDICT_UNSUPPORTED)
        bad = self.work / "坏头.ncm"
        bad.write_bytes(platforms.NCM_MAGIC + bytes(2) + b"\xff" * 4)
        self.assertEqual(probe.diagnose(bad, self.profile).verdict, probe.VERDICT_BROKEN)
        no_footer = self.work / "无尾.mflac"
        no_footer.write_bytes(b"\x99" * 10000)
        self.assertEqual(probe.diagnose(no_footer, self.profile).verdict, probe.VERDICT_UNSUPPORTED)

    def test_cancel_mid_extraction(self):
        src = next(p for p in self.data if p.suffix == ".NCM")
        e = threading.Event()
        with tempfile.TemporaryDirectory() as directory, patch.object(platforms, "CHUNK", 2048):
            result = convert.convert_to_directory(probe.diagnose(src, self.profile), Path(directory), "mp3",
                                                  on_progress=lambda _: e.set(), profile=self.profile, cancel=e)
            self.assertTrue(result.cancelled)
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless(os.environ.get("TEST_GUI"), "设置 TEST_GUI=1 在图形桌面跑 Tk 冒烟测试")
    def test_windows_gui(self):
        import app
        with contextlib.ExitStack() as stack:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(patch.object(app.messagebox, "askokcancel", return_value=True))
            window = app.MusicConverter()
            stack.callback(window._close)
            unknown = self.work / "未知.mgg"
            unknown.write_bytes(b"\x98" * 2345)
            paths = [p for p in self.data if "STag" not in p.name] + [unknown]
            window._add_paths(paths + paths)
            window._set_profile(self.profile)
            end = time.monotonic() + 12
            while len(window.rows) != len(window.infos) and time.monotonic() < end:
                window.update()
                time.sleep(0.02)
            self.assertEqual(len(window.rows), len(paths))
            self.assertEqual(len(window.infos), len(paths))
            window.out_var.set(str(directory))
            window.start_btn.invoke()
            self.assertTrue(window.busy)
            end = time.monotonic() + 40
            while window.busy and time.monotonic() < end:
                window.update()
                time.sleep(0.03)
            self.assertFalse(window.busy)
            self.assertIn("成功 9/9", window.status.cget("text"))
            self.assertIn("跳过 1 个", window.log.get("1.0", "end"))
            self.assertEqual(len(list(directory.iterdir())), 9)
            self.assertEqual(float(window.progress["value"]), 100)


if __name__ == "__main__":
    unittest.main()
