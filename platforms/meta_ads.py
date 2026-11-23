"""
platforms/meta_ads.py
-----------------------
Tích hợp Meta Marketing API (Facebook/Instagram) qua SDK facebook-business.

Luồng xử lý (theo mục 3.3.1 Master Spec):
Upload ảnh (AdImage) hoặc upload video (AdVideo - hỗ trợ chunked upload &
chờ video processing status == 'ready') -> Tạo AdCreative gắn page_id ->
Tạo Ad vào adset_id chỉ định.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adsinsights import AdsInsights
from facebook_business.api import FacebookAdsApi
from facebook_business.exceptions import FacebookRequestError

from config.settings import MetaConfig
from core.retry import retry_with_backoff
from platforms.base_ads import AdCreationResult, PlatformAdsConnector

logger = logging.getLogger(__name__)

VIDEO_PROCESSING_POLL_INTERVAL_SEC = 5
VIDEO_PROCESSING_TIMEOUT_SEC = 600


def _is_fb_retryable(exc: BaseException) -> bool:
    """Retry khi lỗi là rate-limit (code 17/32/613) hoặc lỗi tạm thời (code 1/2)."""
    if isinstance(exc, FacebookRequestError):
        code = exc.api_error_code()
        return code in (1, 2, 4, 17, 32, 613)
    return True


class MetaAdsConnector(PlatformAdsConnector):
    platform_name = "META"

    def __init__(self, cfg: MetaConfig):
        self._cfg = cfg
        FacebookAdsApi.init(access_token=cfg.access_token, api_version=cfg.api_version)
        self._account = AdAccount(cfg.ad_account_id)

    # --------------------------------------------------------------- upload
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_fb_retryable)
    def _upload_image(self, media_path: Path) -> str:
        image = AdImage(parent_id=self._cfg.ad_account_id)
        image[AdImage.Field.filename] = str(media_path)
        image.remote_create()
        image_hash = image[AdImage.Field.hash]
        logger.info("Upload ảnh Meta thành công, hash=%s", image_hash)
        return image_hash

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_fb_retryable)
    def _upload_video(self, media_path: Path) -> str:
        video = AdVideo(parent_id=self._cfg.ad_account_id)
        video[AdVideo.Field.filepath] = str(media_path)
        video.remote_create()
        video_id = video.get_id()
        self._wait_for_video_ready(video_id)
        logger.info("Upload video Meta thành công, video_id=%s", video_id)
        return video_id

    def _wait_for_video_ready(self, video_id: str) -> None:
        """Chờ video processing xong (status == 'ready') trước khi tạo creative."""
        elapsed = 0
        video = AdVideo(video_id)
        while elapsed < VIDEO_PROCESSING_TIMEOUT_SEC:
            video.api_get(fields=["status"])
            status = video.get("status", {}).get("video_status")
            if status == "ready":
                return
            if status == "error":
                raise RuntimeError(f"Meta video {video_id} xử lý lỗi: {video.get('status')}")
            logger.info("Video %s đang xử lý (status=%s), chờ %ss...", video_id, status, VIDEO_PROCESSING_POLL_INTERVAL_SEC)
            time.sleep(VIDEO_PROCESSING_POLL_INTERVAL_SEC)
            elapsed += VIDEO_PROCESSING_POLL_INTERVAL_SEC
        raise TimeoutError(f"Meta video {video_id} không xử lý xong sau {VIDEO_PROCESSING_TIMEOUT_SEC}s")

    def upload_media(self, media_path: Path, media_type: str) -> str:
        if media_type.upper() == "IMAGE":
            return self._upload_image(media_path)
        if media_type.upper() == "VIDEO":
            return self._upload_video(media_path)
        raise ValueError(f"Media_Type không hợp lệ cho Meta: {media_type}")

    # ----------------------------------------------------------------- ad
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_fb_retryable)
    def _create_creative(
        self, ad_name: str, media_identifier: str, media_type: str, landing_page_url: str, content: Dict[str, Any]
    ) -> str:
        meta_content = content["meta"]
        link_data: Dict[str, Any] = {
            "link": landing_page_url,
            "message": meta_content["primary_text"][0] if meta_content["primary_text"] else "",
            "name": meta_content["headline"],
            "description": meta_content["description"],
            "call_to_action": {
                "type": meta_content["call_to_action"],
                "value": {"link": landing_page_url},
            },
        }
        if media_type.upper() == "IMAGE":
            link_data["image_hash"] = media_identifier
        else:
            link_data["video_id"] = media_identifier
            # Video ads trên Meta cần image_url cho thumbnail; dùng page picture mặc định
            # nếu không có thumbnail riêng được cung cấp trong luồng này.

        object_story_spec = {
            "page_id": self._cfg.page_id,
            "link_data": link_data,
        }

        creative = AdCreative(parent_id=self._cfg.ad_account_id)
        creative.update(
            {
                AdCreative.Field.name: f"{ad_name}_creative",
                AdCreative.Field.object_story_spec: object_story_spec,
            }
        )
        creative.remote_create()
        return creative.get_id()

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_fb_retryable)
    def _create_ad(self, ad_name: str, adset_id: str, creative_id: str, initial_status: str) -> Ad:
        ad = Ad(parent_id=self._cfg.ad_account_id)
        ad.update(
            {
                Ad.Field.name: ad_name,
                Ad.Field.adset_id: adset_id,
                Ad.Field.creative: {"creative_id": creative_id},
                Ad.Field.status: initial_status.upper(),
            }
        )
        ad.remote_create()
        return ad

    def create_ad(
        self,
        *,
        row_id: str,
        topic_group: str,
        ad_name: str,
        media_identifier: str,
        media_type: str,
        campaign_id: Optional[str],
        adgroup_or_adset_id: str,
        landing_page_url: str,
        content: Dict[str, Any],
        initial_status: str,
    ) -> AdCreationResult:
        creative_id = self._create_creative(ad_name, media_identifier, media_type, landing_page_url, content)
        ad = self._create_ad(ad_name, adgroup_or_adset_id, creative_id, initial_status)
        meta_content = content["meta"]
        return AdCreationResult(
            platform=self.platform_name,
            ad_id=ad.get_id(),
            ad_name=ad_name,
            generated_headline=meta_content["headline"],
            generated_primary_text=meta_content["primary_text"][0] if meta_content["primary_text"] else "",
            raw_response=ad.export_all_data(),
        )

    # ------------------------------------------------------------- insights
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_fb_retryable)
    def get_insights(self, ad_ids: List[str], lookback_days: int) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for ad_id in ad_ids:
            ad = Ad(ad_id)
            insights = ad.get_insights(
                params={
                    "date_preset": "last_7d" if lookback_days == 7 else f"last_{lookback_days}d",
                    "fields": [
                        AdsInsights.Field.spend,
                        AdsInsights.Field.impressions,
                        AdsInsights.Field.clicks,
                        AdsInsights.Field.actions,
                    ],
                }
            )
            for row in insights:
                conversions = 0
                for action in row.get("actions", []) or []:
                    if action.get("action_type") in ("offsite_conversion", "lead", "purchase"):
                        conversions += int(float(action.get("value", 0)))
                results.append(
                    {
                        "ad_id": ad_id,
                        "spend": float(row.get("spend", 0)),
                        "impressions": int(row.get("impressions", 0)),
                        "clicks": int(row.get("clicks", 0)),
                        "conversions": conversions,
                    }
                )
        return results
