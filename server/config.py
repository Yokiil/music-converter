"""Trusted server settings. Only the operator supplies a local adapter profile."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core import platforms

MIB = 1024 ** 2


@dataclass(frozen=True)
class ServerConfig:
    profile_path: Path | None = field(default=None, repr=False)
    temp_dir: Path | None = field(default=None, repr=False)
    max_upload_bytes: int = 64 * MIB
    max_output_bytes: int = 128 * MIB
    concurrency: int = 2
    upload_timeout: float = 60
    processing_timeout: float = 120
    download_timeout: float = 90

    @classmethod
    def from_env(cls) -> ServerConfig:
        def number(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError:
                raise ValueError(f"{name} 必须是整数。") from None
            if not low <= value <= high:
                raise ValueError(f"{name} 必须在 {low} 到 {high} 之间。")
            return value

        def path_var(name: str) -> Path | None:
            value = os.environ.get(name, "").strip()
            return Path(value).expanduser() if value else None

        default_profile = Path(__file__).resolve().parents[1] / "server-profile.local.json"
        profile_path = path_var("MUSIC_SERVER_PROFILE")
        if profile_path is None and default_profile.is_file():
            profile_path = default_profile
        return cls(
            profile_path=profile_path,
            temp_dir=path_var("MUSIC_TEMP_DIR"),
            max_upload_bytes=number("MUSIC_MAX_UPLOAD_MIB", 64, 1, 128) * MIB,
            max_output_bytes=number("MUSIC_MAX_OUTPUT_MIB", 128, 1, 256) * MIB,
            concurrency=number("MUSIC_CONCURRENCY", 2, 1, 4),
            upload_timeout=number("MUSIC_UPLOAD_TIMEOUT", 60, 5, 300),
            processing_timeout=number("MUSIC_PROCESSING_TIMEOUT", 120, 5, 600),
            download_timeout=number("MUSIC_DOWNLOAD_TIMEOUT", 90, 5, 300),
        )

    def load_profile(self) -> platforms.Profile:
        if self.temp_dir is not None and not self.temp_dir.is_dir():
            raise ValueError("MUSIC_TEMP_DIR 必须指向现有的工作目录。")
        if self.profile_path is None:
            return platforms.EMPTY_PROFILE
        try:
            profile = platforms.load_profile(self.profile_path)
        except OSError:
            raise ValueError("无法打开 MUSIC_SERVER_PROFILE 指定的本机文件。") from None
        if "master_key" in profile.get("qmc2", {}):
            raise ValueError("服务端共享配置不接受 qmc2.master_key；该字段只适用于对应的单曲。")
        return profile
