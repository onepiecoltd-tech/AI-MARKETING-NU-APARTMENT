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
from functools import cached_property
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
    """Xác thực Google Sheets/Drive API. Hỗ trợ 2 cách, chọn 1:

    1. Service Account JSON key (GCP_SERVICE_ACCOUNT_JSON) — cách cũ, chỉ dùng
       được nếu tổ chức GCP KHÔNG bật policy `iam.disableServiceAccountKeyCreation`.
    2. OAuth Client (Desktop app) + refresh token (GOOGLE_OAUTH_CLIENT_ID /
       GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_REFRESH_TOKEN) — cách dùng khi
       org chặn tạo service account key. Lấy refresh token bằng cách chạy
       `scripts/get_google_refresh_token.py` một lần duy nhất (xem HUONG_DAN_SU_DUNG.md).

    Nếu cả 2 đều thiếu, raise lỗi rõ ràng ở __post_init__.
    """

    sheet_id: str = field(default_factory=lambda: _get_env("GSHEET_ID", required=True))
    service_account_json: str = field(
        default_factory=lambda: _get_env("GCP_SERVICE_ACCOUNT_JSON", "")
    )
    oauth_client_id: str = field(
        default_factory=lambda: _get_env("GOOGLE_OAUTH_CLIENT_ID", "")
    )
    oauth_client_secret: str = field(
        default_factory=lambda: _get_env("GOOGLE_OAUTH_CLIENT_SECRET", "")
    )
    oauth_refresh_token: str = field(
        default_factory=lambda: _get_env("GOOGLE_OAUTH_REFRESH_TOKEN", "")
    )

    def __post_init__(self) -> None:
        has_service_account = bool(self.service_account_json)
        has_oauth = bool(
            self.oauth_client_id and self.oauth_client_secret and self.oauth_refresh_token
        )
        if not has_service_account and not has_oauth:
            raise EnvironmentError(
                "Thiếu cấu hình xác thực Google Workspace. Cấu hình MỘT trong hai: "
                "(1) GCP_SERVICE_ACCOUNT_JSON, hoặc "
                "(2) cả 3 biến GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET + "
                "GOOGLE_OAUTH_REFRESH_TOKEN (xem HUONG_DAN_SU_DUNG.md mục lấy refresh token)."
            )


@dataclass(frozen=True)
class LLMConfig:
    provider: str = field(default_factory=lambda: _get_env("LLM_PROVIDER", "openai"))
    api_key: str = field(default_factory=lambda: _get_env("LLM_API_KEY", required=True))
    model_openai: str = field(default_factory=lambda: _get_env("LLM_MODEL_OPENAI", "gpt-4o-mini"))
    model_gemini: str = field(default_factory=lambda: _get_env("LLM_MODEL_GEMINI", "gemini-3.8-flash"))


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
class YouTubeConfig:
    """OAuth riêng cho đăng ORGANIC lên YouTube (platforms/organic/youtube_organic.py).
    Tách khỏi GoogleAdsConfig: job đăng organic KHÔNG cần GOOGLE_ADS_DEVELOPER_TOKEN
    hay GOOGLE_ADS_CUSTOMER_ID.

    Dùng chung OAuth Client (GOOGLE_OAUTH_CLIENT_ID/SECRET — cùng client Desktop
    app đã tạo cho GoogleWorkspaceConfig, vì cùng 1 Google Cloud project và đã
    thêm sẵn scope youtube/youtube.upload khi cấu hình OAuth consent screen).
    Nếu không có, fallback sang GOOGLE_ADS_CLIENT_ID/SECRET cho ai dùng chung
    client của Google Ads. refresh_token LUÔN là biến riêng (YOUTUBE_REFRESH_TOKEN)
    vì refresh token gắn với 1 tập scope cụ thể lúc cấp."""

    client_id: str = field(
        default_factory=lambda: _get_env("GOOGLE_OAUTH_CLIENT_ID")
        or _get_env("GOOGLE_ADS_CLIENT_ID", required=True)
    )
    client_secret: str = field(
        default_factory=lambda: _get_env("GOOGLE_OAUTH_CLIENT_SECRET")
        or _get_env("GOOGLE_ADS_CLIENT_SECRET", required=True)
    )
    refresh_token: str = field(default_factory=lambda: _get_env("YOUTUBE_REFRESH_TOKEN", required=True))


@dataclass(frozen=True)
class NotifierConfig:
    telegram_bot_token: str = field(default_factory=lambda: _get_env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _get_env("TELEGRAM_CHAT_ID", ""))


# ------------------------------------------------------------------------
# Organic posting (không qua Ads) — Facebook Page, Instagram, TikTok Content
# Posting API. Tách riêng khỏi MetaConfig/TikTokConfig (Ads) vì dùng token/app
# khác nhau (Page token thay vì Ad Account token; app TikTok "Login Kit +
# Content Posting API" thay vì "Marketing API").
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class FacebookPageConfig:
    page_id: str = field(default_factory=lambda: _get_env("FB_PAGE_ID", required=True))
    page_access_token: str = field(default_factory=lambda: _get_env("FB_PAGE_ACCESS_TOKEN", required=True))
    api_version: str = field(default_factory=lambda: _get_env("META_API_VERSION", "v21.0"))


@dataclass(frozen=True)
class InstagramConfig:
    ig_business_account_id: str = field(default_factory=lambda: _get_env("IG_BUSINESS_ACCOUNT_ID", required=True))
    # Instagram Graph API dùng chung access token với Facebook Page (Page đã
    # link với tài khoản Instagram Professional) — xem HUONG_DAN_SU_DUNG.md.
    page_access_token: str = field(default_factory=lambda: _get_env("FB_PAGE_ACCESS_TOKEN", required=True))
    api_version: str = field(default_factory=lambda: _get_env("META_API_VERSION", "v21.0"))


@dataclass(frozen=True)
class TikTokContentConfig:
    """App 'TikTok Login Kit + Content Posting API' — KHÁC với TikTokConfig
    (TikTok Business/Marketing API) dùng cho Ads ở platforms/tiktok_ads.py."""

    access_token: str = field(default_factory=lambda: _get_env("TIKTOK_CONTENT_ACCESS_TOKEN", required=True))
    api_base: str = field(default_factory=lambda: _get_env("TIKTOK_CONTENT_API_BASE", "https://open.tiktokapis.com/v2"))


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
    content_queue_tab: str = "Content_Queue"
    content_registry_tab: str = "Content_Registry"


class AppConfig:
    """
    Gom toàn bộ config, nhưng CHỈ validate/đọc env của 1 nhóm (ví dụ Meta Ads,
    hay Facebook Page organic) khi thực sự truy cập (`cfg.meta`, `cfg.facebook_page`...).

    Lý do dùng lazy property (cached_property) thay vì dataclass field thường:
    mỗi job (deploy_ads / weekly_report / run_organic_post) chỉ cần 1 tập con
    các biến môi trường. Nếu validate toàn bộ ngay khi gọi load_config(), job
    đăng organic sẽ bị raise lỗi "thiếu biến môi trường" của Meta/TikTok/Google
    Ads dù không hề dùng tới — mỗi sub-config chỉ bị validate đúng lúc cần.
    """

    @cached_property
    def google_workspace(self) -> GoogleWorkspaceConfig:
        return GoogleWorkspaceConfig()

    @cached_property
    def llm(self) -> LLMConfig:
        return LLMConfig()

    @cached_property
    def meta(self) -> MetaConfig:
        return MetaConfig()

    @cached_property
    def tiktok(self) -> TikTokConfig:
        return TikTokConfig()

    @cached_property
    def google_ads(self) -> GoogleAdsConfig:
        return GoogleAdsConfig()

    @cached_property
    def youtube(self) -> YouTubeConfig:
        return YouTubeConfig()

    @cached_property
    def facebook_page(self) -> FacebookPageConfig:
        return FacebookPageConfig()

    @cached_property
    def instagram(self) -> InstagramConfig:
        return InstagramConfig()

    @cached_property
    def tiktok_content(self) -> TikTokContentConfig:
        return TikTokContentConfig()

    @cached_property
    def notifier(self) -> NotifierConfig:
        return NotifierConfig()

    @cached_property
    def pipeline(self) -> PipelineConfig:
        return PipelineConfig()


def load_config() -> AppConfig:
    """Entry point duy nhất để nạp config. Gọi 1 lần ở đầu mỗi job — việc đọc/
    validate biến môi trường của từng nhóm (meta/tiktok/google_ads/facebook_page/...)
    chỉ xảy ra khi job thực sự truy cập đến nhóm đó."""
    return AppConfig()
