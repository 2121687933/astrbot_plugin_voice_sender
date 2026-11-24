"""发语音 —— 把文字变成语音发到 QQ，音色和 TTS-RVC 插件共用。

设计上的取舍：
- 合成交给配套的 tts-rvc-service（和 TTS-RVC 语音回复插件是同一个服务），
  本插件只管「怎么让你想发的时候发出去」，避免重复实现 TTS + RVC。
- 没填服务地址时自动去读 TTS-RVC 插件的配置，做到装了就联动、不用重复配。
- 发出去的是 Record 语音段；合成失败一律退回文字提示，不吞消息。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star

try:  # AstrBot 各版本 register 的落点略有差异
    from astrbot.api.star import register
except ImportError:  # pragma: no cover - 老版本兜底
    def register(*args, **kwargs):  # type: ignore[misc]
        def _wrap(cls):
            cls.__plugin_meta__ = args
            return cls

        return _wrap

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:  # pragma: no cover
    get_astrbot_data_path = None  # type: ignore[assignment]

from .voicekit import (
    AudioError,
    RateLimiter,
    SessionState,
    TTSError,
    TTSClient,
    describe,
    validate,
)

PLUGIN_NAME = "astrbot_plugin_voice_sender"
TTS_RVC_PLUGIN = "astrbot_plugin_tts_rvc"
DEFAULT_SERVICE = "http://127.0.0.1:9880"
MAX_TEXT = 300
DETECT_INTERVAL = 30.0

HELP_TEXT = """我把文字变成语音发出去，音色跟 TTS-RVC 插件共用。

/说 <文字>         合成语音发出来（也能用 /vsay）
/发语音 <路径|URL>  把现成的音频发成语音（/vsend）
/音色列表          看声音服务里有哪些音色（/vvoices）
/设置音色 <id>      换当前会话的音色（/vset）
/语音配置          看服务地址、健康状态（/vinfo）
/语音帮助          就是这条（/vhelp）

语音服务要先跑起来，默认地址 http://127.0.0.1:9880。
没填的话会自动借用 TTS-RVC 插件的配置。"""


@register(
    PLUGIN_NAME,
    "大肥鱼",
    "把文字变成语音发到 QQ，音色跟 TTS-RVC 插件共用",
    "1.0.0",
)
class VoiceSenderPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.data_dir = self._data_dir()
        self.client = TTSClient(
            self.data_dir / "cache",
            timeout=float(self._cfg("timeout", 90.0)),
            log=logger,
        )
        self.state = SessionState(self.data_dir / "state.json")
        self.limiter = RateLimiter(int(self._cfg("rate_limit_per_min", 6)))
        self._last_detect = 0.0
        self._detected_from = ""
        self.refresh_service(force=True)

    # ------------------------------------------------------------------ 基础

    def _cfg(self, key: str, default=None):
        conf = getattr(self, "config", None)
        if conf is None:
            return default
        try:
            value = conf.get(key, default)
        except Exception:  # noqa: BLE001
            try:
                value = conf[key]  # type: ignore[index]
            except Exception:  # noqa: BLE001
                return default
        return default if value is None else value

    @staticmethod
    def _astrbot_data_root() -> Path | None:
        if get_astrbot_data_path is None:
            return None
        try:
            return Path(get_astrbot_data_path())
        except Exception:  # noqa: BLE001
            return None

    def _data_dir(self) -> Path:
        root = self._astrbot_data_root()
        if root is not None:
            target = root / "plugin_data" / PLUGIN_NAME
        else:
            try:
                from astrbot.api.star import StarTools

                target = Path(StarTools.get_data_dir(PLUGIN_NAME))
            except Exception:  # noqa: BLE001
                target = Path(__file__).resolve().parent / "data"
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------ 服务地址（联动）

    def _read_tts_rvc_config(self) -> dict:
        """读 TTS-RVC 插件的配置，拿它的 service_url 和 voice_id。"""
        root = self._astrbot_data_root()
        if root is None:
            return {}
        path = root / "config" / f"{TTS_RVC_PLUGIN}_config.json"
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def refresh_service(self, *, force: bool = False) -> None:
        """确定用哪个声音服务：自己配的优先，其次借 TTS-RVC 的，最后用默认。"""
        now = time.monotonic()
        if not force and self.client.configured and now - self._last_detect < DETECT_INTERVAL:
            return
        if not force and now - self._last_detect < DETECT_INTERVAL:
            return
        self._last_detect = now

        own = str(self._cfg("tts_service_url", "") or "").strip()
        if own:
            self.client.set_base(own)
            self._detected_from = "插件配置"
            return

        borrowed = self._read_tts_rvc_config()
        url = str(borrowed.get("service_url") or "").strip()
        if url:
            self.client.set_base(url)
            self._detected_from = TTS_RVC_PLUGIN
            return

        self.client.set_base(DEFAULT_SERVICE)
        self._detected_from = "默认地址"

    def _default_voice(self) -> str:
        own = str(self._cfg("default_voice_id", "") or "").strip()
        if own:
            return own
        return str(self._read_tts_rvc_config().get("voice_id") or "").strip()

    def _voice_of(self, event: AstrMessageEvent) -> str:
        umo = self._umo(event)
        session_voice = self.state.get_voice(umo)
        return session_voice or self._default_voice()

    # ---------------------------------------------------------- 权限与限流

    @staticmethod
    def _umo(event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "")

    @staticmethod
    def _gid(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_group_id() or "")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _uid(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:  # noqa: BLE001
            return ""

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:  # noqa: BLE001
            return False

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        if self._is_admin(event):
            return True
        if not self._cfg("admin_only", True):
            return True
        users = [str(x) for x in (self._cfg("allowed_users", []) or [])]
        groups = [str(x) for x in (self._cfg("allowed_groups", []) or [])]
        if users and self._uid(event) in users:
            return True
        if groups and self._gid(event) in groups:
            return True
        return False

    def _guard(self, event: AstrMessageEvent, *, limit: bool = True) -> str:
        if not self._cfg("enabled", True):
            return "发语音功能现在是关着的，去插件配置里打开 enabled。"
        if not self._is_allowed(event):
            return "这条命令只给管理员用，找主人开个权限吧。"
        if limit and not self.limiter.allow(self._umo(event)):
            limit_n = int(self._cfg("rate_limit_per_min", 6))
            return f"说得太快啦，每分钟最多 {limit_n} 次，等一下再试。"
        return ""

    # ------------------------------------------------------------ 参数解析

    @staticmethod
    def _split_args(command_names: tuple[str, ...], message: str) -> tuple[str, str]:
        """把「/说 你好呀」拆成 ("说", "你好呀")，引号里的空格保留。"""
        raw = (message or "").strip()
        if not raw:
            return "", ""
        head, _, tail = raw.partition(" ")
        name = head.lstrip("/／").strip()
        rest = tail.strip()
        if rest[:1] in ('"', "“", "'"):
            quote = rest[0]
            close = {'"': '"', "“": "”", "'": "'"}[quote]
            end = rest.find(close, 1)
            if end > 0:
                return name, rest[1:end].strip()
        return name, rest

    def _audio_roots(self) -> list[Path]:
        roots: list[Path] = []
        for item in self._cfg("allow_dirs", []) or []:
            try:
                roots.append(Path(str(item)).expanduser().resolve())
            except OSError:
                continue
        roots.append(self.data_dir.resolve())
        return roots

    def _resolve_audio(self, raw: str) -> Path:
        text = (raw or "").strip().strip('"“”')
        if not text:
            raise AudioError("没给文件路径")
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = self.data_dir / path
        try:
            path = path.resolve()
        except OSError as exc:
            raise AudioError(f"路径解析失败：{exc}") from exc
        for root in self._audio_roots():
            if path == root or root in path.parents:
                return path
        raise AudioError("这个路径不在允许的目录里，去配置里把目录加进 allow_dirs")

    # ------------------------------------------------------------ 发送语音

    @staticmethod
    def _record(path: str):
        try:
            return Record(file=path, url=path)
        except TypeError:  # 老版本只认 file
            return Record(file=path)

    async def _impl_say(self, event: AstrMessageEvent):
        error = self._guard(event)
        if error:
            yield event.plain_result(error)
            return

        _, text = self._split_args(("说", "vsay", "语音"), event.message_str)
        if not text:
            yield event.plain_result("用法：/说 你好呀，合成好会直接发一条语音出来。")
            return
        if len(text) > MAX_TEXT:
            yield event.plain_result(f"文字太长啦，控制在 {MAX_TEXT} 字以内（现在 {len(text)} 字）。")
            return

        self.refresh_service()
        if not self.client.configured:
            yield event.plain_result("还没找到声音服务，先在插件配置里填 tts_service_url。")
            return

        voice_id = self._voice_of(event)
        try:
            path = await self.client.synth(
                text, voice_id, cache=bool(self._cfg("cache", True))
            )
        except TTSError as exc:
            logger.warning("[voice_sender] 合成失败：%s", exc)
            yield event.plain_result(f"语音合成失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("[voice_sender] 合成出现意外错误")
            yield event.plain_result(f"语音合成失败：{exc}")
            return

        logger.info("[voice_sender] 已合成 %d 字 -> %s", len(text), path)
        yield event.chain_result([self._record(path)])

    async def _impl_send(self, event: AstrMessageEvent):
        error = self._guard(event)
        if error:
            yield event.plain_result(error)
            return

        _, target = self._split_args(("发语音", "vsend"), event.message_str)
        if not target:
            yield event.plain_result(
                "用法：/发语音 <文件路径或 http 链接>\n"
                "路径要放在配置的 allow_dirs 里，也可以用 /说 直接合成。"
            )
            return

        if target.startswith(("http://", "https://")):
            if not self._cfg("allow_url", False):
                yield event.plain_result(
                    "发外链音频需要在配置里打开 allow_url，默认关着以防被拿去发奇怪的东西。"
                )
                return
            yield event.chain_result([self._record(target)])
            return

        try:
            path = self._resolve_audio(target)
            size, _ext = validate(path, float(self._cfg("max_file_mb", 20) or 0))
        except AudioError as exc:
            yield event.plain_result(f"发不了：{exc}")
            return

        logger.info("[voice_sender] 发送本地音频 %s（%d 字节）", path, size)
        yield event.chain_result([self._record(str(path))])

    # ------------------------------------------------------------ 音色相关

    async def _impl_voices(self, event: AstrMessageEvent):
        error = self._guard(event)
        if error:
            yield event.plain_result(error)
            return

        self.refresh_service()
        voices = await self.client.voices()
        if voices is None:
            yield event.plain_result(
                f"连不上声音服务（{self.client.describe()}）。\n"
                "先确认语音服务启动了：浏览器打开服务地址下的 /health 看看。"
            )
            return
        if not voices:
            yield event.plain_result(
                "声音服务里还没有导入 RVC 模型，先把 .pth 和 .index 导进去。"
            )
            return

        current = self._voice_of(event)
        lines = [f"可用音色（{len(voices)}）："]
        for item in voices:
            vid = str(item.get("id") or "?")
            mark = "  ← 当前" if vid == current else ""
            rate = item.get("sample_rate") or "?"
            version = item.get("version") or "?"
            lines.append(f"- {vid}（{version} / {rate}Hz）{mark}")
        lines.append("用 /设置音色 <id> 换当前会话的音色。")
        yield event.plain_result("\n".join(lines))

    async def _impl_set_voice(self, event: AstrMessageEvent):
        error = self._guard(event)
        if error:
            yield event.plain_result(error)
            return

        _, voice_id = self._split_args(("设置音色", "vset"), event.message_str)
        umo = self._umo(event)
        if not voice_id:
            yield event.plain_result("用法：/设置音色 <音色 id>，先用 /音色列表 看看有哪些。")
            return
        if voice_id.lower() in ("默认", "default", "reset"):
            self.state.set_voice(umo, "")
            yield event.plain_result("已恢复默认音色。")
            return

        self.refresh_service()
        voices = await self.client.voices()
        if voices is not None and voices:
            known = {str(v.get("id")) for v in voices}
            if voice_id not in known:
                yield event.plain_result(
                    f"没有叫「{voice_id}」的音色。当前可用：" + "、".join(sorted(known))
                )
                return

        self.state.set_voice(umo, voice_id)
        yield event.plain_result(f"当前会话的音色改成：{voice_id}")

    async def _impl_info(self, event: AstrMessageEvent):
        error = self._guard(event, limit=False)
        if error:
            yield event.plain_result(error)
            return

        self.refresh_service(force=True)
        health = await self.client.health()
        voices = await self.client.voices(timeout=3.0)
        umo = self._umo(event)

        lines = ["语音发送状态"]
        lines.append(f"服务：{self.client.describe()}")
        lines.append(f"来源：{self._detected_from or '未确定'}")
        if health is None:
            lines.append("健康：连不上（服务没启动或地址不对）")
        else:
            ok = health.get("ok", health.get("status", "?"))
            model = health.get("model") or health.get("current_model") or ""
            lines.append(f"健康：{ok}" + (f"｜模型 {model}" if model else ""))
        if voices is None:
            lines.append("音色：查询失败")
        else:
            lines.append(f"音色：{len(voices)} 个")
        lines.append(f"当前音色：{self._voice_of(event) or '默认'}")
        lines.append(f"会话覆盖：{self.state.get_voice(umo) or '无'}")
        lines.append(f"本分钟剩余次数：{self.limiter.remaining(umo)}")
        try:
            cached = len(list((self.data_dir / "cache").glob("*.wav")))
        except OSError:
            cached = 0
        lines.append(f"缓存文件：{cached} 个")
        yield event.plain_result("\n".join(lines))

    async def _impl_help(self, event: AstrMessageEvent):
        yield event.plain_result(HELP_TEXT)

    # ---------------------------------------------------------------- 指令

    @filter.command("说")
    @filter.command("vsay")
    async def say(self, event: AstrMessageEvent):
        async for result in self._impl_say(event):
            yield result

    @filter.command("发语音")
    @filter.command("vsend")
    async def send_voice(self, event: AstrMessageEvent):
        async for result in self._impl_send(event):
            yield result

    @filter.command("音色列表")
    @filter.command("vvoices")
    async def voices(self, event: AstrMessageEvent):
        async for result in self._impl_voices(event):
            yield result

    @filter.command("设置音色")
    @filter.command("vset")
    async def set_voice(self, event: AstrMessageEvent):
        async for result in self._impl_set_voice(event):
            yield result

    @filter.command("语音配置")
    @filter.command("vinfo")
    async def info(self, event: AstrMessageEvent):
        async for result in self._impl_info(event):
            yield result

    @filter.command("语音帮助")
    @filter.command("vhelp")
    async def help(self, event: AstrMessageEvent):
        async for result in self._impl_help(event):
            yield result
