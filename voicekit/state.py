"""会话状态（音色）和简单限流。

状态存在插件数据目录的 state.json 里，结构是 {会话 umo: {"voice": "音色 id"}}，
原子写，坏了就当空的重来，不让它影响聊天。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class SessionState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict[str, object]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
            return
        if isinstance(raw, dict):
            self._data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        else:
            self._data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get_voice(self, umo: str) -> str:
        return str(self._data.get(umo, {}).get("voice") or "")

    def set_voice(self, umo: str, voice_id: str) -> None:
        slot = self._data.setdefault(umo, {})
        if voice_id:
            slot["voice"] = voice_id
        else:
            slot.pop("voice", None)
        self.save()


class RateLimiter:
    """滑动窗口限流：key 在 window 秒内最多 limit 次。"""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = max(0, int(limit))
        self.window = float(window)
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 500:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
        return True

    def remaining(self, key: str) -> int:
        if self.limit <= 0:
            return 9999
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        return max(0, self.limit - len(hits))
