"""tts-rvc-service 的客户端封装。

配套服务是 TTS-RVC 语音回复插件用的那一个（默认 http://127.0.0.1:9880）：
    GET  /health  -> JSON，服务状态
    GET  /voices  -> JSON 数组，可用音色
    POST /tts     -> 请求体 {"text": ..., "voice_id": ...}，返回 wav 二进制

这里额外做了三件小事：合成结果按 (音色, 文本) 缓存、连续失败自动熔断、
服务地址可以从别的插件配置里借（联动 TTS-RVC 插件时不用重复填）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

DEFAULT_TIMEOUT = 90.0
MIN_AUDIO_BYTES = 1000
FAIL_THRESHOLD = 3
BACKOFF_SECONDS = 60.0
CACHE_KEEP = 200
CACHE_MAX_AGE = 7 * 24 * 3600


class TTSError(Exception):
    """合成失败，消息可以直接给用户看。"""


def normalize_base(url: str) -> str:
    """把 service_url 归一化成根地址：http://host:9880/tts -> http://host:9880。"""
    base = str(url or "").strip().rstrip("/")
    if base.endswith("/tts"):
        base = base[: -len("/tts")]
    return base


def explain_error(status: int, data: bytes) -> str:
    """把服务端的报错翻译成一句人话。"""
    text = data[:600].decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
        if isinstance(detail, str) and detail.strip():
            text = detail.strip()

    low = text.lower()
    if "asset_missing" in low or "hubert" in low or "rmvpe" in low:
        return "声音服务还缺 RVC 推理模型（HuBERT / RMVPE），先去语音服务那边把模型下好"
    if "tts_unavailable" in low or "edge-tts" in low:
        return "声音服务缺 edge-tts 这个依赖，装一下：pip install edge-tts"
    if "voice" in low and ("not found" in low or "unknown" in low or "不存在" in text):
        return "这个音色声音服务里没有，用 /音色列表 看看有哪些"
    if "connection" in low or "refused" in low:
        return "连不上声音服务，确认一下服务是不是没开"
    return f"声音服务返回 HTTP {status}：{text[:180]}"


class TTSClient:
    def __init__(self, cache_dir: str | Path, *, timeout: float = DEFAULT_TIMEOUT, log=None):
        self.cache_dir = Path(cache_dir)
        self.timeout = float(timeout)
        self.log = log
        self.base = ""
        self._down_until = 0.0
        self._down_reason = ""
        self._fails = 0

    # ------------------------------------------------------------ 服务地址

    def set_base(self, url: str) -> None:
        self.base = normalize_base(url)

    @property
    def configured(self) -> bool:
        return bool(self.base)

    def describe(self) -> str:
        if not self.base:
            return "未配置（可在插件配置里填 tts_service_url，或装 TTS-RVC 插件自动借用）"
        if time.monotonic() < self._down_until:
            left = int(self._down_until - time.monotonic())
            return f"{self.base}（熔断中，约 {left} 秒后重试：{self._down_reason}）"
        return self.base

    # ---------------------------------------------------------------- 请求

    async def _request(
        self, method: str, path: str, *, json_body: dict | None = None, timeout: float | None = None
    ) -> tuple[int, bytes]:
        # 延迟导入：离线自检不需要 aiohttp，插件运行时才有
        import aiohttp

        if not self.base.startswith(("http://", "https://")):
            raise TTSError(f"声音服务地址不合法：{self.base or '空'}")
        total = float(timeout if timeout is not None else self.timeout)
        timeout_cfg = aiohttp.ClientTimeout(total=total)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.request(method, self.base + path, json=json_body) as resp:
                data = await resp.read()
                return resp.status, data

    def _mark_down(self, reason: object) -> None:
        self._fails += 1
        if self._fails < FAIL_THRESHOLD:
            return
        self._down_until = time.monotonic() + BACKOFF_SECONDS
        self._down_reason = str(reason)[:120]
        if self.log:
            self.log.warning(
                "[voice_sender] 声音服务连续失败 %d 次，%.0f 秒内跳过合成：%s",
                self._fails,
                BACKOFF_SECONDS,
                self._down_reason,
            )

    def _mark_up(self) -> None:
        self._fails = 0
        self._down_until = 0.0
        self._down_reason = ""

    # ------------------------------------------------------------ 查询接口

    async def health(self) -> dict | None:
        try:
            status, data = await self._request("GET", "/health", timeout=3.0)
            if status != 200:
                return None
            payload = json.loads(data.decode("utf-8", "replace"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    async def voices(self, timeout: float = 5.0) -> list[dict] | None:
        """返回音色列表；服务连不上返回 None（和「一个音色都没有」区分开）。"""
        try:
            status, data = await self._request("GET", "/voices", timeout=timeout)
            if status != 200:
                return None
            payload = json.loads(data.decode("utf-8", "replace"))
            if not isinstance(payload, list):
                return None
            return [item for item in payload if isinstance(item, dict)]
        except Exception:
            return None

    # ---------------------------------------------------------------- 合成

    def _cache_path(self, text: str, voice_id: str | None) -> Path:
        digest = hashlib.sha256(f"{voice_id or '-'}\x00{text}".encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.wav"

    @staticmethod
    def _cache_ok(path: Path) -> bool:
        try:
            return (
                path.is_file()
                and path.stat().st_size > MIN_AUDIO_BYTES
                and time.time() - path.stat().st_mtime < CACHE_MAX_AGE
            )
        except OSError:
            return False

    def cleanup_cache(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        now = time.time()
        for old in files[CACHE_KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass
        for old in files[:CACHE_KEEP]:
            try:
                if now - old.stat().st_mtime > CACHE_MAX_AGE:
                    old.unlink()
            except OSError:
                pass

    async def synth(self, text: str, voice_id: str | None, *, cache: bool = True) -> str:
        """把文字合成成 wav，返回本地绝对路径。"""
        if time.monotonic() < self._down_until:
            raise TTSError(f"声音服务暂时不可用（{self._down_reason}），稍后再试")

        path = self._cache_path(text, voice_id)
        if cache and self._cache_ok(path):
            return str(path)

        payload: dict[str, object] = {"text": text}
        if voice_id:
            payload["voice_id"] = voice_id

        try:
            status, data = await self._request("POST", "/tts", json_body=payload)
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._mark_down(exc)
            raise TTSError(f"连不上声音服务（{exc}）") from exc

        if status != 200:
            error = TTSError(explain_error(status, data))
            if status >= 500:
                self._mark_down(error)
            raise error
        if len(data) < MIN_AUDIO_BYTES:
            raise TTSError("声音服务返回的音频是空的，检查模型是否加载成功")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        self._mark_up()
        self.cleanup_cache()
        return str(path)
