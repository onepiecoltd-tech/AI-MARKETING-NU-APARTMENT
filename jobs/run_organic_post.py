"""
jobs/run_organic_post.py
---------------------------
Entrypoint cho workflow đăng ORGANIC (không qua Ads) lên YouTube/Facebook/
Instagram/TikTok — song song với jobs/run_deploy_ads.py nhưng đọc từ tab
Content_Queue (không phải Ad_Queue) và không cần Campaign_IDs/Ad Account.

Luồng xử lý:
1. Đọc các dòng READY trong Content_Queue.
2. Với mỗi dòng: set PROCESSING -> sinh caption/title qua LLM (hoặc dùng
   Caption_Override nếu có) -> tải media tạm từ Drive -> đăng lên từng
   platform trong Platforms -> ghi lại Posted_URLs + DONE, và log vào
   Content_Registry.
3. Lỗi ở 1 platform/1 dòng không chặn các dòng/platform khác.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from config.settings import load_config
from core.gdrive_handler import GoogleDriveHandler
from core.gsheet_handler import (
    ContentQueueRow,
    GoogleSheetHandler,
    PIPELINE_STATUS_DONE,
    PIPELINE_STATUS_ERROR,
    PIPELINE_STATUS_PROCESSING,
)
from core.llm_generator import LLMContentGenerator
from core.notifier import TelegramNotifier
from platforms.organic.base_organic import OrganicPostConnector
from platforms.organic.facebook_organic import FacebookOrganicConnector
from platforms.organic.instagram_organic import InstagramOrganicConnector, drive_url_to_direct_link
from platforms.organic.tiktok_organic import TikTokOrganicConnector
from platforms.organic.youtube_organic import YouTubeOrganicConnector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_organic_post")

JOB_NAME = "Organic Post"


def _build_connector_factories(cfg) -> Dict[str, Any]:
    return {
        "YOUTUBE": lambda: YouTubeOrganicConnector(cfg.youtube),
        "FACEBOOK": lambda: FacebookOrganicConnector(cfg.facebook_page),
        "INSTAGRAM": lambda: InstagramOrganicConnector(cfg.instagram),
        "TIKTOK": lambda: TikTokOrganicConnector(cfg.tiktok_content),
    }


def _override_or_generate_content(
    row: ContentQueueRow, llm: LLMContentGenerator
) -> Dict[str, Any]:
    """Nếu Caption_Override có nội dung, dùng nguyên văn cho MỌI platform
    (bỏ qua LLM hoàn toàn cho dòng này); nếu không, gọi LLM sinh content
    theo schema riêng cho từng platform."""
    if row.caption_override.strip():
        caption = row.caption_override.strip()
        content: Dict[str, Any] = {}
        if "YOUTUBE" in row.platforms:
            content["youtube"] = {"title": caption[:100], "description": caption, "tags": []}
        if "FACEBOOK" in row.platforms:
            content["facebook"] = {"caption": caption}
        if "INSTAGRAM" in row.platforms:
            content["instagram"] = {"caption": caption, "hashtags": []}
        if "TIKTOK" in row.platforms:
            content["tiktok"] = {"caption": caption[:150], "hashtags": []}
        return content
    return llm.generate_organic_content(row.topic_group, row.topic_prompt, row.platforms)


def process_row(
    row: ContentQueueRow,
    sheet_handler: GoogleSheetHandler,
    drive_handler: GoogleDriveHandler,
    llm: LLMContentGenerator,
    connector_factories: Dict[str, Any],
    connector_cache: Dict[str, OrganicPostConnector],
    notifier: TelegramNotifier,
) -> None:
    logger.info("=== Xử lý Content Row_ID=%s | Topic_Group=%s ===", row.row_id, row.topic_group)
    sheet_handler.set_content_status(row, PIPELINE_STATUS_PROCESSING)

    posted_urls: Dict[str, str] = dict(row.posted_urls)
    registry_entries = []
    errors = []

    try:
        content = _override_or_generate_content(row, llm)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi sinh content LLM cho content row %s", row.row_id)
        sheet_handler.set_content_status(row, PIPELINE_STATUS_ERROR, f"LLM content error: {exc}")
        notifier.send_error(JOB_NAME, str(exc), context=f"Row_ID={row.row_id} (LLM content generation)")
        return

    # Instagram cần media_url công khai (Drive share "Anyone with the link"),
    # gắn thẳng vào content trước khi gọi connector.
    if "INSTAGRAM" in row.platforms and "instagram" in content:
        content["instagram"]["_media_url"] = drive_url_to_direct_link(row.media_drive_url)

    for platform in row.platforms:
        if platform in posted_urls:
            logger.info("Platform %s đã đăng cho content row %s, skip.", platform, row.row_id)
            continue
        try:
            connector = connector_cache.get(platform)
            if connector is None:
                if platform not in connector_factories:
                    raise ValueError(f"Platform '{platform}' không được hỗ trợ cho đăng organic.")
                connector = connector_factories[platform]()
                connector_cache[platform] = connector

            with drive_handler.download_temp(row.media_drive_url, row.row_id) as media_path:
                result = connector.publish(
                    row_id=row.row_id,
                    topic_group=row.topic_group,
                    media_path=media_path,
                    media_type=row.media_type,
                    content=content,
                    visibility=row.visibility,
                )

            posted_urls[platform.lower()] = result.post_url or result.post_id
            registry_entries.append(
                {
                    "Posted_Date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "Row_ID": row.row_id,
                    "Topic_Group": row.topic_group,
                    "Platform": platform,
                    "Post_ID": result.post_id,
                    "Post_URL": result.post_url,
                    "Caption": _extract_caption_for_log(content, platform),
                }
            )
            logger.info("Đăng thành công trên %s: %s", platform, result.post_url or result.post_id)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Lỗi đăng organic trên %s cho content row %s", platform, row.row_id)
            errors.append(f"[{platform}] {exc}")

    if registry_entries:
        sheet_handler.append_content_registry_entries(registry_entries)

    if errors:
        error_log = " | ".join(errors)
        final_status = PIPELINE_STATUS_ERROR if len(errors) == len(row.platforms) else PIPELINE_STATUS_DONE
        sheet_handler.set_content_posted_urls(row, posted_urls, final_status, error_log)
        notifier.send_error(JOB_NAME, error_log, context=f"Row_ID={row.row_id}")
    else:
        sheet_handler.set_content_posted_urls(row, posted_urls, PIPELINE_STATUS_DONE, "")
        logger.info("Content row %s hoàn tất DONE trên: %s", row.row_id, list(posted_urls.keys()))


def _extract_caption_for_log(content: Dict[str, Any], platform: str) -> str:
    key = platform.lower()
    if key not in content:
        return ""
    entry = content[key]
    return entry.get("caption") or entry.get("title") or ""


def main() -> int:
    cfg = load_config()
    sheet_handler = GoogleSheetHandler(
        cfg.google_workspace,
        cfg.pipeline.ad_queue_tab,
        cfg.pipeline.ad_registry_tab,
        cfg.pipeline.weekly_reports_tab,
        cfg.pipeline.content_queue_tab,
        cfg.pipeline.content_registry_tab,
    )
    drive_handler = GoogleDriveHandler(cfg.google_workspace, cfg.pipeline)
    llm = LLMContentGenerator(cfg.llm)
    notifier = TelegramNotifier(cfg.notifier)
    connector_factories = _build_connector_factories(cfg)
    connector_cache: Dict[str, OrganicPostConnector] = {}

    try:
        rows = sheet_handler.read_ready_content_rows()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi đọc Content_Queue")
        notifier.send_error(JOB_NAME, f"Không đọc được Content_Queue: {exc}")
        return 1

    if not rows:
        logger.info("Không có dòng nào ở trạng thái READY trong Content_Queue. Kết thúc.")
        return 0

    for row in rows:
        try:
            process_row(row, sheet_handler, drive_handler, llm, connector_factories, connector_cache, notifier)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Lỗi không xác định khi xử lý content row %s", row.row_id)
            try:
                sheet_handler.set_content_status(row, PIPELINE_STATUS_ERROR, f"Unexpected error: {exc}")
            except Exception:  # noqa: BLE001
                pass
            notifier.send_error(JOB_NAME, str(exc), context=f"Row_ID={row.row_id} (unexpected)")

    logger.info("Hoàn tất batch đăng organic: %d dòng đã xử lý.", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
