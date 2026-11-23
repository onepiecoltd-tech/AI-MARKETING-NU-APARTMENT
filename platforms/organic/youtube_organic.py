"""
platforms/organic/youtube_organic.py
----------------------------------------
Đăng video ORGANIC lên chính YouTube Channel của mình qua YouTube Data API v3
(`videos.insert`) — khác với platforms/google_ads.py (upload Unlisted chỉ để
làm asset cho Google Ads). Ở đây video được đăng với privacyStatus theo cột
Visibility của Content_Queue (PUBLIC/UNLISTED/PRIVATE).

Dùng chung OAuth Client ID/Secret với GoogleWorkspaceConfig (GOOGLE_OAUTH_CLIENT_ID/
SECRET — cùng 1 Google Cloud project, fallback GOOGLE_ADS_CLIENT_ID/SECRET nếu
không có) + YOUTUBE_REFRESH_TOKEN riêng — xem HUONG_DAN_SU_DUNG.md mục đăng ký.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build as build_google_api
from googleapiclient.http import MediaFileUpload

from config.settings import YouTubeConfig
from core.retry import retry_with_backoff
from platforms.organic.base_organic import OrganicPostConnector, OrganicPostResult

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

_VISIBILITY_MAP = {
    "PUBLIC": "public",
    "PRIVATE": "private",
    "UNLISTED": "unlisted",
}


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "500", "502", "503", "504", "timeout", "quota", "rate"))


class YouTubeOrganicConnector(OrganicPostConnector):
    platform_name = "YOUTUBE"

    def __init__(self, cfg: YouTubeConfig):
        self._cfg = cfg
        creds = OAuthCredentials(
            token=None,
            refresh_token=cfg.refresh_token,
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=YOUTUBE_UPLOAD_SCOPES,
        )
        creds.refresh(GoogleAuthRequest())
        self._service = build_google_api("youtube", "v3", credentials=creds, cache_discovery=False)

    @retry_with_backoff(max_retries=4, base_delay=5.0, should_retry=_is_retryable)
    def publish(
        self,
        *,
        row_id: str,
        topic_group: str,
        media_path: Path,
        media_type: str,
        content: Dict[str, Any],
        visibility: str,
    ) -> OrganicPostResult:
        if media_type.upper() != "VIDEO":
            raise ValueError("YouTube chỉ hỗ trợ đăng VIDEO, không hỗ trợ IMAGE.")

        yt_content = content["youtube"]
        privacy_status = _VISIBILITY_MAP.get(visibility.upper(), "public")

        body = {
            "snippet": {
                "title": yt_content["title"],
                "description": yt_content["description"],
                "tags": yt_content.get("tags", []),
                "categoryId": "22",  # People & Blogs — có thể đổi theo nhu cầu
            },
            "status": {"privacyStatus": privacy_status},
        }
        media = MediaFileUpload(str(media_path), chunksize=-1, resumable=True)
        request = self._service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload YouTube (organic): %d%%", int(status.progress() * 100))

        video_id = response["id"]
        post_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Đăng YouTube organic thành công: %s", post_url)
        return OrganicPostResult(
            platform=self.platform_name, post_id=video_id, post_url=post_url, raw_response=response
        )
