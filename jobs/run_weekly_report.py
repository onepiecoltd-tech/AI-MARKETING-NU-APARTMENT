"""
jobs/run_weekly_report.py
----------------------------
Entrypoint cho workflow tổng hợp báo cáo tuần (.github/workflows/weekly_report.yml).
Chạy 08:00 sáng Thứ 2 hàng tuần (UTC+7) — xem mục 3.5 Master Spec.

Logic:
1. Đọc danh sách Ad_ID đang hoạt động/có phát sinh chi tiêu từ Ad_Registry.
2. Query Insights/Metrics từ Meta, TikTok, Google Ads cho 7 ngày gần nhất.
3. Join dữ liệu với cột Topic_Group (từ Ad_Registry).
4. Tính tổng chỉ số theo Từng nền tảng và Từng nhóm chủ đề (cross-platform).
5. Gửi bảng số liệu vào LLM để sinh nhận xét (Top 2 hiệu quả nhất / Top 2 cần tối ưu).
6. Ghi vào tab Weekly_Reports và gửi tin nhắn Markdown về Telegram.
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from config.settings import load_config
from core.gsheet_handler import GoogleSheetHandler
from core.llm_generator import LLMContentGenerator
from core.notifier import TelegramNotifier
from platforms.base_ads import PlatformAdsConnector
from platforms.google_ads import GoogleAdsConnector
from platforms.meta_ads import MetaAdsConnector
from platforms.tiktok_ads import TikTokAdsConnector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_weekly_report")

JOB_NAME = "Weekly Report"


def _week_range_label(lookback_days: int) -> str:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    return f"{start.isoformat()} - {end.isoformat()}"


def _group_registry_by_platform(registry: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in registry:
        platform = (entry.get("Platform") or "").strip().upper()
        if platform and entry.get("Ad_ID"):
            grouped[platform].append(entry)
    return grouped


def _build_connectors(cfg) -> Dict[str, PlatformAdsConnector]:
    connectors: Dict[str, PlatformAdsConnector] = {}
    try:
        connectors["META"] = MetaAdsConnector(cfg.meta)
    except Exception:  # noqa: BLE001
        logger.warning("Không khởi tạo được Meta connector (có thể thiếu config) — skip Meta trong report.")
    try:
        connectors["TIKTOK"] = TikTokAdsConnector(cfg.tiktok)
    except Exception:  # noqa: BLE001
        logger.warning("Không khởi tạo được TikTok connector — skip TikTok trong report.")
    try:
        connectors["GOOGLE"] = GoogleAdsConnector(cfg.google_ads)
    except Exception:  # noqa: BLE001
        logger.warning("Không khởi tạo được Google Ads connector — skip Google trong report.")
    return connectors


def _compute_derived_metrics(agg: Dict[str, Any]) -> Dict[str, Any]:
    spend = agg["spend"]
    clicks = agg["clicks"]
    impressions = agg["impressions"]
    conversions = agg["conversions"]
    agg["ctr"] = round((clicks / impressions * 100), 2) if impressions else 0.0
    agg["cpc"] = round((spend / clicks), 2) if clicks else 0.0
    agg["cpa"] = round((spend / conversions), 2) if conversions else 0.0
    # ROAS cần giá trị revenue — pipeline này không có dữ liệu revenue trực tiếp từ
    # Ads API nên để 0 nếu không có; có thể mở rộng join với dữ liệu đơn hàng thực tế.
    agg.setdefault("roas", 0.0)
    return agg


def main() -> int:
    cfg = load_config()
    sheet_handler = GoogleSheetHandler(
        cfg.google_workspace,
        cfg.pipeline.ad_queue_tab,
        cfg.pipeline.ad_registry_tab,
        cfg.pipeline.weekly_reports_tab,
    )
    llm = LLMContentGenerator(cfg.llm)
    notifier = TelegramNotifier(cfg.notifier)

    try:
        registry = sheet_handler.read_registry()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi đọc Ad_Registry")
        notifier.send_error(JOB_NAME, f"Không đọc được Ad_Registry: {exc}")
        return 1

    if not registry:
        logger.info("Ad_Registry trống — không có dữ liệu để báo cáo.")
        return 0

    grouped_by_platform = _group_registry_by_platform(registry)
    connectors = _build_connectors(cfg)
    lookback_days = cfg.pipeline.report_lookback_days
    week_range = _week_range_label(lookback_days)

    # topic_platform_key -> aggregate metrics
    aggregates: Dict[tuple, Dict[str, Any]] = {}
    ad_id_to_topic: Dict[str, str] = {e["Ad_ID"]: e.get("Topic_Group", "") for e in registry if e.get("Ad_ID")}

    for platform, entries in grouped_by_platform.items():
        connector = connectors.get(platform)
        if connector is None:
            continue
        ad_ids = [e["Ad_ID"] for e in entries]
        try:
            insights = connector.get_insights(ad_ids, lookback_days)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Lỗi lấy insights từ %s", platform)
            notifier.send_error(JOB_NAME, f"[{platform}] Lỗi lấy insights: {exc}")
            continue

        for row in insights:
            topic_group = ad_id_to_topic.get(row["ad_id"], "Unknown")
            key = (platform, topic_group)
            agg = aggregates.setdefault(
                key, {"platform": platform, "topic_group": topic_group, "spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0}
            )
            agg["spend"] += row.get("spend", 0.0)
            agg["impressions"] += row.get("impressions", 0)
            agg["clicks"] += row.get("clicks", 0)
            agg["conversions"] += row.get("conversions", 0)

    if not aggregates:
        logger.info("Không lấy được insights nào tuần này — có thể chưa có chi tiêu.")
        notifier.send_message(f"📊 *Báo cáo tuần {week_range}*: chưa có dữ liệu chi tiêu để báo cáo.")
        return 0

    metrics_table: List[Dict[str, Any]] = []
    weekly_report_rows: List[Dict[str, Any]] = []
    for agg in aggregates.values():
        agg = _compute_derived_metrics(agg)
        row_record = {
            "Week_Range": week_range,
            "Platform": agg["platform"],
            "Topic_Group": agg["topic_group"],
            "Spend": round(agg["spend"], 2),
            "Impressions": agg["impressions"],
            "Clicks": agg["clicks"],
            "CTR": agg["ctr"],
            "CPC": agg["cpc"],
            "Conversions": agg["conversions"],
            "CPA": agg["cpa"],
            "ROAS": agg["roas"],
        }
        metrics_table.append(row_record)
        weekly_report_rows.append(row_record)

    try:
        sheet_handler.append_weekly_report_rows(weekly_report_rows)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi ghi Weekly_Reports")
        notifier.send_error(JOB_NAME, f"Không ghi được Weekly_Reports: {exc}")

    try:
        insight_markdown = llm.generate_weekly_insight(metrics_table)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lỗi sinh nhận xét LLM cho weekly report")
        insight_markdown = f"_Không sinh được nhận xét tự động: {exc}_"

    report_text = f"*Khoảng thời gian:* {week_range}\n\n{insight_markdown}"
    notifier.send_weekly_report(report_text)
    logger.info("Hoàn tất weekly report cho khoảng %s.", week_range)
    return 0


if __name__ == "__main__":
    sys.exit(main())
