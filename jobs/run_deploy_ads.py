"""
jobs/run_deploy_ads.py
------------------------
Entrypoint cho workflow lên Ad tự động (.github/workflows/deploy_ads.yml).

Luồng xử lý:
1. Đọc các dòng READY trong Ad_Queue.
2. Với mỗi dòng: set PROCESSING -> sinh content qua LLM -> tải media tạm từ
   Drive -> upload lên từng platform trong Platforms -> tạo Ad -> ghi lại
   Created_Ad_IDs + DONE vào Ad_Queue, và append vào Ad_Registry.
3. Nếu lỗi ở bất kỳ bước nào: ghi ERROR + Error_Log vào dòng đó, gửi cảnh báo
   Telegram, và tiếp tục xử lý các dòng khác (không để 1 dòng lỗi chặn cả batch).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from config.settings import load_config
from core.gdrive_handler import GoogleDriveHandler
from core.gsheet_handler import (
    AdQueueRow,
    GoogleSheetHandler,
    PIPELINE_STATUS_DONE,
    PIPELINE_STATUS_ERROR,
    PIPELINE_STATUS_PROCESSING,
)
from core.llm_generator import LLMContentGenerator, build_ad_name, build_utm_url
from core.notifier import TelegramNotifier
from platforms.base_ads import PlatformAdsConnector
from platforms.google_ads import GoogleAdsConnector
from platforms.meta_ads import MetaAdsConnector
from platforms.tiktok_ads import TikTokAdsConnector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_deploy_ads")

JOB_NAME = "Deploy Ads"


def _build_connectors(cfg) -> Dict[str, PlatformAdsConnector]:
    """Chỉ khởi tạo connector khi thực sự cần (lazy), tránh lỗi thiếu env của
    platform không được dùng trong batch này."""
    return {
        "META": lambda: MetaAdsConnector(cfg.meta),
        "TIKTOK": lambda: TikTokAdsConnector(cfg.tiktok),
        "GOOGLE": lambda: GoogleAdsConnector(cfg.google_ads),
    }


def process_row(
    row: AdQueueRow,
    sheet_handler: GoogleSheetHandler,
    drive_handler: GoogleDriveHandler,
    llm: LLMContentGenerator,
    connector_factories: Dict[str, Any],
    connector_cache: Dict[str, PlatformAdsConnector],
    notifier: TelegramNotifier,
) -> None:
    logger.info("=== Xử lý Row_ID=%s | Topic_Group=%s ===", row.row_id, row.topic_group)
    sheet_handler.set_status(row, PIPELINE_STATUS_PROCESSING)

    created_ad_ids: Dict[str, str] = dict(row.created_ad_ids)  # giữ lại nếu retry từ lỗi trước
    registry_entries = []
    errors = []
    ad_name = build_ad_name(row.topic_group, row.row_id, datetime.now(timezone.utc))

    try:
        content = llm.generate_ad_content(row.topic_group, row.topic_prompt, row.platforms)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi sinh content LLM cho row %s", row.row_id)
        sheet_handler.set_status(row, PIPELINE_STATUS_ERROR, f"LLM content error: {exc}")
        notifier.send_error(JOB_NAME, str(exc), context=f"Row_ID={row.row_id} (LLM content generation)")
        return

    for platform in row.platforms:
        if platform in created_ad_ids:
            logger.info("Platform %s đã có Ad_ID cho row %s, skip.", platform, row.row_id)
            continue
        try:
            connector = connector_cache.get(platform)
            if connector is None:
                connector = connector_factories[platform]()
                connector_cache[platform] = connector

            adgroup_or_adset_id = row.campaign_ids.get(platform.lower(), "")
            if not adgroup_or_adset_id:
                raise ValueError(f"Thiếu Campaign_IDs cho platform {platform} trong Campaign_IDs JSON")

            utm_url = build_utm_url(row.landing_page_url, platform, row.topic_group, row.row_id)

            with drive_handler.download_temp(row.media_drive_url, row.row_id) as media_path:
                media_identifier = connector.upload_media(media_path, row.media_type)
                result = connector.create_ad(
                    row_id=row.row_id,
                    topic_group=row.topic_group,
                    ad_name=ad_name,
                    media_identifier=media_identifier,
                    media_type=row.media_type,
                    campaign_id=row.campaign_ids.get(platform.lower()),
                    adgroup_or_adset_id=adgroup_or_adset_id,
                    landing_page_url=utm_url,
                    content=content,
                    initial_status=row.initial_status,
                )

            created_ad_ids[platform.lower()] = result.ad_id
            registry_entries.append(
                {
                    "Created_Date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "Row_ID": row.row_id,
                    "Topic_Group": row.topic_group,
                    "Platform": platform,
                    "Ad_ID": result.ad_id,
                    "Ad_Name": result.ad_name,
                    "Generated_Headline": result.generated_headline,
                    "Generated_Primary_Text": result.generated_primary_text,
                }
            )
            logger.info("Tạo Ad thành công trên %s: ad_id=%s", platform, result.ad_id)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Lỗi tạo Ad trên %s cho row %s", platform, row.row_id)
            errors.append(f"[{platform}] {exc}")

    # Ghi lại kết quả cuối cùng cho dòng này
    if registry_entries:
        sheet_handler.append_registry_entries(registry_entries)

    if errors:
        error_log = " | ".join(errors)
        final_status = PIPELINE_STATUS_ERROR if len(errors) == len(row.platforms) else PIPELINE_STATUS_DONE
        sheet_handler.set_created_ad_ids(row, created_ad_ids, final_status, error_log)
        notifier.send_error(JOB_NAME, error_log, context=f"Row_ID={row.row_id}")
    else:
        sheet_handler.set_created_ad_ids(row, created_ad_ids, PIPELINE_STATUS_DONE, "")
        logger.info("Row %s hoàn tất DONE trên tất cả platforms: %s", row.row_id, list(created_ad_ids.keys()))


def main() -> int:
    cfg = load_config()
    sheet_handler = GoogleSheetHandler(
        cfg.google_workspace,
        cfg.pipeline.ad_queue_tab,
        cfg.pipeline.ad_registry_tab,
        cfg.pipeline.weekly_reports_tab,
    )
    drive_handler = GoogleDriveHandler(cfg.google_workspace, cfg.pipeline)
    llm = LLMContentGenerator(cfg.llm)
    notifier = TelegramNotifier(cfg.notifier)
    connector_factories = _build_connectors(cfg)
    connector_cache: Dict[str, PlatformAdsConnector] = {}

    try:
        rows = sheet_handler.read_ready_rows()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi đọc Ad_Queue")
        notifier.send_error(JOB_NAME, f"Không đọc được Ad_Queue: {exc}")
        return 1

    if not rows:
        logger.info("Không có dòng nào ở trạng thái READY. Kết thúc.")
        return 0

    for row in rows:
        try:
            process_row(row, sheet_handler, drive_handler, llm, connector_factories, connector_cache, notifier)
        except Exception as exc:  # noqa: BLE001
            # Lỗi không lường trước ở cấp row — không để crash cả batch.
            logger.exception("Lỗi không xác định khi xử lý row %s", row.row_id)
            try:
                sheet_handler.set_status(row, PIPELINE_STATUS_ERROR, f"Unexpected error: {exc}")
            except Exception:  # noqa: BLE001
                pass
            notifier.send_error(JOB_NAME, str(exc), context=f"Row_ID={row.row_id} (unexpected)")

    logger.info("Hoàn tất batch deploy ads: %d dòng đã xử lý.", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
