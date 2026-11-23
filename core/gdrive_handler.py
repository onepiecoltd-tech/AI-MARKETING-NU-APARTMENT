"""
core/gdrive_handler.py
-----------------------
Tải file tạm từ Google Drive & dọn dẹp ổ đĩa runner.

Tuân thủ mục 3.4 Master Spec:
- Tuyệt đối không commit media vào repo.
- Chỉ tải từng file một theo dòng đang xử lý.
- Xóa ngay lập tức (os.remove) trong khối finally sau khi dùng xong.
- Giới hạn kích thước file tải về tối đa (mặc định 500MB), kiểm tra metadata
  trước khi download.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
import json

from config.settings import GoogleWorkspaceConfig, PipelineConfig
from core.retry import is_rate_limit_or_transient, retry_with_backoff

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

_DRIVE_FILE_ID_PATTERNS = (
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"id=([a-zA-Z0-9_-]+)"),
    re.compile(r"^([a-zA-Z0-9_-]{20,})$"),  # bare file id
)


def extract_drive_file_id(url_or_id: str) -> str:
    """Trích xuất Drive file ID từ URL chia sẻ hoặc chấp nhận ID thuần."""
    for pattern in _DRIVE_FILE_ID_PATTERNS:
        match = pattern.search(url_or_id.strip())
        if match:
            return match.group(1)
    raise ValueError(f"Không trích xuất được Google Drive file ID từ: {url_or_id}")


def _build_credentials(service_account_json: str):
    """service_account_json có thể là nội dung JSON thô hoặc đường dẫn file."""
    candidate_path = Path(service_account_json)
    if candidate_path.exists():
        info = json.loads(candidate_path.read_text(encoding="utf-8"))
    else:
        info = json.loads(service_account_json)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


class GoogleDriveHandler:
    """Wrapper quản lý download/cleanup media từ Google Drive."""

    def __init__(self, workspace_cfg: GoogleWorkspaceConfig, pipeline_cfg: PipelineConfig):
        self._cfg = workspace_cfg
        self._pipeline_cfg = pipeline_cfg
        creds = _build_credentials(workspace_cfg.service_account_json)
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._pipeline_cfg.media_cache_dir.mkdir(parents=True, exist_ok=True)

    @retry_with_backoff(max_retries=4, base_delay=2.0, should_retry=is_rate_limit_or_transient)
    def _get_metadata(self, file_id: str) -> dict:
        return (
            self._service.files()
            .get(fileId=file_id, fields="id,name,size,mimeType")
            .execute()
        )

    def _validate_size(self, metadata: dict) -> None:
        size_bytes = int(metadata.get("size", 0))
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > self._pipeline_cfg.max_media_file_mb:
            raise ValueError(
                f"File '{metadata.get('name')}' có kích thước {size_mb:.1f}MB, "
                f"vượt giới hạn {self._pipeline_cfg.max_media_file_mb}MB cho phép."
            )

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=is_rate_limit_or_transient)
    def _download_to_path(self, file_id: str, dest_path: Path) -> None:
        request = self._service.files().get_media(fileId=file_id)
        with io.FileIO(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.info(
                        "Đang tải %s: %d%%", dest_path.name, int(status.progress() * 100)
                    )

    @contextmanager
    def download_temp(self, media_url_or_id: str, row_id: str) -> Iterator[Path]:
        """
        Context manager: tải media về /tmp/media_cache/<row_id>_<name>, yield
        Path để các connector dùng, và LUÔN xóa file trong finally — dù upload
        thành công hay lỗi — để không bao giờ tràn 14GB SSD của GitHub Runner.
        """
        file_id = extract_drive_file_id(media_url_or_id)
        metadata = self._get_metadata(file_id)
        self._validate_size(metadata)

        safe_name = re.sub(r"[^\w.\-]", "_", metadata.get("name", f"{file_id}"))
        dest_path = self._pipeline_cfg.media_cache_dir / f"{row_id}_{safe_name}"

        try:
            logger.info("Tải media '%s' (row=%s) từ Drive...", metadata.get("name"), row_id)
            self._download_to_path(file_id, dest_path)
            yield dest_path
        finally:
            try:
                if dest_path.exists():
                    os.remove(dest_path)
                    logger.info("Đã xóa file tạm: %s", dest_path)
            except OSError as exc:  # pragma: no cover
                logger.warning("Không thể xóa file tạm %s: %s", dest_path, exc)

    def get_mime_type(self, media_url_or_id: str) -> Optional[str]:
        file_id = extract_drive_file_id(media_url_or_id)
        metadata = self._get_metadata(file_id)
        return metadata.get("mimeType")
