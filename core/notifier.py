"""
core/notifier.py
-----------------
Gửi thông báo lỗi & báo cáo tuần qua Telegram.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from config.settings import NotifierConfig
from core.retry import is_rate_limit_or_transient, retry_with_backoff

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096


class TelegramNotifier:
    def __init__(self, cfg: NotifierConfig):
        self._cfg = cfg
        self._enabled = bool(cfg.telegram_bot_token and cfg.telegram_chat_id)
        if not self._enabled:
            logger.warning(
                "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID chưa cấu hình — notifier sẽ chỉ log ra console."
            )

    @retry_with_backoff(max_retries=3, base_delay=2.0, should_retry=is_rate_limit_or_transient)
    def _send(self, text: str, parse_mode: str = "Markdown") -> None:
        if not self._enabled:
            logger.info("[Telegram-disabled] %s", text)
            return
        url = f"https://api.telegram.org/bot{self._cfg.telegram_bot_token}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": self._cfg.telegram_chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Telegram API lỗi {resp.status_code}: {resp.text}")

    def send_message(self, text: str) -> None:
        """Gửi message, tự động chia nhỏ nếu vượt giới hạn 4096 ký tự của Telegram."""
        for chunk in self._chunk_text(text):
            self._send(chunk)

    def send_error(self, job_name: str, error_message: str, context: Optional[str] = None) -> None:
        text = f"🚨 *Lỗi Pipeline: {job_name}*\n```\n{error_message[:1500]}\n```"
        if context:
            text += f"\n_Context:_ {context[:500]}"
        self.send_message(text)

    def send_weekly_report(self, markdown_report: str) -> None:
        text = f"📊 *Báo cáo hiệu suất quảng cáo tuần này*\n\n{markdown_report}"
        self.send_message(text)

    @staticmethod
    def _chunk_text(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN):
        if len(text) <= max_len:
            yield text
            return
        for i in range(0, len(text), max_len):
            yield text[i : i + max_len]
