"""
platforms/base_ads.py
-----------------------
Abstract Base Class định nghĩa interface chung cho mọi connector nền tảng
quảng cáo (Meta, TikTok, Google Ads). Mỗi connector implement create_ad()
và trả về AdCreationResult chuẩn hóa để jobs/run_deploy_ads.py xử lý đồng nhất.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class AdCreationResult:
    """Kết quả chuẩn hóa sau khi tạo Ad thành công trên 1 nền tảng."""

    platform: str
    ad_id: str
    ad_name: str
    generated_headline: str
    generated_primary_text: str
    raw_response: Dict[str, Any]


class PlatformAdsConnector(ABC):
    """Interface chung mà meta_ads.py / tiktok_ads.py / google_ads.py phải implement."""

    platform_name: str = "BASE"

    @abstractmethod
    def upload_media(self, media_path: Path, media_type: str) -> str:
        """
        Upload file media (ảnh/video) lên nền tảng, trả về media identifier
        (image_hash/image_id, video_id, asset_resource_name, v.v. tùy nền tảng).
        """
        raise NotImplementedError

    @abstractmethod
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
        """
        Tạo Ad Creative + Ad hoàn chỉnh trên nền tảng, gắn vào adset/adgroup
        chỉ định, với trạng thái initial_status (PAUSED/ACTIVE).
        """
        raise NotImplementedError

    @abstractmethod
    def get_insights(self, ad_ids: list[str], lookback_days: int) -> list[Dict[str, Any]]:
        """
        Lấy metrics (spend, impressions, clicks, conversions...) cho danh sách
        ad_id trong N ngày gần nhất, trả về list dict đã chuẩn hóa field name:
        ad_id, spend, impressions, clicks, conversions.
        """
        raise NotImplementedError
