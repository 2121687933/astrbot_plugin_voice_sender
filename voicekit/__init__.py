"""插件的内部模块：TTS 客户端、音频工具、会话状态。"""

from .audio import AudioError, describe, fmt_size, sniff_ext, validate, wav_duration
from .state import RateLimiter, SessionState
from .tts import DEFAULT_TIMEOUT, TTSError, TTSClient, normalize_base

__all__ = [
    "AudioError",
    "DEFAULT_TIMEOUT",
    "RateLimiter",
    "SessionState",
    "TTSError",
    "TTSClient",
    "describe",
    "fmt_size",
    "normalize_base",
    "sniff_ext",
    "validate",
    "wav_duration",
]
