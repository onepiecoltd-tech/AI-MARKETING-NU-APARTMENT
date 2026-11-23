"""
platforms/organic/base_organic.py
------------------------------------
Abstract Base Class cho các connector đăng ORGANIC (không qua Ads) lên kênh/
trang/channel của chính mình. Khác platforms/base_ads.py: không có khái niệm
campaign/adset/adgroup — chỉ cần media + caption, đăng thẳng lên Page/Channel/
Account cố định (ID lấy từ config).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class OrganicPostResult:
    platform: str
    post_id: str
    post_url: str
    raw_response: Dict[str, Any]


class OrganicPostConnector(ABC):
    platform_name: str = "BASE"

    @abstractmethod
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
        """Đăng 1 bài organic (ảnh hoặc video) lên nền tảng, trả về kết quả
        chuẩn hóa (post_id + post_url thật để lưu vào Content_Registry)."""
        raise NotImplementedError
