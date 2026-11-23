"""
core/gsheet_handler.py
-----------------------
Đọc/ghi Google Sheets (Ad_Queue, Ad_Registry, Weekly_Reports) theo schema
mục 3.1 Master Spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config.settings import GoogleWorkspaceConfig
from core.gdrive_handler import _build_credentials  # reuse credential loader
from core.retry import is_rate_limit_or_transient, retry_with_backoff

logger = logging.getLogger(__name__)

AD_QUEUE_COLUMNS = [
    "Row_ID",
    "Topic_Group",
    "Topic_Prompt",
    "Media_Drive_URL",
    "Media_Type",
    "Platforms",
    "Campaign_IDs",
    "Landing_Page_URL",
    "Initial_Status",
    "Pipeline_Status",
    "Created_Ad_IDs",
    "Error_Log",
]

AD_REGISTRY_COLUMNS = [
    "Created_Date",
    "Row_ID",
    "Topic_Group",
    "Platform",
    "Ad_ID",
    "Ad_Name",
    "Generated_Headline",
    "Generated_Primary_Text",
]

WEEKLY_REPORT_COLUMNS = [
    "Week_Range",
    "Platform",
    "Topic_Group",
    "Spend",
    "Impressions",
    "Clicks",
    "CTR",
    "CPC",
    "Conversions",
    "CPA",
    "ROAS",
]

# Content_Queue: đăng ORGANIC (không qua Ads) lên chính kênh/trang/channel của
# mình. Khác Ad_Queue: KHÔNG có Campaign_IDs/Initial_Status (không có khái
# niệm AdSet/AdGroup/budget) — chỉ có 1 Page/Channel/IG Account cố định lấy từ
# config (FB_PAGE_ID, IG_BUSINESS_ACCOUNT_ID, YouTube channel qua OAuth token).
CONTENT_QUEUE_COLUMNS = [
    "Row_ID",
    "Topic_Group",
    "Topic_Prompt",
    "Media_Drive_URL",
    "Media_Type",
    "Platforms",
    "Caption_Override",
    "Visibility",
    "Pipeline_Status",
    "Posted_URLs",
    "Error_Log",
]

CONTENT_REGISTRY_COLUMNS = [
    "Posted_Date",
    "Row_ID",
    "Topic_Group",
    "Platform",
    "Post_ID",
    "Post_URL",
    "Caption",
]

PIPELINE_STATUS_READY = "READY"
PIPELINE_STATUS_PROCESSING = "PROCESSING"
PIPELINE_STATUS_DONE = "DONE"
PIPELINE_STATUS_ERROR = "ERROR"


@dataclass
class AdQueueRow:
    row_index: int  # 1-based index tính cả header, dùng để update đúng dòng
    row_id: str
    topic_group: str
    topic_prompt: str
    media_drive_url: str
    media_type: str
    platforms: List[str]
    campaign_ids: Dict[str, str]
    landing_page_url: str
    initial_status: str
    pipeline_status: str
    created_ad_ids: Dict[str, str] = field(default_factory=dict)
    error_log: str = ""


@dataclass
class ContentQueueRow:
    row_index: int
    row_id: str
    topic_group: str
    topic_prompt: str
    media_drive_url: str
    media_type: str
    platforms: List[str]
    caption_override: str
    visibility: str
    pipeline_status: str
    posted_urls: Dict[str, str] = field(default_factory=dict)
    error_log: str = ""


class GoogleSheetHandler:
    def __init__(
        self,
        cfg: GoogleWorkspaceConfig,
        ad_queue_tab: str,
        ad_registry_tab: str,
        weekly_reports_tab: str,
        content_queue_tab: str = "Content_Queue",
        content_registry_tab: str = "Content_Registry",
    ):
        self._cfg = cfg
        self.ad_queue_tab = ad_queue_tab
        self.ad_registry_tab = ad_registry_tab
        self.weekly_reports_tab = weekly_reports_tab
        self.content_queue_tab = content_queue_tab
        self.content_registry_tab = content_registry_tab
        creds = _build_credentials(cfg.service_account_json)
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # ---------------------------------------------------------------- utils
    @retry_with_backoff(max_retries=4, base_delay=2.0, should_retry=is_rate_limit_or_transient)
    def _get_values(self, range_: str) -> List[List[Any]]:
        result = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self._cfg.sheet_id, range=range_)
            .execute()
        )
        return result.get("values", [])

    @retry_with_backoff(max_retries=4, base_delay=2.0, should_retry=is_rate_limit_or_transient)
    def _update_values(self, range_: str, values: List[List[Any]]) -> None:
        self._service.spreadsheets().values().update(
            spreadsheetId=self._cfg.sheet_id,
            range=range_,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    @retry_with_backoff(max_retries=4, base_delay=2.0, should_retry=is_rate_limit_or_transient)
    def _append_values(self, range_: str, values: List[List[Any]]) -> None:
        self._service.spreadsheets().values().append(
            spreadsheetId=self._cfg.sheet_id,
            range=range_,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()

    @staticmethod
    def _safe_json_loads(text: str) -> Dict[str, Any]:
        import json

        if not text or not text.strip():
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Không parse được JSON: %r — trả về {}", text)
            return {}

    # ------------------------------------------------------------ Ad_Queue
    def read_ready_rows(self) -> List[AdQueueRow]:
        """Đọc toàn bộ Ad_Queue, trả về các dòng có Pipeline_Status == READY."""
        raw = self._get_values(f"{self.ad_queue_tab}!A2:L")
        rows: List[AdQueueRow] = []
        for idx, raw_row in enumerate(raw, start=2):  # dòng 1 là header
            padded = raw_row + [""] * (len(AD_QUEUE_COLUMNS) - len(raw_row))
            record = dict(zip(AD_QUEUE_COLUMNS, padded))
            if record.get("Pipeline_Status", "").strip().upper() != PIPELINE_STATUS_READY:
                continue
            platforms = [p.strip().upper() for p in record.get("Platforms", "").split(",") if p.strip()]
            rows.append(
                AdQueueRow(
                    row_index=idx,
                    row_id=record.get("Row_ID", "").strip(),
                    topic_group=record.get("Topic_Group", "").strip(),
                    topic_prompt=record.get("Topic_Prompt", "").strip(),
                    media_drive_url=record.get("Media_Drive_URL", "").strip(),
                    media_type=record.get("Media_Type", "").strip().upper(),
                    platforms=platforms,
                    campaign_ids=self._safe_json_loads(record.get("Campaign_IDs", "")),
                    landing_page_url=record.get("Landing_Page_URL", "").strip(),
                    initial_status=record.get("Initial_Status", "PAUSED").strip().upper(),
                    pipeline_status=record.get("Pipeline_Status", "").strip().upper(),
                    created_ad_ids=self._safe_json_loads(record.get("Created_Ad_IDs", "")),
                    error_log=record.get("Error_Log", ""),
                )
            )
        logger.info("Tìm thấy %d dòng READY trong Ad_Queue.", len(rows))
        return rows

    def set_status(self, row: AdQueueRow, status: str, error_log: str = "") -> None:
        """
        Cập nhật cột Pipeline_Status (J) và Error_Log (L), giữ nguyên
        Created_Ad_IDs (K) hiện có của dòng — dùng khi chuyển sang
        PROCESSING hoặc khi set ERROR mà chưa tạo được Ad nào.
        """
        self._update_values(
            f"{self.ad_queue_tab}!J{row.row_index}:L{row.row_index}",
            [[status, _json_dumps(row.created_ad_ids), error_log]],
        )

    def set_created_ad_ids(self, row: AdQueueRow, created_ad_ids: Dict[str, str], status: str, error_log: str = "") -> None:
        """Ghi lại JSON Ad_ID đã tạo + status + error log vào đúng dòng."""
        self._update_values(
            f"{self.ad_queue_tab}!J{row.row_index}:L{row.row_index}",
            [[status, _json_dumps(created_ad_ids), error_log]],
        )

    # --------------------------------------------------------- Ad_Registry
    def append_registry_entries(self, entries: List[Dict[str, Any]]) -> None:
        if not entries:
            return
        values = [[e.get(col, "") for col in AD_REGISTRY_COLUMNS] for e in entries]
        self._append_values(f"{self.ad_registry_tab}!A:H", values)
        logger.info("Đã ghi %d dòng vào Ad_Registry.", len(entries))

    def read_registry(self) -> List[Dict[str, Any]]:
        raw = self._get_values(f"{self.ad_registry_tab}!A2:H")
        entries = []
        for raw_row in raw:
            padded = raw_row + [""] * (len(AD_REGISTRY_COLUMNS) - len(raw_row))
            entries.append(dict(zip(AD_REGISTRY_COLUMNS, padded)))
        return entries

    # ----------------------------------------------------- Weekly_Reports
    def append_weekly_report_rows(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = [[r.get(col, "") for col in WEEKLY_REPORT_COLUMNS] for r in rows]
        self._append_values(f"{self.weekly_reports_tab}!A:K", values)
        logger.info("Đã ghi %d dòng vào Weekly_Reports.", len(rows))

    # -------------------------------------------------------- Content_Queue
    def read_ready_content_rows(self) -> List[ContentQueueRow]:
        """Đọc Content_Queue, trả về các dòng Pipeline_Status == READY."""
        raw = self._get_values(f"{self.content_queue_tab}!A2:K")
        rows: List[ContentQueueRow] = []
        for idx, raw_row in enumerate(raw, start=2):
            padded = raw_row + [""] * (len(CONTENT_QUEUE_COLUMNS) - len(raw_row))
            record = dict(zip(CONTENT_QUEUE_COLUMNS, padded))
            if record.get("Pipeline_Status", "").strip().upper() != PIPELINE_STATUS_READY:
                continue
            platforms = [p.strip().upper() for p in record.get("Platforms", "").split(",") if p.strip()]
            rows.append(
                ContentQueueRow(
                    row_index=idx,
                    row_id=record.get("Row_ID", "").strip(),
                    topic_group=record.get("Topic_Group", "").strip(),
                    topic_prompt=record.get("Topic_Prompt", "").strip(),
                    media_drive_url=record.get("Media_Drive_URL", "").strip(),
                    media_type=record.get("Media_Type", "").strip().upper(),
                    platforms=platforms,
                    caption_override=record.get("Caption_Override", "").strip(),
                    visibility=record.get("Visibility", "PUBLIC").strip().upper() or "PUBLIC",
                    pipeline_status=record.get("Pipeline_Status", "").strip().upper(),
                    posted_urls=self._safe_json_loads(record.get("Posted_URLs", "")),
                    error_log=record.get("Error_Log", ""),
                )
            )
        logger.info("Tìm thấy %d dòng READY trong Content_Queue.", len(rows))
        return rows

    def set_content_status(self, row: ContentQueueRow, status: str, error_log: str = "") -> None:
        """Cập nhật Pipeline_Status (I) + Error_Log (K), giữ nguyên Posted_URLs (J)."""
        self._update_values(
            f"{self.content_queue_tab}!I{row.row_index}:K{row.row_index}",
            [[status, _json_dumps(row.posted_urls), error_log]],
        )

    def set_content_posted_urls(
        self, row: ContentQueueRow, posted_urls: Dict[str, str], status: str, error_log: str = ""
    ) -> None:
        """Ghi JSON Posted_URLs (link bài đăng thật trên từng nền tảng) + status + error log."""
        self._update_values(
            f"{self.content_queue_tab}!I{row.row_index}:K{row.row_index}",
            [[status, _json_dumps(posted_urls), error_log]],
        )

    # ----------------------------------------------------- Content_Registry
    def append_content_registry_entries(self, entries: List[Dict[str, Any]]) -> None:
        if not entries:
            return
        values = [[e.get(col, "") for col in CONTENT_REGISTRY_COLUMNS] for e in entries]
        self._append_values(f"{self.content_registry_tab}!A:G", values)
        logger.info("Đã ghi %d dòng vào Content_Registry.", len(entries))


def _json_dumps(data: Dict[str, Any]) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)
