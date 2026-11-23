"""
platforms/organic/instagram_organic.py
------------------------------------------
Đăng ORGANIC lên Instagram (Business/Creator account) qua Instagram Graph API
— Content Publishing (tạo media container → publish).

LƯU Ý QUAN TRỌNG: Instagram Graph API KHÔNG nhận upload file nhị phân trực
tiếp — chỉ nhận `image_url`/`video_url` là link công khai để server Instagram
tự tải về. Vì vậy Media_Drive_URL trong Content_Queue PHẢI được share ở chế độ
"Anyone with the link" (không chỉ share cho Service Account) để Instagram có
thể fetch được. Xem HUONG_DAN_SU_DUNG.md mục Instagram để biết cách lấy link
tải trực tiếp từ Google Drive.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict

import requests

from config.settings import InstagramConfig
from core.gdrive_handler import extract_drive_file_id
from core.retry import retry_with_backoff
from platforms.organic.base_organic import OrganicPostConnector, OrganicPostResult

logger = logging.getLogger(__name__)

VIDEO_CONTAINER_POLL_INTERVAL_SEC = 5
VIDEO_CONTAINER_TIMEOUT_SEC = 300


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "500", "502", "503", "504", "timeout", "rate limit"))


def drive_url_to_direct_link(media_drive_url: str) -> str:
    """Chuyển link share Google Drive thành link tải trực tiếp để Instagram
    (và TikTok PULL_FROM_URL) có thể fetch. YÊU CẦU file đã share "Anyone with
    the link" — nếu chỉ share cho Service Account, Instagram sẽ không tải được."""
    file_id = extract_drive_file_id(media_drive_url)
    return f"https://drive.google.com/uc?export=download&id={file_id}"


class InstagramOrganicConnector(OrganicPostConnector):
    platform_name = "INSTAGRAM"

    def __init__(self, cfg: InstagramConfig):
        self._cfg = cfg
        self._base_url = f"https://graph.facebook.com/{cfg.api_version}"

    def _check(self, resp: requests.Response) -> Dict[str, Any]:
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Instagram Graph API lỗi: {data.get('error', data)}")
        return data

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_retryable)
    def _create_container(self, media_url: str, caption: str, media_type: str) -> str:
        url = f"{self._base_url}/{self._cfg.ig_business_account_id}/media"
        payload = {"caption": caption, "access_token": self._cfg.page_access_token}
        if media_type.upper() == "IMAGE":
            payload["image_url"] = media_url
        else:
            payload["video_url"] = media_url
            payload["media_type"] = "REELS"  # single feed video => đăng dạng Reels theo chính sách hiện tại của Meta
        resp = requests.post(url, data=payload, timeout=60)
        result = self._check(resp)
        return result["id"]

    def _wait_container_ready(self, creation_id: str) -> None:
        url = f"{self._base_url}/{creation_id}"
        elapsed = 0
        while elapsed < VIDEO_CONTAINER_TIMEOUT_SEC:
            resp = requests.get(url, params={"fields": "status_code", "access_token": self._cfg.page_access_token}, timeout=30)
            result = self._check(resp)
            status = result.get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise RuntimeError(f"Instagram container {creation_id} xử lý lỗi.")
            logger.info("Instagram container %s đang xử lý (status=%s)...", creation_id, status)
            time.sleep(VIDEO_CONTAINER_POLL_INTERVAL_SEC)
            elapsed += VIDEO_CONTAINER_POLL_INTERVAL_SEC
        raise TimeoutError(f"Instagram container {creation_id} không xử lý xong sau {VIDEO_CONTAINER_TIMEOUT_SEC}s")

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_retryable)
    def _publish_container(self, creation_id: str) -> Dict[str, Any]:
        url = f"{self._base_url}/{self._cfg.ig_business_account_id}/media_publish"
        resp = requests.post(
            url, data={"creation_id": creation_id, "access_token": self._cfg.page_access_token}, timeout=60
        )
        return self._check(resp)

    @retry_with_backoff(max_retries=3, base_delay=2.0, should_retry=_is_retryable)
    def _get_permalink(self, media_id: str) -> str:
        """Media ID trả về từ media_publish KHÔNG phải shortcode hiển thị trên
        URL công khai — phải gọi riêng field `permalink` để lấy link thật."""
        url = f"{self._base_url}/{media_id}"
        resp = requests.get(url, params={"fields": "permalink", "access_token": self._cfg.page_access_token}, timeout=30)
        result = self._check(resp)
        return result.get("permalink", "")

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
        from core.llm_generator import compose_caption

        ig_content = content["instagram"]
        caption = compose_caption(ig_content["caption"], ig_content.get("hashtags", []))

        # media_path không dùng trực tiếp — Instagram cần media_url công khai.
        # media_url được truyền kèm qua content["instagram"]["_media_url"] do
        # jobs/run_organic_post.py gắn vào trước khi gọi publish().
        media_url = ig_content.get("_media_url")
        if not media_url:
            raise ValueError("Thiếu media_url công khai (Drive) để Instagram fetch — xem drive_url_to_direct_link().")

        creation_id = self._create_container(media_url, caption, media_type)
        if media_type.upper() == "VIDEO":
            self._wait_container_ready(creation_id)
        result = self._publish_container(creation_id)
        media_id = result.get("id", "")
        post_url = self._get_permalink(media_id) if media_id else ""
        logger.info("Đăng Instagram organic thành công: media_id=%s", media_id)
        return OrganicPostResult(platform=self.platform_name, post_id=media_id, post_url=post_url, raw_response=result)
