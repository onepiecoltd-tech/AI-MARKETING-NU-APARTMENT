"""
platforms/tiktok_ads.py
--------------------------
Tích hợp TikTok Business API v1.3 qua REST (requests), header Access-Token.

Luồng xử lý (theo mục 3.3.2 Master Spec):
Upload media qua /file/image/ad/upload/ hoặc /file/video/ad/upload/ (tính md5
signature trước khi upload) -> Nhận image_id / video_id -> Tạo Ad qua
/ad/create/ vào adgroup_id chỉ định.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config.settings import TikTokConfig
from core.retry import retry_with_backoff
from platforms.base_ads import AdCreationResult, PlatformAdsConnector

logger = logging.getLogger(__name__)


def _is_tiktok_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "500", "502", "503", "504", "timeout", "rate limit", "40100"))


def _md5_of_file(path: Path) -> str:
    hasher = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class TikTokAdsConnector(PlatformAdsConnector):
    platform_name = "TIKTOK"

    def __init__(self, cfg: TikTokConfig):
        self._cfg = cfg
        self._session = requests.Session()
        self._session.headers.update(
            {"Access-Token": cfg.access_token, "Content-Type": "application/json"}
        )

    def _check_response(self, resp: requests.Response) -> Dict[str, Any]:
        resp.raise_for_status()
        payload = resp.json()
        code = payload.get("code")
        if code != 0:
            raise RuntimeError(f"TikTok API lỗi code={code} message={payload.get('message')} | request_id={payload.get('request_id')}")
        return payload.get("data", {})

    # --------------------------------------------------------------- upload
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_tiktok_retryable)
    def _upload_image(self, media_path: Path) -> str:
        signature = _md5_of_file(media_path)
        url = f"{self._cfg.api_base}/file/image/ad/upload/"
        with open(media_path, "rb") as fh:
            files = {"image_file": (media_path.name, fh, "application/octet-stream")}
            data = {
                "advertiser_id": self._cfg.advertiser_id,
                "upload_type": "UPLOAD_BY_FILE",
                "image_signature": signature,
            }
            resp = self._session.post(url, data=data, files=files, timeout=60)
        result = self._check_response(resp)
        image_id = result.get("image_id")
        logger.info("Upload ảnh TikTok thành công, image_id=%s", image_id)
        return image_id

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_tiktok_retryable)
    def _upload_video(self, media_path: Path) -> str:
        signature = _md5_of_file(media_path)
        url = f"{self._cfg.api_base}/file/video/ad/upload/"
        with open(media_path, "rb") as fh:
            files = {"video_file": (media_path.name, fh, "application/octet-stream")}
            data = {
                "advertiser_id": self._cfg.advertiser_id,
                "upload_type": "UPLOAD_BY_FILE",
                "video_signature": signature,
            }
            resp = self._session.post(url, data=data, files=files, timeout=120)
        result = self._check_response(resp)
        # API trả về list[dict] cho video upload
        video_info = result[0] if isinstance(result, list) else result
        video_id = video_info.get("video_id")
        logger.info("Upload video TikTok thành công, video_id=%s", video_id)
        return video_id

    def upload_media(self, media_path: Path, media_type: str) -> str:
        if media_type.upper() == "IMAGE":
            return self._upload_image(media_path)
        if media_type.upper() == "VIDEO":
            return self._upload_video(media_path)
        raise ValueError(f"Media_Type không hợp lệ cho TikTok: {media_type}")

    # ----------------------------------------------------------------- ad
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_tiktok_retryable)
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
        tiktok_content = content["tiktok"]
        creative: Dict[str, Any] = {
            "ad_name": ad_name,
            "ad_text": tiktok_content["ad_text"],
            "call_to_action": tiktok_content["call_to_action"],
            "landing_page_url": landing_page_url,
            "identity_id": self._cfg.identity_id,
            "identity_type": "CUSTOMIZED_USER",
        }
        if media_type.upper() == "IMAGE":
            creative["image_ids"] = [media_identifier]
        else:
            creative["video_id"] = media_identifier

        payload = {
            "advertiser_id": self._cfg.advertiser_id,
            "adgroup_id": adgroup_or_adset_id,
            "creatives": [creative],
        }
        url = f"{self._cfg.api_base}/ad/create/"
        resp = self._session.post(url, json=payload, timeout=60)
        result = self._check_response(resp)
        ad_ids = result.get("ad_ids", [])
        ad_id = ad_ids[0] if ad_ids else ""

        # Set trạng thái ban đầu (PAUSED/ACTIVE) nếu API tạo ad không set theo initial_status
        if initial_status.upper() == "PAUSED" and ad_id:
            self._set_ad_status(ad_id, "DISABLE")
        elif initial_status.upper() == "ACTIVE" and ad_id:
            self._set_ad_status(ad_id, "ENABLE")

        return AdCreationResult(
            platform=self.platform_name,
            ad_id=ad_id,
            ad_name=ad_name,
            generated_headline=tiktok_content["ad_text"][:40],
            generated_primary_text=tiktok_content["ad_text"],
            raw_response=result,
        )

    @retry_with_backoff(max_retries=3, base_delay=2.0, should_retry=_is_tiktok_retryable)
    def _set_ad_status(self, ad_id: str, operation_status: str) -> None:
        url = f"{self._cfg.api_base}/ad/status/update/"
        payload = {
            "advertiser_id": self._cfg.advertiser_id,
            "ad_ids": [ad_id],
            "operation_status": operation_status,
        }
        resp = self._session.post(url, json=payload, timeout=30)
        self._check_response(resp)

    # ------------------------------------------------------------- insights
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_tiktok_retryable)
    def get_insights(self, ad_ids: List[str], lookback_days: int) -> List[Dict[str, Any]]:
        url = f"{self._cfg.api_base}/report/integrated/get/"
        params = {
            "advertiser_id": self._cfg.advertiser_id,
            "report_type": "BASIC",
            "dimensions": '["ad_id"]',
            "data_level": "AUCTION_AD",
            "metrics": '["spend","impressions","clicks","conversion"]',
            "filtering": f'[{{"field_name":"ad_ids","filter_type":"IN","filter_value":"{ad_ids}"}}]',
            "lifetime": "false",
            "page": 1,
            "page_size": 100,
        }
        # TikTok report API dùng window theo ngày; tính start/end theo lookback_days
        from datetime import datetime, timedelta

        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=lookback_days)
        params["start_date"] = start_date.isoformat()
        params["end_date"] = end_date.isoformat()

        resp = self._session.get(url, params=params, timeout=60)
        result = self._check_response(resp)
        results: List[Dict[str, Any]] = []
        for item in result.get("list", []):
            dims = item.get("dimensions", {})
            metrics = item.get("metrics", {})
            results.append(
                {
                    "ad_id": dims.get("ad_id"),
                    "spend": float(metrics.get("spend", 0)),
                    "impressions": int(float(metrics.get("impressions", 0))),
                    "clicks": int(float(metrics.get("clicks", 0))),
                    "conversions": int(float(metrics.get("conversion", 0))),
                }
            )
        return results
