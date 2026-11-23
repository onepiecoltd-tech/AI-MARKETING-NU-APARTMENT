"""
platforms/organic/tiktok_organic.py
---------------------------------------
Đăng ORGANIC lên TikTok qua Content Posting API — Direct Post
(`/v2/post/publish/video/init/` với source FILE_UPLOAD, upload chunk nhị phân
trực tiếp — không cần media_url công khai như Instagram).

LƯU Ý: đây là app "Login Kit + Content Posting API" — ĐĂNG KÝ RIÊNG, khác
hoàn toàn app "TikTok Business/Marketing API" dùng cho Ads ở
platforms/tiktok_ads.py. Access token cần scope `video.publish`.

Trước khi audit: TikTok giới hạn client chưa audit chỉ đăng được ở chế độ
riêng tư (SELF_ONLY) cho tối đa 5 user/24h — xem HUONG_DAN_SU_DUNG.md.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict

import requests

from config.settings import TikTokContentConfig
from core.retry import retry_with_backoff
from platforms.organic.base_organic import OrganicPostConnector, OrganicPostResult

logger = logging.getLogger(__name__)

PUBLISH_POLL_INTERVAL_SEC = 5
PUBLISH_TIMEOUT_SEC = 300
CHUNK_SIZE_BYTES = 10 * 1024 * 1024  # 10MB/chunk theo khuyến nghị TikTok


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "500", "502", "503", "504", "timeout", "rate limit"))


class TikTokOrganicConnector(OrganicPostConnector):
    platform_name = "TIKTOK"

    def __init__(self, cfg: TikTokContentConfig):
        self._cfg = cfg
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {cfg.access_token}", "Content-Type": "application/json; charset=UTF-8"}
        )

    def _check(self, resp: requests.Response) -> Dict[str, Any]:
        resp.raise_for_status()
        payload = resp.json()
        error = payload.get("error", {})
        if error.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok Content Posting API lỗi: {error}")
        return payload.get("data", {})

    @retry_with_backoff(max_retries=3, base_delay=2.0, should_retry=_is_retryable)
    def _get_privacy_options(self) -> list:
        url = f"{self._cfg.api_base}/post/publish/creator_info/query/"
        resp = self._session.post(url, timeout=30)
        data = self._check(resp)
        return data.get("privacy_level_options", ["SELF_ONLY"])

    @retry_with_backoff(max_retries=4, base_delay=5.0, should_retry=_is_retryable)
    def _init_video_upload(self, video_size: int, caption: str, privacy_level: str) -> Dict[str, Any]:
        url = f"{self._cfg.api_base}/post/publish/video/init/"
        chunk_count = max(1, (video_size + CHUNK_SIZE_BYTES - 1) // CHUNK_SIZE_BYTES)
        payload = {
            "post_info": {
                "title": caption,
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": min(CHUNK_SIZE_BYTES, video_size),
                "total_chunk_count": chunk_count,
            },
        }
        resp = self._session.post(url, json=payload, timeout=60)
        return self._check(resp)

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_retryable)
    def _upload_chunks(self, upload_url: str, media_path: Path, video_size: int) -> None:
        with open(media_path, "rb") as fh:
            offset = 0
            while offset < video_size:
                chunk = fh.read(CHUNK_SIZE_BYTES)
                chunk_len = len(chunk)
                end_byte = offset + chunk_len - 1
                headers = {
                    "Content-Range": f"bytes {offset}-{end_byte}/{video_size}",
                    "Content-Type": "video/mp4",
                }
                resp = requests.put(upload_url, headers=headers, data=chunk, timeout=120)
                if resp.status_code not in (200, 201, 206):
                    raise RuntimeError(f"TikTok chunk upload lỗi tại offset {offset}: {resp.status_code} {resp.text}")
                offset += chunk_len
                logger.info("TikTok upload chunk: %d/%d bytes", offset, video_size)

    @retry_with_backoff(max_retries=3, base_delay=3.0, should_retry=_is_retryable)
    def _poll_publish_status(self, publish_id: str) -> Dict[str, Any]:
        url = f"{self._cfg.api_base}/post/publish/status/fetch/"
        elapsed = 0
        while elapsed < PUBLISH_TIMEOUT_SEC:
            resp = self._session.post(url, json={"publish_id": publish_id}, timeout=30)
            data = self._check(resp)
            status = data.get("status")
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                return data
            if status == "FAILED":
                raise RuntimeError(f"TikTok publish {publish_id} thất bại: {data.get('fail_reason')}")
            logger.info("TikTok publish %s đang xử lý (status=%s)...", publish_id, status)
            time.sleep(PUBLISH_POLL_INTERVAL_SEC)
            elapsed += PUBLISH_POLL_INTERVAL_SEC
        raise TimeoutError(f"TikTok publish {publish_id} không hoàn tất sau {PUBLISH_TIMEOUT_SEC}s")

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
            raise ValueError("TikTok Content Posting API (Direct Post) trong module này chỉ hỗ trợ VIDEO.")

        from core.llm_generator import compose_caption

        tt_content = content["tiktok"]
        caption = compose_caption(tt_content["caption"], tt_content.get("hashtags", []))

        privacy_options = self._get_privacy_options()
        desired = "PUBLIC_TO_EVERYONE" if visibility.upper() == "PUBLIC" else "SELF_ONLY"
        privacy_level = desired if desired in privacy_options else privacy_options[0]
        if privacy_level != desired:
            logger.warning(
                "App TikTok chưa được audit hoặc không có quyền '%s' — tự chuyển sang '%s' "
                "(xem mục TikTok trong HUONG_DAN_SU_DUNG.md).",
                desired, privacy_level,
            )

        video_size = media_path.stat().st_size
        init_result = self._init_video_upload(video_size, caption, privacy_level)
        publish_id = init_result["publish_id"]
        upload_url = init_result["upload_url"]

        self._upload_chunks(upload_url, media_path, video_size)
        status_result = self._poll_publish_status(publish_id)

        # TikTok Content Posting API không trả về URL công khai của bài đăng
        # trong response (và nếu privacy_level=SELF_ONLY thì bài cũng không có
        # link public) — để trống, anh tự mở app TikTok để lấy link nếu cần.
        post_url = ""
        logger.info("Đăng TikTok organic thành công: publish_id=%s, privacy=%s", publish_id, privacy_level)
        return OrganicPostResult(
            platform=self.platform_name, post_id=publish_id, post_url=post_url, raw_response=status_result
        )
