# 语音发送

让 QQ 机器人把文字变成语音发出来。音色直接复用 [TTS-RVC 语音回复](https://github.com/kuqiwang/astrbot_plugin_tts_rvc) 插件的配置和配套服务，装了 TTS-RVC 基本零配置。

TTS-RVC 是「自动把每条回复都转成语音」，这个插件是「**你想发的时候发一条**」——说一句话，机器人回你一段语音。两个插件互补，可以同时装。

## 功能

- **`/说 <文字>`** — 把文字合成语音发出来
- **`/发语音 <路径或网址>`** — 直接发一个现成的音频文件
- **`/音色列表`** — 列出声音服务上可用的音色
- **`/设置音色 <id>`** — 给当前群/私聊单独设一个音色，设 `默认` 恢复
- **`/语音配置`** — 看服务状态、地址来源、当前音色
- **`/语音帮助`** — 命令速查

中英文命令都能用：`/说` 等于 `/vsay`，`/发语音` 等于 `/vsend`，`/音色列表` 等于 `/vvoices`，`/设置音色` 等于 `/vset`，`/语音配置` 等于 `/vinfo`，`/语音帮助` 等于 `/vhelp`。

## 和 TTS-RVC 的联动

服务地址按这个顺序找，先找到的算数：

1. 本插件配置里的 `tts_service_url`（想单独指定就填这里）
2. 读 `data/config/astrbot_plugin_tts_rvc_config.json` 里的 `service_url`
3. 都没有就用默认的 `http://127.0.0.1:9880`

默认音色同理：自己的 `default_voice_id` 为空时，用 TTS-RVC 配置里的 `voice_id`。

所以只要 TTS-RVC 那边是好的，装完这个插件直接 `/说 你好` 就能出声，不用再配一遍地址。`/语音配置` 里的「地址来源」会告诉你是从哪里读到的。

## 安装

1. 插件市场搜索「语音发送」安装，或者把这个目录放进 `data/plugins/`
2. 确保声音服务在跑（默认 `http://127.0.0.1:9880`）
3. 配置里确认 `allow_dirs`，要发本地音频时把目录加进去

只依赖 `aiohttp`，AstrBot 自带，不用额外装。

## 命令

| 命令 | 别名 | 说明 |
| --- | --- | --- |
| `/说 <文字>` | `/vsay` | 合成语音发出来 |
| `/发语音 <路径/网址>` | `/vsend` | 直接发一个音频文件 |
| `/音色列表` | `/vvoices` | 看服务上有哪些音色 |
| `/设置音色 <id>` | `/vset` | 设置当前会话音色，`默认` 复位 |
| `/语音配置` | `/vinfo` | 服务状态和音色 |
| `/语音帮助` | `/vhelp` | 命令速查 |

## 配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `tts_service_url` | `""` | 声音服务地址，留空自动沿用 TTS-RVC |
| `default_voice_id` | `""` | 默认音色，留空沿用 TTS-RVC |
| `admin_only` | `true` | 只有管理员能用 |
| `allowed_users` | `[]` | 额外放行的 QQ 号 |
| `allowed_groups` | `[]` | 额外放行的群号 |
| `allow_dirs` | `["/shared"]` | 允许发出去的本地音频目录 |
| `allow_url` | `false` | 允不允许直接发外链音频 |
| `max_text_chars` | `300` | 一次最多合成多少字 |
| `max_file_mb` | `20.0` | 音频大小上限 |
| `timeout` | `90.0` | 合成超时秒数 |
| `cache` | `true` | 同一句话同一音色缓存，不重复合成 |
| `rate_limit_per_min` | `6` | 每人每分钟最多几次 |

## 支持平台

- **aiocqhttp**（NapCat / Lagrange / SnowLuma / go-cqhttp）——语音用 `Record` 消息段，普通语音条
- **qq_official**（QQ 官方机器人）——走内核文件消息段，按被动回复发送

## 安全

- 本地音频只发 `allow_dirs` 里的（插件自己的数据目录永远可读），`..` 和软链接逃逸会被拦
- 默认只给管理员用，可以按 QQ 号 / 群号白名单放开
- 有频率限制，默认每人每分钟 6 次

## 排错

**一直说「连不上声音服务」**

先在服务器上 `curl http://127.0.0.1:9880/health`。如果 AstrBot 跑在 Docker 里，容器内的 `127.0.0.1` 是容器自己；声音服务在宿主机上就写 `http://<宿主机IP>:9880`，或者把两个服务放进同一个 compose 网络，用服务名访问。

**合成失败但服务是好的**

看日志里服务返回的内容。文本太长、音色 ID 不存在、模型还没加载完，都会返回错误。

**`/发语音` 提示路径不在允许目录**

把音频所在目录加进 `allow_dirs`。不想配的话，把文件放进插件的数据目录。

**`/说` 说没权限**

默认只给管理员用。配置里 `admin_only` 改成 `false`，或者把自己的号加进 `allowed_users`。

## 目录

```
astrbot_plugin_voice_sender/
├── main.py              # 命令和调度
├── voicekit/
│   ├── tts.py           # 声音服务客户端（含缓存、熔断）
│   ├── audio.py         # 音频格式识别和校验
│   └── state.py         # 会话音色 + 限流
├── _conf_schema.json
├── metadata.yaml
├── selfcheck.py         # 离线自检，不需要 AstrBot
└── smoke_test.py        # 桩包冒烟测试，不需要 AstrBot
```

两个测试都能直接跑：

```bash
python selfcheck.py      # 纯逻辑，35 项
python smoke_test.py     # 造 astrbot 桩包，import 真插件，35 项
```

## License

MIT
