"""插件的内部模块：TTS 客户端、音频工具、会话状态。"""

from .audio import AudioError, describe, fmt_size, sniff_ext, validate, wav_duration
from .state import MODE_TEXT, MODE_VOICE, MODES, RateLimiter, SessionState
from .tts import DEFAULT_TIMEOUT, TTSError, TTSClient, explain_error, normalize_base

__all__ = [
    "AudioError",
    "DEFAULT_TIMEOUT",
    "MODE_TEXT",
    "MODE_VOICE",
    "MODES",
    "RateLimiter",
    "SessionState",
    "TTSError",
    "TTSClient",
    "describe",
    "explain_error",
    "fmt_size",
    "normalize_base",
    "sniff_ext",
    "validate",
    "wav_duration",
]
