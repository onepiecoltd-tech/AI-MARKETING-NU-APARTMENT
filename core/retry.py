"""
core/retry.py
--------------
Decorator retry với exponential backoff, dùng chung cho mọi lời gọi API
(Meta, TikTok, Google Ads, Google Drive/Sheets, LLM) để chống lỗi rate-limit
tạm thời (HTTP 429 / 5xx / timeout).
"""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, Iterable, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


def retry_with_backoff(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    retryable_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    should_retry: Callable[[BaseException], bool] | None = None,
) -> Callable[[F], F]:
    """
    Retry hàm được decorate với exponential backoff + jitter.

    Args:
        max_retries: số lần retry tối đa (không tính lần gọi đầu).
        base_delay: delay cơ sở (giây) cho lần retry đầu tiên.
        max_delay: delay tối đa, tránh chờ quá lâu.
        retryable_exceptions: tuple các exception type được coi là có thể retry.
        should_retry: callback tùy chọn nhận exception, trả True nếu nên retry
            (dùng để lọc theo mã lỗi HTTP cụ thể, ví dụ 429/500/502/503).
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:  # noqa: BLE001
                    attempt += 1
                    if should_retry is not None and not should_retry(exc):
                        logger.error(
                            "[%s] Lỗi không thể retry: %s", func.__qualname__, exc
                        )
                        raise
                    if attempt > max_retries:
                        logger.error(
                            "[%s] Hết số lần retry (%s lần) — raise lỗi: %s",
                            func.__qualname__,
                            max_retries,
                            exc,
                        )
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay = delay + random.uniform(0, delay * 0.25)  # jitter
                    logger.warning(
                        "[%s] Lỗi: %s — retry lần %s/%s sau %.1fs",
                        func.__qualname__,
                        exc,
                        attempt,
                        max_retries,
                        delay,
                    )
                    time.sleep(delay)

        return wrapper  # type: ignore[return-value]

    return decorator


def is_rate_limit_or_transient(exc: BaseException) -> bool:
    """
    Heuristic chung: coi lỗi là tạm thời (nên retry) nếu message/status chứa
    các mã hiệu HTTP 429/500/502/503/504 hoặc liên quan timeout/connection.
    Mỗi connector có thể override bằng should_retry riêng, chính xác hơn
    (ví dụ parse status_code thật từ response của SDK).
    """
    text = str(exc).lower()
    transient_markers: Iterable[str] = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "too many requests",
        "temporarily unavailable",
    )
    return any(marker in text for marker in transient_markers)
