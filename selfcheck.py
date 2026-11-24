"""离线自检：不装 AstrBot、不连网络，只验 voicekit 里的纯逻辑。

    py -3 selfcheck.py
"""

from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voicekit import (  # noqa: E402
    AudioError,
    RateLimiter,
    SessionState,
    TTSError,
    TTSClient,
    describe,
    fmt_size,
    normalize_base,
    sniff_ext,
    validate,
    wav_duration,
)

OK = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [X ] {name} {detail}")


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000, channels: int = 1, bits: int = 16) -> None:
    block = channels * (bits // 8)
    data_len = int(rate * seconds) * block
    header = b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, bits)
    header += b"data" + struct.pack("<I", data_len)
    path.write_bytes(header + b"\x00" * data_len)


def main() -> int:
    print("[服务地址]")
    check("归一化 /tts 结尾", normalize_base("http://127.0.0.1:9880/tts") == "http://127.0.0.1:9880")
    check("归一化多余斜杠", normalize_base("http://h:1/tts/") == "http://h:1")
    check("空地址", normalize_base("") == "")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        print("[音频识别]")
        wav = root / "a.wav"
        make_wav(wav, seconds=1.0)
        check("wav 识别", sniff_ext(wav) == ".wav")
        dur = wav_duration(wav)
        check("wav 时长≈1 秒", dur is not None and abs(dur - 1.0) < 0.05, f"得到 {dur}")

        mp3 = root / "b.mp3"
        mp3.write_bytes(b"ID3\x03\x00\x00\x00" + b"\x00" * 2000)
        check("mp3 按文件头识别", sniff_ext(mp3) == ".mp3")

        silk = root / "c.silk"
        silk.write_bytes(b"\x02#!SILK_V3" + b"\xff" * 500)
        check("silk 按文件头识别", sniff_ext(silk) == ".silk")

        weird = root / "d.bin"
        weird.write_bytes(b"\x00\x01\x02\x03")
        check("未知格式返回空", sniff_ext(weird) is None)

        size, ext = validate(wav, 0)
        check("validate 通过", size == wav.stat().st_size and ext == ".wav")
        try:
            validate(weird, 0)
            check("validate 拦未知格式", False)
        except AudioError:
            check("validate 拦未知格式", True)
        try:
            validate(wav, 0.000001)
            check("validate 拦超大文件", False)
        except AudioError:
            check("validate 拦超大文件", True)
        try:
            validate(root / "不存在.wav", 0)
            check("validate 拦不存在", False)
        except AudioError:
            check("validate 拦不存在", True)

        check("fmt_size KB", fmt_size(2048) == "2.0 KB", fmt_size(2048))
        check("describe 带时长", "1.0 秒" in describe(wav), describe(wav))

        print("[会话状态]")
        state = SessionState(root / "sub" / "state.json")
        check("初始为空", state.get_voice("umo:g:1") == "")
        state.set_voice("umo:g:1", "xilian")
        check("写入后能读", state.get_voice("umo:g:1") == "xilian")
        again = SessionState(root / "sub" / "state.json")
        check("持久化到磁盘", again.get_voice("umo:g:1") == "xilian")
        again.set_voice("umo:g:1", "")
        check("清空音色", again.get_voice("umo:g:1") == "")
        bad = root / "sub" / "state.json"
        bad.write_text("{坏掉的 json", encoding="utf-8")
        check("坏文件不炸", SessionState(bad).get_voice("x") == "")

        print("[限流]")
        limiter = RateLimiter(2, window=60)
        check("前两次放行", limiter.allow("k") and limiter.allow("k"))
        check("第三次拦下", not limiter.allow("k"))
        check("其它 key 不受影响", limiter.allow("k2"))
        check("剩余次数为 0", limiter.remaining("k") == 0)
        unlimited = RateLimiter(0)
        check("limit=0 不限制", all(unlimited.allow("k") for _ in range(50)))

        print("[TTS 客户端]")
        client = TTSClient(root / "cache", timeout=5.0)
        check("初始未配置", not client.configured)
        client.set_base("http://127.0.0.1:9880/tts")
        check("设置地址后已配置", client.configured and client.base == "http://127.0.0.1:9880")

        c1 = client._cache_path("你好", "a")
        c2 = client._cache_path("你好", "b")
        c3 = client._cache_path("你好", "a")
        check("缓存键按音色区分", c1 != c2 and c1 == c3)

        small = client._cache_path("x", None)
        small.parent.mkdir(parents=True, exist_ok=True)
        small.write_bytes(b"\x00" * 100)
        check("太小不算有效缓存", not client._cache_ok(small))
        small.write_bytes(b"\x00" * 5000)
        check("够大算有效缓存", client._cache_ok(small))

        for _ in range(3):
            client._mark_down("connection refused")
        check("连续失败进熔断", client._down_until > 0)
        check("熔断原因可读", "connection refused" in client._down_reason)
        check("熔断后描述带提示", "熔断" in client.describe())

        client._mark_up()
        check("恢复后清空熔断", client._down_until == 0)

        check("TTSError 可携带中文", TTSError("合成失败").args[0] == "合成失败")

        fake = root / "cache2"
        client2 = TTSClient(fake)
        check("缓存目录未创建也不炸", client2.cleanup_cache() is None)

    print(f"\n结果：{OK} 通过，{FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
