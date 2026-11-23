"""Optional real-browser checks against the default local API.

Requires Playwright and a local Chromium/Edge browser. Run from the project root:
  python -m tests.browser_smoke
Five generated wrappers use public default parameters; no profile upload is used.
Screenshots and downloads from this check never enter the source repository.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

from playwright.async_api import async_playwright, expect

from core import probe

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("MUSIC_TEST_URL", "http://127.0.0.1:8787")


async def _run_browser(directory: Path):
    if urllib.parse.urlparse(BASE).hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Browser smoke tests must target a local server.")
    from core import platforms
    from tests.fixtures import write_audio
    from tests.test_ncm_defaults import ncmdump_fixture
    from tests.test_platform_defaults import QMC_MASK, QMC_CORE, QMC_GARBLE, independent_kwm
    from tests.test_vendor_readers import fixture as kgm_fixture

    lib = platforms._backend()
    tone, _, cover = write_audio(directory)
    audio = tone.read_bytes()
    candidates = [directory / name for name in ("网易云合成 FLAC.NCM", "QQ 合成 v1.qmcflac",
                                               "酷狗合成 v3.kgm", "酷我合成.kwm", "QQ 合成 v2.mflac0")]
    candidates[0].write_bytes(ncmdump_fixture(audio, cover.read_bytes(), "flac"))
    with lib.QMCv1.new(mask=QMC_MASK) as encoder:
        encoder.write(audio)
        encoder.save(candidates[1])
    candidates[2].write_bytes(kgm_fixture(audio, kind=3))
    candidates[3].write_bytes(independent_kwm(audio, bytes.fromhex("7856341240302010"), 32, "flac"))
    with lib.QMCv2.new("rc4") as encoder:
        encoder.write(audio)
        encoder.save(core_key=QMC_CORE, garble_keys=QMC_GARBLE, filething=candidates[4])
    total = len(candidates)
    errors = []
    shots = ROOT / "tests"
    async with async_playwright() as playwright:
        edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        executable = os.environ.get("MUSIC_TEST_BROWSER") or (str(edge) if edge.is_file() else None)
        browser = await playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1, accept_downloads=True)
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(BASE, wait_until="domcontentloaded")
            await page.locator("#service-status.connected").wait_for()
            assert await page.title() == "声转 · 音乐格式转换"
            assert await page.locator("#convert").is_disabled()
            await page.screenshot(path=str(shots / "web-empty.png"), full_page=True)
            await page.get_by_role("button", name="支持的格式").click()
            assert await page.locator("#help-dialog").is_visible()
            await page.get_by_role("button", name="知道了").click()
            await page.locator("#file-input").set_input_files([str(p) for p in candidates])
            await expect(page.locator("#ready-count")).to_have_text(str(total), timeout=20000)
            assert await page.locator(".file-row").count() == total
            # Duplicate input does not duplicate the pending queue.
            await page.locator("#file-input").set_input_files([str(candidates[0])])
            assert await page.locator(".file-row").count() == total
            assert await page.locator(".recent-row").count() == total
            assert await page.locator("#total-size").inner_text() != "0 B"
            await page.locator("#file-search").fill("酷狗")
            await expect(page.locator(".file-row:visible")).to_have_count(1)
            await page.locator("#file-search").fill("不存在的音乐")
            await expect(page.locator("#search-empty")).to_be_visible()
            await page.locator("#file-search").fill("")
            await expect(page.locator(".file-row:visible")).to_have_count(total)
            await page.locator('.nav-item[href="#settings-section"]').click()
            await expect(page.locator('.nav-item[href="#settings-section"]')).to_have_class("nav-item active")
            await page.screenshot(path=str(shots / "web-ready.png"), full_page=True)
            await page.locator("#convert").click()
            await expect(page.locator(".download-link")).to_have_count(total, timeout=30000)
            assert await page.locator("#batch-status").inner_text() == "全部转换完成"
            assert await page.locator("#batch-count").inner_text() == f"{total} / {total}"
            # Simulate a BFCache pagehide: completed download URLs must survive.
            await page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))")
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                async with page.expect_download() as event:
                    await page.locator(".download-link").first.click()
                download = await event.value
                assert download.suggested_filename.endswith(".mp3")
                path = output / download.suggested_filename
                await download.save_as(path)
                info = probe.diagnose(path)
                assert info.codec == "mp3" and info.has_cover, info
                async with page.expect_download() as event:
                    await page.locator("#download-all").click()
                archive = await event.value
                zpath = output / archive.suggested_filename
                await archive.save_as(zpath)
                with zipfile.ZipFile(zpath) as z:
                    assert z.testzip() is None
                    assert len(z.namelist()) == total
                    assert all(n.endswith(".mp3") for n in z.namelist())
                    assert "网易云合成 FLAC.mp3" in z.namelist()
            await page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
            await page.screenshot(path=str(shots / "web-results.png"), full_page=True)
            # File names are rendered as text, never injected as HTML.
            await page.locator("#clear-completed").click()
            await expect(page.locator(".file-row")).to_have_count(0)
            await expect(page.locator("#recent-empty")).to_be_visible()
            await page.locator("#file-input").set_input_files({"name": "<img src=x onerror=alert(1)>.mgg", "mimeType": "application/octet-stream", "buffer": b"bad audio"})
            await expect(page.locator(".file-status")).to_have_class("file-status error")
            assert not await page.locator("#file-list img").count()
            assert "<img" in await page.locator(".file-title").inner_text()
            assert await page.locator("#convert").is_disabled()
            await page.locator("#clear-files").click()
            # Actual drop handling, followed by stop/retry with a delayed network response.
            raw = base64.b64encode(tone.read_bytes()).decode()
            await page.evaluate("""(data) => {
                const bytes = Uint8Array.from(atob(data), ch => ch.charCodeAt(0));
                const transfer = new DataTransfer();
                transfer.items.add(new File([bytes], '拖入测试.flac', { type: 'audio/flac' }));
                document.querySelector('#dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
            }""", raw)
            await expect(page.locator("#ready-count")).to_have_text("1")
            async def delayed(route):
                await asyncio.sleep(1)
                try:
                    await route.continue_()
                except Exception:
                    pass
            await page.route("**/api/v1/convert?**", delayed)
            async with page.expect_request("**/api/v1/convert?**"):
                await page.locator("#convert").click()
            await page.locator("#stop").click()
            await expect(page.locator("#convert")).to_be_visible()
            assert await page.locator("#batch-status").inner_text() == "已停止"
            assert await page.locator("#convert").is_enabled()
            await page.unroute("**/api/v1/convert?**", delayed)
            await page.locator("#convert").click()
            await page.locator(".download-link").wait_for(timeout=20000)
            print("Desktop: automatic detection, real MP3/ZIP downloads, safe names, drag/drop, stop and retry passed")

            mobile = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=1)
            mobile.on("pageerror", lambda exc: errors.append(str(exc)))
            await mobile.goto(BASE, wait_until="domcontentloaded")
            await mobile.locator("#service-status.connected").wait_for()
            assert await mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile overflow"
            for width in (320, 768, 1024):
                await mobile.set_viewport_size({"width": width, "height": 844})
                assert await mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"overflow at {width}px"
            await mobile.set_viewport_size({"width": 390, "height": 844})
            await mobile.locator("#formats-help-top").click()
            await expect(mobile.locator("#help-dialog")).to_be_visible()
            await mobile.locator("#help-done").click()
            await mobile.locator("#file-input").set_input_files([str(candidates[0])])
            await expect(mobile.locator("#ready-count")).to_have_text("1")
            await mobile.screenshot(path=str(shots / "web-mobile.png"), full_page=True)
            # Simulated outage produces a recoverable UI, not a permanently enabled converter.
            await mobile.route("**/healthz", lambda route: route.abort())
            await mobile.reload(wait_until="domcontentloaded")
            await mobile.locator("#connection-banner").wait_for()
            assert await mobile.locator("#convert").is_disabled()
            await mobile.unroute("**/healthz")
            await mobile.locator("#reconnect").click()
            await mobile.locator("#service-status.connected").wait_for()
            print("Mobile: 390px layout, file input, offline state and reconnect passed")
            assert not errors, errors
            print("No JavaScript page errors. Screenshots: tests/web-{empty,ready,results,mobile}.png")
        finally:
            await browser.close()


async def main():
    with tempfile.TemporaryDirectory(prefix="music-browser-defaults-") as directory:
        await _run_browser(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
