"""
config/prompts.py
------------------
System prompt chuẩn hóa đầu ra JSON cho LLM Content Engine, theo đúng
Strict JSON Schema ở mục 3.2 của Master Spec.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# JSON schema mẫu để LLM bám theo định dạng đầu ra (dùng trong system prompt).
OUTPUT_SCHEMA_EXAMPLE: Dict[str, Any] = {
    "meta": {
        "primary_text": ["<variant 1>", "<variant 2>", "<variant 3>"],
        "headline": "<= 40 ký tự",
        "description": "<= 30 ký tự",
        "call_to_action": "LEARN_MORE | SHOP_NOW | SIGN_UP",
    },
    "tiktok": {
        "ad_text": "<= 100 ký tự, tự nhiên chuẩn TikTok",
        "call_to_action": "LEARN_MORE | SHOP_NOW | SIGN_UP",
    },
    "google": {
        "headlines": ["<h1 <=30 ký tự>", "<h2>", "<h3>"],
        "long_headline": "<= 90 ký tự",
        "descriptions": ["<d1 <=90 ký tự>", "<d2>"],
        "business_name": "<tên doanh nghiệp>",
    },
}

SYSTEM_PROMPT_TEMPLATE = """Bạn là chuyên gia copywriting quảng cáo đa nền tảng (Meta, TikTok, Google Ads).
Nhiệm vụ: sinh nội dung quảng cáo CHỈ cho các nền tảng được yêu cầu, dựa trên:
- Nhóm chủ đề (Topic_Group): {topic_group}
- Ý tưởng / góc tiếp cận / sản phẩm (Topic_Prompt): {topic_prompt}
- Các nền tảng cần sinh nội dung (Platforms): {platforms}

QUY TẮC BẮT BUỘC:
1. Chỉ trả về JSON hợp lệ, KHÔNG kèm giải thích, KHÔNG markdown code fence.
2. Chỉ bao gồm các khóa nền tảng nằm trong danh sách Platforms được yêu cầu.
3. Tuân thủ nghiêm ngặt giới hạn ký tự cho từng trường:
   - Meta: primary_text (tối đa 3 biến thể, mỗi biến thể là 1 câu hoàn chỉnh,
     không giới hạn ký tự cứng nhưng nên súc tích), headline (<= 40 ký tự),
     description (<= 30 ký tự), call_to_action (một trong: LEARN_MORE, SHOP_NOW, SIGN_UP).
   - TikTok: ad_text (<= 100 ký tự, văn phong tự nhiên, bắt trend, đúng tông TikTok),
     call_to_action (một trong: LEARN_MORE, SHOP_NOW, SIGN_UP).
   - Google: headlines (3 đến 5 tiêu đề, mỗi tiêu đề <= 30 ký tự),
     long_headline (<= 90 ký tự), descriptions (2 đến 3 mô tả, mỗi mô tả <= 90 ký tự),
     business_name (tên doanh nghiệp/thương hiệu, ngắn gọn).
4. Văn phong: chân thật, đúng insight của Topic_Prompt, tránh sáo rỗng, tránh từ ngữ
   bị Ads Policy cấm (ví dụ: cam kết kết quả y tế tuyệt đối, so sánh tiêu cực đối thủ).
5. Giữ nguyên ngôn ngữ của Topic_Prompt (nếu Topic_Prompt viết tiếng Việt, nội dung ra tiếng Việt).

Cấu trúc JSON đầu ra tham khảo (CHỈ là ví dụ cấu trúc, không copy nội dung mẫu):
{schema_example}
"""


def build_content_prompt(topic_group: str, topic_prompt: str, platforms: List[str]) -> str:
    """Build system prompt hoàn chỉnh gửi cho LLM."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        topic_group=topic_group,
        topic_prompt=topic_prompt,
        platforms=", ".join(p.strip().upper() for p in platforms),
        schema_example=json.dumps(OUTPUT_SCHEMA_EXAMPLE, ensure_ascii=False, indent=2),
    )


REPORT_ANALYSIS_PROMPT_TEMPLATE = """Bạn là chuyên gia phân tích hiệu suất quảng cáo đa nền tảng.
Dưới đây là bảng số liệu hiệu suất 7 ngày gần nhất, đã gom theo Nhóm Chủ Đề (Topic_Group)
và theo Nền Tảng (Platform):

{metrics_table_json}

YÊU CẦU:
1. Chỉ ra Top 2 Topic_Group hiệu quả nhất (dựa trên CPA thấp / ROAS cao / CTR tốt),
   giải thích ngắn gọn vì sao (1 câu/nhóm).
2. Chỉ ra Top 2 Topic_Group cần tắt hoặc tối ưu lại (CPA cao, CTR thấp, Spend nhiều
   nhưng Conversions thấp), kèm gợi ý hành động cụ thể (1 câu/nhóm).
3. Viết nhận xét tổng quan 2-3 câu về xu hướng chung tuần này.
4. Trả lời bằng tiếng Việt, định dạng Markdown ngắn gọn, phù hợp để gửi qua Telegram.
Không lặp lại toàn bộ bảng số liệu, chỉ trích dẫn số liệu cần thiết để minh chứng.
"""


def build_report_analysis_prompt(metrics_table: List[Dict[str, Any]]) -> str:
    return REPORT_ANALYSIS_PROMPT_TEMPLATE.format(
        metrics_table_json=json.dumps(metrics_table, ensure_ascii=False, indent=2)
    )
