"""
platforms/google_ads.py
-------------------------
Tích hợp Google Ads API v17+ qua SDK `google-ads`.

Luồng xử lý (theo mục 3.3.3 Master Spec):
Upload Image Asset (AssetService) hoặc lấy YouTube Video ID (Google Ads yêu
cầu video phải host trên YouTube; hỗ trợ upload lên YouTube Data API v3 dạng
Unlisted rồi lấy video_id tạo YouTubeVideoAsset) -> Tạo AdGroupAd (Demand Gen /
Responsive Ad) vào ad_group_id chỉ định.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build as build_google_api
from googleapiclient.http import MediaFileUpload

from config.settings import GoogleAdsConfig
from core.retry import retry_with_backoff
from platforms.base_ads import AdCreationResult, PlatformAdsConnector

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _is_google_ads_retryable(exc: BaseException) -> bool:
    if isinstance(exc, GoogleAdsException):
        # RESOURCE_EXHAUSTED / INTERNAL_ERROR / QUOTA_ERROR đáng để retry
        return True
    text = str(exc).lower()
    return any(m in text for m in ("429", "500", "502", "503", "504", "timeout", "quota", "rate"))


class GoogleAdsConnector(PlatformAdsConnector):
    platform_name = "GOOGLE"

    def __init__(self, cfg: GoogleAdsConfig):
        self._cfg = cfg
        self._client = GoogleAdsClient.load_from_dict(
            {
                "developer_token": cfg.developer_token,
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "refresh_token": cfg.refresh_token,
                "use_proto_plus": True,
            }
        )
        self._customer_id = cfg.customer_id.replace("-", "")
        self._youtube_service = None
        if cfg.youtube_refresh_token:
            self._youtube_service = self._build_youtube_service()

    def _build_youtube_service(self):
        creds = OAuthCredentials(
            token=None,
            refresh_token=self._cfg.youtube_refresh_token,
            client_id=self._cfg.client_id,
            client_secret=self._cfg.client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=YOUTUBE_UPLOAD_SCOPES,
        )
        creds.refresh(GoogleAuthRequest())
        return build_google_api("youtube", "v3", credentials=creds, cache_discovery=False)

    # --------------------------------------------------------------- upload
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_google_ads_retryable)
    def _upload_image_asset(self, media_path: Path, ad_name: str) -> str:
        asset_service = self._client.get_service("AssetService")
        asset_operation = self._client.get_type("AssetOperation")
        asset = asset_operation.create
        asset.name = f"{ad_name}_image_asset"
        asset.type_ = self._client.enums.AssetTypeEnum.IMAGE
        with open(media_path, "rb") as fh:
            asset.image_asset.data = fh.read()

        response = asset_service.mutate_assets(customer_id=self._customer_id, operations=[asset_operation])
        resource_name = response.results[0].resource_name
        logger.info("Upload Image Asset Google Ads thành công: %s", resource_name)
        return resource_name

    @retry_with_backoff(max_retries=4, base_delay=5.0, should_retry=_is_google_ads_retryable)
    def _upload_video_to_youtube(self, media_path: Path, ad_name: str) -> str:
        if self._youtube_service is None:
            raise RuntimeError(
                "YOUTUBE_REFRESH_TOKEN chưa được cấu hình — không thể tự động upload video "
                "lên YouTube để dùng cho Google Ads. Hãy cung cấp video_id có sẵn hoặc cấu hình token."
            )
        body = {
            "snippet": {
                "title": ad_name,
                "description": f"Auto-uploaded ad creative for {ad_name}",
                "categoryId": "22",
            },
            "status": {"privacyStatus": "unlisted"},
        }
        media = MediaFileUpload(str(media_path), chunksize=-1, resumable=True)
        request = self._youtube_service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload YouTube: %d%%", int(status.progress() * 100))
        video_id = response["id"]
        logger.info("Upload video lên YouTube (unlisted) thành công, video_id=%s", video_id)
        return video_id

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_google_ads_retryable)
    def _create_youtube_video_asset(self, youtube_video_id: str, ad_name: str) -> str:
        asset_service = self._client.get_service("AssetService")
        asset_operation = self._client.get_type("AssetOperation")
        asset = asset_operation.create
        asset.name = f"{ad_name}_youtube_asset"
        asset.type_ = self._client.enums.AssetTypeEnum.YOUTUBE_VIDEO
        asset.youtube_video_asset.youtube_video_id = youtube_video_id

        response = asset_service.mutate_assets(customer_id=self._customer_id, operations=[asset_operation])
        return response.results[0].resource_name

    def upload_media(self, media_path: Path, media_type: str) -> str:
        if media_type.upper() == "IMAGE":
            return self._upload_image_asset(media_path, media_path.stem)
        if media_type.upper() == "VIDEO":
            youtube_video_id = self._upload_video_to_youtube(media_path, media_path.stem)
            return self._create_youtube_video_asset(youtube_video_id, media_path.stem)
        raise ValueError(f"Media_Type không hợp lệ cho Google Ads: {media_type}")

    # ----------------------------------------------------------------- ad
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_google_ads_retryable)
    def _create_text_assets(self, texts: List[str], ad_name_prefix: str) -> List[str]:
        """Tạo TEXT asset cho headlines/descriptions, trả về list resource_name."""
        asset_service = self._client.get_service("AssetService")
        operations = []
        for i, text in enumerate(texts):
            op = self._client.get_type("AssetOperation")
            op.create.name = f"{ad_name_prefix}_text_{i}"
            op.create.text_asset.text = text
            operations.append(op)
        response = asset_service.mutate_assets(customer_id=self._customer_id, operations=operations)
        return [r.resource_name for r in response.results]

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
        google_content = content["google"]

        headline_resource_names = self._create_text_assets(google_content["headlines"], f"{ad_name}_h")
        description_resource_names = self._create_text_assets(google_content["descriptions"], f"{ad_name}_d")

        ad_group_ad_service = self._client.get_service("AdGroupAdService")
        ad_group_ad_operation = self._client.get_type("AdGroupAdOperation")
        ad_group_ad = ad_group_ad_operation.create
        ad_group_ad.ad_group = self._client.get_service("AdGroupService").ad_group_path(
            self._customer_id, adgroup_or_adset_id
        )
        ad_group_ad.status = (
            self._client.enums.AdGroupAdStatusEnum.PAUSED
            if initial_status.upper() == "PAUSED"
            else self._client.enums.AdGroupAdStatusEnum.ENABLED
        )

        # Responsive Display/Demand Gen Ad: headlines + descriptions + final_urls + image/video asset
        ad = ad_group_ad.ad
        ad.final_urls.append(landing_page_url)
        ad.responsive_display_ad.business_name = google_content["business_name"]
        ad.responsive_display_ad.long_headline.text = google_content["long_headline"]

        for res_name in headline_resource_names:
            asset_text = self._client.get_type("AdTextAsset")
            asset_text.asset = res_name
            ad.responsive_display_ad.headlines.append(asset_text)

        for res_name in description_resource_names:
            asset_text = self._client.get_type("AdTextAsset")
            asset_text.asset = res_name
            ad.responsive_display_ad.descriptions.append(asset_text)

        if media_type.upper() == "IMAGE":
            asset_ref = self._client.get_type("AdImageAsset")
            asset_ref.asset = media_identifier
            ad.responsive_display_ad.marketing_images.append(asset_ref)
        else:
            video_ref = self._client.get_type("AdVideoAsset")
            video_ref.asset = media_identifier
            ad.responsive_display_ad.youtube_videos.append(video_ref)

        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=self._customer_id, operations=[ad_group_ad_operation]
        )
        resource_name = response.results[0].resource_name
        ad_id = resource_name.split("~")[-1] if "~" in resource_name else resource_name

        return AdCreationResult(
            platform=self.platform_name,
            ad_id=ad_id,
            ad_name=ad_name,
            generated_headline=google_content["headlines"][0] if google_content["headlines"] else "",
            generated_primary_text=google_content["long_headline"],
            raw_response={"resource_name": resource_name},
        )

    # ------------------------------------------------------------- insights
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=_is_google_ads_retryable)
    def get_insights(self, ad_ids: List[str], lookback_days: int) -> List[Dict[str, Any]]:
        ga_service = self._client.get_service("GoogleAdsService")
        date_range = "LAST_7_DAYS" if lookback_days == 7 else "LAST_30_DAYS"
        query = f"""
            SELECT
                ad_group_ad.ad.id,
                metrics.cost_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions
            FROM ad_group_ad
            WHERE segments.date DURING {date_range}
              AND ad_group_ad.ad.id IN ({",".join(ad_ids)})
        """
        results: List[Dict[str, Any]] = []
        stream = ga_service.search_stream(customer_id=self._customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                results.append(
                    {
                        "ad_id": str(row.ad_group_ad.ad.id),
                        "spend": row.metrics.cost_micros / 1_000_000,
                        "impressions": row.metrics.impressions,
                        "clicks": row.metrics.clicks,
                        "conversions": row.metrics.conversions,
                    }
                )
        return results
