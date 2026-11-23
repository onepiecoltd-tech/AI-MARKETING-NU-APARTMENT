"""
platforms/organic/facebook_organic.py
-----------------------------------------
Đăng ORGANIC lên Facebook Page của chính mình qua Graph API
(`/{page-id}/photos` cho ảnh, `/{page-id}/videos` cho video) — dùng Page
Access Token (quyền `pages_manage_posts`), KHÁC với META_ACCESS_TOKEN dùng
cho Ads ở platforms/meta_ads.py (System User token, quyền `ads_management`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import requests

from config.settings import FacebookPageConfig
from core.retry import retry_with_backoff
from platforms.organic.base_organic import OrganicPostConnector, OrganicPostResult

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "500", "502", "503", "504", "timeout", "rate limit"))


class FacebookOrganicConnector(OrganicPostConnector):
    platform_name = "FACEBOOK"

    def __init__(self, cfg: FacebookPageConfig):
        self._cfg = cfg
        self._base_url = f"https://graph.facebook.com/{cfg.api_version}"

    def _check(self, resp: requests.Response) -> Dict[str, Any]:
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Facebook Graph API lỗi: {data.get('error', data)}")
        return data

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_retryable)
    def _post_photo(self, media_path: Path, caption: str) -> Dict[str, Any]:
        url = f"{self._base_url}/{self._cfg.page_id}/photos"
        with open(media_path, "rb") as fh:
            files = {"source": fh}
            data = {"caption": caption, "access_token": self._cfg.page_access_token, "published": "true"}
            resp = requests.post(url, data=data, files=files, timeout=120)
        return self._check(resp)

    @retry_with_backoff(max_retries=4, base_delay=5.0, should_retry=_is_retryable)
    def _post_video(self, media_path: Path, caption: str) -> Dict[str, Any]:
        url = f"{self._base_url}/{self._cfg.page_id}/videos"
        with open(media_path, "rb") as fh:
            files = {"source": fh}
            data = {"description": caption, "access_token": self._cfg.page_access_token}
            resp = requests.post(url, data=data, files=files, timeout=300)
        return self._check(resp)

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
        caption = content["facebook"]["caption"]
        if media_type.upper() == "IMAGE":
            result = self._post_photo(media_path, caption)
            # /photos trả về "post_id" dạng "{page_id}_{post_id}" khi published=true —
            # dùng trực tiếp làm permalink; "id" thuần chỉ là photo id, không phải post id.
            composite_post_id = result.get("post_id")
            photo_id = result.get("id", "")
            post_id = composite_post_id or photo_id
            post_url = f"https://www.facebook.com/{composite_post_id}" if composite_post_id else f"https://www.facebook.com/{self._cfg.page_id}/photos/{photo_id}"
        elif media_type.upper() == "VIDEO":
            result = self._post_video(media_path, caption)
            post_id = result.get("id", "")
            post_url = f"https://www.facebook.com/{self._cfg.page_id}/videos/{post_id}"
        else:
            raise ValueError(f"Media_Type không hợp lệ cho Facebook: {media_type}")
        logger.info("Đăng Facebook organic thành công: %s", post_url)
        return OrganicPostResult(platform=self.platform_name, post_id=post_id or "", post_url=post_url, raw_response=result)
