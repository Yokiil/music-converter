"""生成测试用音调、封装和参数；没有真实歌曲或平台参数。"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import warnings
from pathlib import Path

from core import locate, platforms
from core.process import NO_WINDOW


def ffmpeg(*args: str) -> bytes:
    exe = locate.find_ffmpeg()
    if not exe:
        raise RuntimeError("需要 FFmpeg，见 README。")
    return subprocess.run([exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-n", *map(str, args)],
                          check=True, capture_output=True, timeout=45, creationflags=NO_WINDOW).stdout


def synthetic(name: str, size: int) -> bytes:
    return hashlib.shake_256(f"music-converter-demo/{name}".encode()).digest(size)


def write_audio(directory: Path):
    tone = directory / "合成音调.flac"
    song = directory / "合成音调.mp3"
    cover = directory / "合成封面.png"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:a", "flac", tone)
    ffmpeg("-i", tone, "-c:a", "libmp3lame", "-b:a", "128k", "-metadata", "title=测试音调", song)
    ffmpeg("-f", "lavfi", "-i", "color=c=0x225cce:s=32x32", "-frames:v", "1", "-threads", "1", cover)
    return tone, song, cover


def generate(directory: Path, tone: Path, song: Path, cover: Path):
    """写到独立空目录；每种封装记录用于逐字节比对的原始载荷。"""
    directory.mkdir(exist_ok=False)
    lib = platforms._backend()
    from libtakiyasha.qmc import QMCv2QTag, QMCv2STag

    flac, mp3 = tone.read_bytes(), song.read_bytes()
    sample_profile = {
        "ncm": {"core_key": synthetic("ncm.core", 16), "tag_key": synthetic("ncm.tag", 16)},
        "qmc1": {"mask": synthetic("qmc.mask", 128)},
        "qmc2": {"core_key": synthetic("qmc.core", 8), "garble_keys": (synthetic("qmc.garble", 16),)},
        "kgm": {"table1": synthetic("kgm.table1", 272), "table2": synthetic("kgm.table2", 272),
                "tablev2": synthetic("kgm.tablev2", 272), "vpr_key": synthetic("vpr.extra", 17)},
        "kwm": {"core_key": synthetic("kwm.core", 32)},
    }
    media = {}
    for name, payload in (("网易云合成 FLAC.NCM", flac), ("网易云合成 MP3.ncm", mp3)):
        output = directory / name
        with lib.NCM.new() as encoder:
            encoder.write(payload)
            encoder.ncm_tag.musicName = "测试音调"
            encoder.ncm_tag.artist = [["合成歌手", 0]]
            encoder.ncm_tag.album = "测试专辑"
            encoder.cover_data = cover.read_bytes()
            encoder.save(filething=output, **sample_profile["ncm"])
        media[output] = payload

    output = directory / "QQ 合成 v1.qmcflac"
    with lib.QMCv1.new(**sample_profile["qmc1"]) as encoder:
        encoder.write(flac)
        encoder.save(output)
    media[output] = flac

    for name, tail, method in (("QQ 合成长度尾.mflac", "length", "map"),
                                ("QQ 合成 QTag.mflac0", "qtag", "rc4"),
                                ("QQ 合成 EncV2.mgg", "encv2", "map"),
                                ("QQ 合成 STag.mflac", "stag", "map")):
        output = directory / name
        with lib.QMCv2.new(method) as encoder:
            encoder.write(flac)
            opts = {"core_key": sample_profile["qmc2"]["core_key"]}
            if tail == "qtag":
                encoder.extra_info = QMCv2QTag()
            elif tail == "stag":
                encoder.extra_info = QMCv2STag()
                opts = {}
                sample_profile["qmc2"]["master_key"] = encoder.master_key
            elif tail == "encv2":
                opts["garble_keys"] = sample_profile["qmc2"]["garble_keys"]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", lib.LibTakiyashaWarning)
                encoder.save(filething=output, with_extra_info=tail in {"qtag", "stag"}, **opts)
        media[output] = flac

    for ext, magic in (("kgm", platforms.KGM_MAGIC), ("vpr", platforms.VPR_MAGIC)):
        output = directory / f"酷狗合成 v3.{ext}"
        opts = dict(sample_profile["kgm"])
        if ext == "kgm":
            opts.pop("vpr_key")
        with lib.KGMorVPR.new(**opts) as encoder:
            encoder.write(flac)
            header = magic + struct.pack("<III", 60, 3, 0) + encoder.master_key[:16] + bytes(16)
            output.write_bytes(header + encoder.getvalue(nocryptlayer=True))
        media[output] = flac

    output = directory / "酷我合成.kwm"
    with lib.KWM.new(**sample_profile["kwm"]) as encoder:
        encoder.write(mp3)
        encoder.save(output)
    media[output] = mp3

    # STag 的 master_key 只对应一个文件，不能用来覆盖其他文件的内嵌密钥。
    stag_key = sample_profile["qmc2"].pop("master_key")
    profile = directory / "demo-profile.local.json"
    stag_profile = directory / "stag-profile.local.json"
    raw = {name: {k: [b.hex() for b in v] if isinstance(v, tuple) else v.hex() for k, v in fields.items()}
           for name, fields in sample_profile.items()}
    profile.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    stag_profile.write_text(json.dumps({"qmc2": {"master_key": stag_key.hex()}}, indent=2), encoding="utf-8")
    return media, profile, stag_profile


def main():
    directory = Path(tempfile.mkdtemp(prefix=".demo-", dir=Path.cwd()))
    tone, song, cover = write_audio(directory)
    data, profile, stag = generate(directory / "样本", tone, song, cover)
    print(f"生成 {len(data)} 个合成封装：{directory / '样本'}")
    print(f"常用参数：{profile}\nSTag 单曲参数：{stag}")


if __name__ == "__main__":
    main()
