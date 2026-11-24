"""音频文件的轻量工具：不依赖 ffmpeg，只做识别、校验和能算的时长。"""

from __future__ import annotations

import struct
from pathlib import Path

# QQ 语音常见容器格式；silk 是 QQ 自己的编码
KNOWN_EXTS = (".wav", ".mp3", ".silk", ".amr", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".pcm")

MAGIC = (
    (b"RIFF", ".wav"),
    (b"ID3", ".mp3"),
    (b"\xff\xfb", ".mp3"),
    (b"\xff\xf3", ".mp3"),
    (b"\xff\xf2", ".mp3"),
    (b"OggS", ".ogg"),
    (b"#!AMR", ".amr"),
    (b"fLaC", ".flac"),
    (b"\x02#!SILK", ".silk"),
    (b"#!SILK", ".silk"),
    (b"FORM", ".wav"),
)


class AudioError(Exception):
    """音频文件不合规，消息可以直接给用户看。"""


def sniff_ext(path: str | Path) -> str | None:
    """按文件头猜真实格式，猜不出就退回扩展名。"""
    p = Path(path)
    try:
        with p.open("rb") as f:
            head = f.read(12)
    except OSError:
        return None
    for magic, ext in MAGIC:
        if head.startswith(magic):
            return ext
    suffix = p.suffix.lower()
    return suffix if suffix in KNOWN_EXTS else None


def wav_duration(path: str | Path) -> float | None:
    """解析 wav 头算时长；不是标准 PCM wav 就返回 None。"""
    p = Path(path)
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            head = f.read(44)
        if len(head) < 44 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return None
        fmt_tag = struct.unpack("<H", head[20:22])[0]
        channels = struct.unpack("<H", head[22:24])[0]
        rate = struct.unpack("<I", head[24:28])[0]
        bits = struct.unpack("<H", head[34:36])[0]
        if fmt_tag != 1 or not channels or not rate or not bits:
            return None
        byte_rate = rate * channels * (bits // 8)
        if byte_rate <= 0:
            return None
        # 跳过可能存在的附加块，直接按总字节数估
        data_bytes = max(size - 44, 0)
        return data_bytes / byte_rate
    except (OSError, struct.error, ZeroDivisionError):
        return None


def validate(path: str | Path, max_mb: float = 0.0) -> tuple[int, str]:
    """检查一个音频文件能不能发，返回 (字节数, 格式扩展名)。"""
    p = Path(path)
    if not p.is_file():
        raise AudioError(f"文件不存在：{p.name}")
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise AudioError(f"读不到文件：{exc}") from exc
    if size <= 0:
        raise AudioError("文件是空的")
    if max_mb and size > max_mb * 1024 * 1024:
        raise AudioError(f"{p.name} 有 {size / 1024 / 1024:.1f} MB，超过上限 {max_mb:g} MB")
    ext = sniff_ext(p)
    if not ext:
        raise AudioError(
            f"认不出 {p.name} 的格式，支持的扩展名：{'、'.join(KNOWN_EXTS)}"
        )
    return size, ext


def fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def describe(path: str | Path) -> str:
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return p.name
    ext = sniff_ext(p) or p.suffix or "?"
    parts = [p.name, fmt_size(size), ext.lstrip(".")]
    duration = wav_duration(p)
    if duration:
        parts.append(f"{duration:.1f} 秒")
    return "，".join(parts)
