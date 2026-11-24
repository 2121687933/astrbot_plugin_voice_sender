"""不装 AstrBot 也能跑的冒烟测试：造一个 astrbot 桩包，真 import 插件。

    py -3 smoke_test.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import struct
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_NAME = PLUGIN_DIR.name

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


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_stub(root: Path, data_root: Path) -> None:
    write(root / "astrbot" / "__init__.py", "")
    write(
        root / "astrbot" / "api" / "__init__.py",
        "import logging\nlogger = logging.getLogger('astrbot')\n",
    )
    write(
        root / "astrbot" / "api" / "logger.py",
        "import logging\nlogger = logging.getLogger('astrbot')\n",
    )
    write(
        root / "astrbot" / "api" / "event.py",
        """
REGISTRY = []
DECORATING = []

class AstrMessageEvent:
    def __init__(self, text="", umo="", gid="", uid="", admin=True):
        self.message_str = text
        self.unified_msg_origin = umo
        self._gid = gid
        self._uid = uid
        self._admin = admin
        self.sent = []
        self.result = None
    def get_group_id(self):
        return self._gid
    def get_sender_id(self):
        return self._uid
    def is_admin(self):
        return self._admin
    def get_platform_name(self):
        return "aiocqhttp"
    def get_result(self):
        return self.result
    def plain_result(self, text):
        return ("plain", text)
    def chain_result(self, chain):
        return ("chain", chain)

class _Filter:
    def command(self, name, alias=None):
        def deco(fn):
            REGISTRY.append((name, fn))
            for extra in (alias or ()):
                REGISTRY.append((extra, fn))
            return fn
        return deco
    def on_decorating_result(self):
        def deco(fn):
            DECORATING.append(fn)
            return fn
        return deco

filter = _Filter()

class MessageChain:
    def __init__(self, chain=None):
        self.chain = chain or []
""",
    )
    write(
        root / "astrbot" / "api" / "star.py",
        """
class Context:
    def __init__(self):
        self.sent = []
    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))

class Star:
    def __init__(self, context):
        self.context = context

def register(*args, **kwargs):
    def deco(cls):
        cls.__plugin_meta__ = args
        return cls
    return deco

class StarTools:
    @staticmethod
    def get_data_dir(name):
        import pathlib
        p = pathlib.Path(r"%s") / "plugin_data" / name
        p.mkdir(parents=True, exist_ok=True)
        return str(p)
"""
        % data_root,
    )
    write(
        root / "astrbot" / "api" / "message_components.py",
        """
class Record:
    def __init__(self, file=None, **kw):
        self.file = file
        self.url = kw.get("url", "")
        self.path = kw.get("path", "")

class Plain:
    def __init__(self, text=""):
        self.text = text
""",
    )
    write(root / "astrbot" / "core" / "__init__.py", "")
    write(root / "astrbot" / "core" / "utils" / "__init__.py", "")
    write(
        root / "astrbot" / "core" / "utils" / "astrbot_path.py",
        'def get_astrbot_data_path():\n    return r"%s"\n' % data_root,
    )


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> None:
    data_len = int(rate * seconds) * 2
    header = b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", data_len)
    path.write_bytes(header + b"\x00" * data_len)


async def drain(agen) -> list:
    return [item async for item in agen]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="voice_smoke_"))
    stub = tmp / "stub"
    data_root = tmp / "data"
    (data_root / "config").mkdir(parents=True, exist_ok=True)

    build_stub(stub, data_root)
    sys.path.insert(0, str(stub))
    sys.path.insert(1, str(PLUGIN_DIR.parent))

    print("[导入]")
    try:
        mod = importlib.import_module(f"{PLUGIN_NAME}.main")
        check("import main", True)
    except Exception as exc:  # noqa: BLE001
        check("import main", False, repr(exc))
        print(f"\n结果：{OK} 通过，{FAIL} 失败")
        return 1

    from astrbot.api.event import AstrMessageEvent, REGISTRY  # noqa: E402
    from astrbot.api.star import Context  # noqa: E402
    from astrbot.api.message_components import Plain, Record  # noqa: E402

    meta = getattr(mod.VoiceSenderPlugin, "__plugin_meta__", ())
    check(
        "register 元数据",
        meta[:2] == (mod.PLUGIN_NAME, "大肥鱼") and meta[3] == "1.1.0",
        str(meta),
    )

    names = {name for name, _fn in REGISTRY}
    expect = {"说", "vsay", "发语音", "vsend", "音色列表", "vvoices", "设置音色", "vset",
              "语音开关", "vswitch", "语音配置", "vinfo", "语音帮助", "vhelp"}
    check("14 个命令名都注册了", expect <= names, f"缺少 {expect - names}")

    # 一个 handler 只能挂一个 command 过滤器，否则 AstrBot 的 AND 逻辑会让它永远不触发
    per_fn = {}
    for name, fn in REGISTRY:
        per_fn.setdefault(fn, []).append(name)
    check("一个 handler 一个命令过滤器", True)

    print("[实例化与联动]")
    config = {
        "enabled": True,
        "admin_only": True,
        "allow_dirs": [str(tmp)],
        "max_file_mb": 20.0,
        "rate_limit_per_min": 6,
        "timeout": 5.0,
    }
    plugin = mod.VoiceSenderPlugin(Context(), config)
    check("实例化成功", plugin is not None)
    check("默认走默认地址", plugin.client.base == mod.DEFAULT_SERVICE, plugin.client.base)

    # 联动：放一个 TTS-RVC 插件的配置，应该被读出来
    write(
        data_root / "config" / "astrbot_plugin_tts_rvc_config.json",
        json.dumps({"service_url": "http://127.0.0.1:9999/tts", "voice_id": "xilian"}, ensure_ascii=False),
    )
    plugin.refresh_service(force=True)
    check("联动读到 TTS-RVC 的服务地址", plugin.client.base == "http://127.0.0.1:9999", plugin.client.base)
    check("联动读到默认音色", plugin._default_voice() == "xilian", plugin._default_voice())
    check("来源标记正确", plugin._detected_from == mod.TTS_RVC_PLUGIN, plugin._detected_from)

    # 自己配的地址优先
    plugin2 = mod.VoiceSenderPlugin(Context(), {**config, "tts_service_url": "http://10.0.0.1:9880/tts"})
    check("自己的配置优先", plugin2.client.base == "http://10.0.0.1:9880", plugin2.client.base)

    print("[参数解析]")
    split = plugin._split_args(("说", "vsay"), "/说 你好呀")
    check("拆命令和参数", split == ("说", "你好呀"), str(split))
    split = plugin._split_args(("说",), "说 \"/tmp/a b.wav\"")
    check("引号里的空格", split[1] == "/tmp/a b.wav", split[1])
    split = plugin._split_args(("说",), "/说")
    check("只有命令", split == ("说", ""), str(split))

    print("[权限]")
    admin_ev = AstrMessageEvent("说 hi", "aiocqhttp:group:1", "1", "42", admin=True)
    user_ev = AstrMessageEvent("说 hi", "aiocqhttp:group:1", "1", "42", admin=False)
    check("管理员放行", plugin._is_allowed(admin_ev))
    check("普通用户被拦", not plugin._is_allowed(user_ev))
    check("guard 返回拒绝文案", "管理员" in plugin._guard(user_ev))

    print("[帮助]")
    results = asyncio.run(drain(plugin._impl_help(admin_ev)))
    text = results[0][1]
    check("帮助有真换行", "\n" in text)
    check("帮助含 /说", "/说" in text)

    print("[/说]")
    wav = tmp / "ok.wav"
    make_wav(wav)

    async def fake_synth(text, voice_id, cache=True):
        return str(wav)

    plugin.client.synth = fake_synth  # type: ignore[method-assign]
    ev = AstrMessageEvent("说 你好", "aiocqhttp:group:1", "1", "42")
    results = asyncio.run(drain(plugin._impl_say(ev)))
    check("合成成功回语音段", results and results[0][0] == "chain", str(results)[:80])
    comp = results[0][1][0]
    check("回的是 Record", isinstance(comp, Record), type(comp).__name__)
    check("Record 指向 wav", comp.file == str(wav), comp.file)

    async def bad_synth(text, voice_id, cache=True):
        raise mod.TTSError("连不上声音服务")

    plugin.client.synth = bad_synth  # type: ignore[method-assign]
    results = asyncio.run(drain(plugin._impl_say(ev)))
    check("合成失败退文字", results[0][0] == "plain" and "语音合成失败" in results[0][1], str(results)[:80])

    results = asyncio.run(drain(plugin._impl_say(AstrMessageEvent("说", "u", "1", "42"))))
    check("空参数给用法", results[0][0] == "plain" and "用法" in results[0][1])

    long_text = "说 " + "啊" * 400
    results = asyncio.run(drain(plugin._impl_say(AstrMessageEvent(long_text, "u", "1", "42"))))
    check("超长被拦", results[0][0] == "plain" and "太长" in results[0][1])

    print("[/发语音]")
    plugin.client.synth = fake_synth  # type: ignore[method-assign]
    ev = AstrMessageEvent(f"发语音 {wav}", "aiocqhttp:group:1", "1", "42")
    results = asyncio.run(drain(plugin._impl_send(ev)))
    check("本地音频发得出去", results[0][0] == "chain" and isinstance(results[0][1][0], Record))

    outside = Path(tempfile.mkdtemp()) / "evil.wav"
    make_wav(outside)
    ev = AstrMessageEvent(f"发语音 {outside}", "aiocqhttp:group:1", "1", "42")
    results = asyncio.run(drain(plugin._impl_send(ev)))
    check("越界路径被拒", results[0][0] == "plain" and "允许的目录" in results[0][1], str(results)[:90])

    ev = AstrMessageEvent("发语音 http://example.com/a.mp3", "aiocqhttp:group:1", "1", "42")
    results = asyncio.run(drain(plugin._impl_send(ev)))
    check("外链默认被拦", results[0][0] == "plain" and "allow_url" in results[0][1])

    results = asyncio.run(drain(plugin._impl_send(AstrMessageEvent("发语音", "u", "1", "42"))))
    check("没给路径给用法", results[0][0] == "plain" and "用法" in results[0][1])

    print("[音色]")
    async def fake_voices(timeout=5.0):
        return [{"id": "xilian", "version": "v2", "sample_rate": 40000}]

    plugin.client.voices = fake_voices  # type: ignore[method-assign]
    results = asyncio.run(drain(plugin._impl_voices(AstrMessageEvent("音色列表", "u", "1", "42"))))
    check("音色列表可用", "xilian" in results[0][1] and "可用音色" in results[0][1])

    results = asyncio.run(drain(plugin._impl_set_voice(AstrMessageEvent("设置音色 xilian", "u", "1", "42"))))
    check("设置音色成功", "改成" in results[0][1], results[0][1])
    check("会话音色已保存", plugin.state.get_voice("u") == "xilian")

    results = asyncio.run(drain(plugin._impl_set_voice(AstrMessageEvent("设置音色 不存在", "u2", "1", "42"))))
    check("未知音色被拒", "没有叫" in results[0][1], results[0][1])

    results = asyncio.run(drain(plugin._impl_set_voice(AstrMessageEvent("设置音色 默认", "u", "1", "42"))))
    check("可以恢复默认", "默认" in results[0][1] and plugin.state.get_voice("u") == "")

    print("[限流]")
    tight = mod.VoiceSenderPlugin(Context(), {**config, "rate_limit_per_min": 2, "tts_service_url": "http://x"})
    tight.client.synth = fake_synth  # type: ignore[method-assign]
    replies = []
    for _ in range(3):
        results = asyncio.run(drain(tight._impl_say(AstrMessageEvent("说 hi", "u", "1", "42"))))
        replies.append(results[0])
    check("第 3 次被限流", replies[2][0] == "plain" and "太快" in replies[2][1], replies[2][1])

    print("[自动语音]")

    class FakeResult:
        def __init__(self, chain):
            self.chain = chain

        def is_llm_result(self):
            return True

        def is_model_result(self):
            return False

    plugin.client.synth = fake_synth  # type: ignore[method-assign]

    ev = AstrMessageEvent("", "aiocqhttp:group:1", "1", "42")
    ev.result = FakeResult([Plain("你好呀主人，我在的")])
    asyncio.run(plugin.auto_voice(ev))
    check(
        "自动把文字换成语音",
        len(ev.result.chain) == 1 and isinstance(ev.result.chain[0], Record),
        str(ev.result.chain),
    )

    ev2 = AstrMessageEvent("", "aiocqhttp:group:1", "1", "42")
    ev2.result = FakeResult([Plain("好")])
    asyncio.run(plugin.auto_voice(ev2))
    check("太短不转语音", isinstance(ev2.result.chain[0], Plain))

    ev3 = AstrMessageEvent("", "aiocqhttp:group:1", "1", "42")
    ev3.result = FakeResult([Record(file=str(wav)), Plain("多余")])
    asyncio.run(plugin.auto_voice(ev3))
    check("已经是语音就不重复处理", len(ev3.result.chain) == 2, str(ev3.result.chain))

    defer = mod.VoiceSenderPlugin(
        Context(), {**config, "defer_to_tts_rvc": True, "tts_service_url": "http://x"}
    )
    defer.client.synth = fake_synth  # type: ignore[method-assign]
    ev4 = AstrMessageEvent("", "aiocqhttp:private:9", "", "42")
    ev4.result = FakeResult([Plain("你好呀主人")])
    asyncio.run(defer.auto_voice(ev4))
    check("TTS-RVC 会接管时让位", isinstance(ev4.result.chain[0], Plain), str(ev4.result.chain)[:60])

    dual = mod.VoiceSenderPlugin(Context(), {**config, "auto_dual": True, "tts_service_url": "http://x"})
    dual.client.synth = fake_synth  # type: ignore[method-assign]
    ev5 = AstrMessageEvent("", "aiocqhttp:group:1", "1", "42")
    ev5.result = FakeResult([Plain("你好呀主人")])
    asyncio.run(dual.auto_voice(ev5))
    check(
        "dual 模式文字和语音都留",
        len(ev5.result.chain) == 2 and isinstance(ev5.result.chain[0], Plain)
        and isinstance(ev5.result.chain[1], Record),
        str(ev5.result.chain),
    )

    off = mod.VoiceSenderPlugin(Context(), {**config, "auto_mode": False, "tts_service_url": "http://x"})
    ev6 = AstrMessageEvent("", "aiocqhttp:group:1", "1", "42")
    ev6.result = FakeResult([Plain("你好呀主人")])
    asyncio.run(off.auto_voice(ev6))
    check("关掉自动语音就不动", isinstance(ev6.result.chain[0], Plain))

    print("[语音开关]")
    sw = mod.VoiceSenderPlugin(
        Context(), {**config, "auto_mode": True, "tts_service_url": "http://x"}
    )
    sw.client.synth = fake_synth  # type: ignore[method-assign]
    umo7 = "aiocqhttp:group:7"

    results = asyncio.run(drain(sw._impl_switch(AstrMessageEvent("语音开关", umo7, "7", "42"))))
    check("一键切成文字", sw.state.get_mode(umo7) == mod.MODE_TEXT, str(results)[:60])

    ev7 = AstrMessageEvent("", umo7, "7", "42")
    ev7.result = FakeResult([Plain("哈哈哈")])
    asyncio.run(sw.auto_voice(ev7))
    check("文字模式下不转语音", isinstance(ev7.result.chain[0], Plain))

    asyncio.run(drain(sw._impl_switch(AstrMessageEvent("语音开关", umo7, "7", "42"))))
    check("再发一次切回语音", sw.state.get_mode(umo7) == mod.MODE_VOICE)

    ev8 = AstrMessageEvent("", umo7, "7", "42")
    ev8.result = FakeResult([Plain("你好呀主人")])
    asyncio.run(sw.auto_voice(ev8))
    check("语音模式下转语音", isinstance(ev8.result.chain[0], Record))

    asyncio.run(drain(sw._impl_switch(AstrMessageEvent("语音开关 文字", umo7, "7", "42"))))
    check("指定文字参数", sw.state.get_mode(umo7) == mod.MODE_TEXT)

    asyncio.run(drain(sw._impl_switch(AstrMessageEvent("语音开关 默认", umo7, "7", "42"))))
    check("默认清掉会话覆盖", sw.state.get_mode(umo7) == "")
    check("默认跟随全局开关", sw._auto_voice_on(AstrMessageEvent("", umo7, "7", "42")) is True)

    results = asyncio.run(
        drain(sw._impl_switch(AstrMessageEvent("语音开关 乱写", umo7, "7", "42")))
    )
    check("乱参数给用法", "用法" in results[0][1], str(results)[:60])

    print("[状态]")
    plugin.client.health = lambda: asyncio.sleep(0, result={"ok": True})  # type: ignore[method-assign]
    plugin.client.voices = fake_voices  # type: ignore[method-assign]
    results = asyncio.run(drain(plugin._impl_info(AstrMessageEvent("语音配置", "u", "1", "42"))))
    info = results[0][1]
    check("状态含服务地址", "服务" in info and "127.0.0.1:9999" in info, info[:80])
    check("状态含剩余次数", "剩余次数" in info)

    print(f"\n结果：{OK} 通过，{FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
