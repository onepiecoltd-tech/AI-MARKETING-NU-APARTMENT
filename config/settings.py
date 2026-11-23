"""
config/settings.py
-------------------
Load biến môi trường & cấu hình mặc định cho toàn bộ pipeline.

Mọi secret được đọc từ biến môi trường (GitHub Actions Secrets khi chạy trên
Runner, hoặc file .env khi chạy local qua python-dotenv). KHÔNG hard-code
bất kỳ API key nào trong source code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    # Cho phép chạy local với file .env; trên GitHub Actions biến đã có sẵn
    # trong environment nên import lỗi (chưa cài) cũng không sao.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _get_env(key: str, default: Optional[str] = None, required: bool = False) -> str:
    """Đọc biến môi trường, raise lỗi rõ ràng nếu thiếu biến bắt buộc."""
    value = os.getenv(key, default)
    if required and not value:
        raise EnvironmentError(
            f"Thiếu biến môi trường bắt buộc: '{key}'. "
            f"Vui lòng cấu hình trong GitHub Secrets hoặc file .env (xem .env.example)."
        )
    return value or ""


@dataclass(frozen=True)
class GoogleWorkspaceConfig:
    service_account_json: str = field(
        default_factory=lambda: _get_env("GCP_SERVICE_ACCOUNT_JSON", required=True)
    )
    sheet_id: str = field(default_factory=lambda: _get_env("GSHEET_ID", required=True))


@dataclass(frozen=True)
class LLMConfig:
    provider: str = field(default_factory=lambda: _get_env("LLM_PROVIDER", "openai"))
    api_key: str = field(default_factory=lambda: _get_env("LLM_API_KEY", required=True))
    model_openai: str = field(default_factory=lambda: _get_env("LLM_MODEL_OPENAI", "gpt-4o-mini"))
    model_gemini: str = field(default_factory=lambda: _get_env("LLM_MODEL_GEMINI", "gemini-1.5-pro"))


@dataclass(frozen=True)
class MetaConfig:
    access_token: str = field(default_factory=lambda: _get_env("META_ACCESS_TOKEN", required=True))
    ad_account_id: str = field(default_factory=lambda: _get_env("META_AD_ACCOUNT_ID", required=True))
    page_id: str = field(default_factory=lambda: _get_env("META_PAGE_ID", required=True))
    api_version: str = field(default_factory=lambda: _get_env("META_API_VERSION", "v21.0"))


@dataclass(frozen=True)
class TikTokConfig:
    access_token: str = field(default_factory=lambda: _get_env("TIKTOK_ACCESS_TOKEN", required=True))
    advertiser_id: str = field(default_factory=lambda: _get_env("TIKTOK_ADVERTISER_ID", required=True))
    identity_id: str = field(default_factory=lambda: _get_env("TIKTOK_IDENTITY_ID", required=True))
    api_base: str = field(default_factory=lambda: _get_env("TIKTOK_API_BASE", "https://business-api.tiktok.com/open_api/v1.3"))


@dataclass(frozen=True)
class GoogleAdsConfig:
    developer_token: str = field(default_factory=lambda: _get_env("GOOGLE_ADS_DEVELOPER_TOKEN", required=True))
    client_id: str = field(default_factory=lambda: _get_env("GOOGLE_ADS_CLIENT_ID", required=True))
    client_secret: str = field(default_factory=lambda: _get_env("GOOGLE_ADS_CLIENT_SECRET", required=True))
    refresh_token: str = field(default_factory=lambda: _get_env("GOOGLE_ADS_REFRESH_TOKEN", required=True))
    customer_id: str = field(default_factory=lambda: _get_env("GOOGLE_ADS_CUSTOMER_ID", required=True))
    youtube_refresh_token: str = field(default_factory=lambda: _get_env("YOUTUBE_REFRESH_TOKEN", ""))


@dataclass(frozen=True)
class NotifierConfig:
    telegram_bot_token: str = field(default_factory=lambda: _get_env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _get_env("TELEGRAM_CHAT_ID", ""))


@dataclass(frozen=True)
class PipelineConfig:
    """Cấu hình chung cho toàn bộ pipeline."""

    media_cache_dir: Path = Path(_get_env("MEDIA_CACHE_DIR", "/tmp/media_cache"))
    max_media_file_mb: int = int(_get_env("MAX_MEDIA_FILE_MB", "500"))
    report_lookback_days: int = int(_get_env("REPORT_LOOKBACK_DAYS", "7"))
    max_retries: int = int(_get_env("MAX_RETRIES", "5"))
    retry_base_delay_sec: float = float(_get_env("RETRY_BASE_DELAY_SEC", "2"))

    ad_queue_tab: str = "Ad_Queue"
    ad_registry_tab: str = "Ad_Registry"
    weekly_reports_tab: str = "Weekly_Reports"


@dataclass(frozen=True)
class AppConfig:
    google_workspace: GoogleWorkspaceConfig = field(default_factory=GoogleWorkspaceConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    meta: MetaConfig = field(default_factory=MetaConfig)
    tiktok: TikTokConfig = field(default_factory=TikTokConfig)
    google_ads: GoogleAdsConfig = field(default_factory=GoogleAdsConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)


def load_config() -> AppConfig:
    """Entry point duy nhất để nạp toàn bộ config. Gọi 1 lần ở đầu mỗi job."""
    return AppConfig()
