"""
core/llm_generator.py
-----------------------
Gọi OpenAI hoặc Gemini để sinh Ad Copy theo Strict JSON Schema, và tự động
gắn UTM vào Landing_Page_URL theo quy ước đặt tên ở mục 3.2 Master Spec.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from config.prompts import build_content_prompt, build_organic_content_prompt, build_report_analysis_prompt
from config.settings import LLMConfig
from core.retry import is_rate_limit_or_transient, retry_with_backoff

logger = logging.getLogger(__name__)

# Giới hạn ký tự cứng theo spec — dùng để validate/trim output của LLM, đảm bảo
# không bao giờ vượt policy của từng nền tảng cho dù LLM có "quên" quy tắc.
_LIMITS = {
    "meta.headline": 40,
    "meta.description": 30,
    "tiktok.ad_text": 100,
    "google.headlines": 30,
    "google.long_headline": 90,
    "google.descriptions": 90,
    "organic.youtube_title": 100,
    "organic.tiktok_caption": 150,
}
_VALID_CTAS = {"LEARN_MORE", "SHOP_NOW", "SIGN_UP"}


class LLMGenerationError(RuntimeError):
    pass


class LLMContentGenerator:
    def __init__(self, cfg: LLMConfig):
        self._cfg = cfg
        self._provider = cfg.provider.lower().strip()
        if self._provider == "openai":
            from openai import OpenAI  # import cục bộ để không bắt buộc cài cả 2 SDK

            self._client = OpenAI(api_key=cfg.api_key)
        elif self._provider == "gemini":
            import google.generativeai as genai

            genai.configure(api_key=cfg.api_key)
            self._client = genai.GenerativeModel(cfg.model_gemini)
        else:
            raise ValueError(f"LLM_PROVIDER không hỗ trợ: {cfg.provider} (chỉ nhận 'openai' hoặc 'gemini')")

    # ------------------------------------------------------------- calls
    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=is_rate_limit_or_transient)
    def _call_openai(self, system_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._cfg.model_openai,
            messages=[
                {"role": "system", "content": "Bạn chỉ trả về JSON hợp lệ, không có text thừa."},
                {"role": "user", "content": system_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        return response.choices[0].message.content or "{}"

    @retry_with_backoff(max_retries=4, base_delay=3.0, should_retry=is_rate_limit_or_transient)
    def _call_gemini(self, system_prompt: str) -> str:
        response = self._client.generate_content(
            system_prompt,
            generation_config={"response_mime_type": "application/json", "temperature": 0.7},
        )
        return response.text or "{}"

    def _call_llm(self, prompt: str) -> str:
        if self._provider == "openai":
            return self._call_openai(prompt)
        return self._call_gemini(prompt)

    # ---------------------------------------------------------- public API
    def generate_ad_content(self, topic_group: str, topic_prompt: str, platforms: List[str]) -> Dict[str, Any]:
        prompt = build_content_prompt(topic_group, topic_prompt, platforms)
        raw_text = self._call_llm(prompt)
        data = self._parse_json_safely(raw_text)
        data = self._validate_and_trim(data, platforms)
        return data

    def generate_organic_content(self, topic_group: str, topic_prompt: str, platforms: List[str]) -> Dict[str, Any]:
        """Sinh nội dung đăng ORGANIC (YouTube/Facebook/Instagram/TikTok) — khác
        generate_ad_content() ở giọng văn tự nhiên + schema riêng (title/description/
        tags cho YouTube, hashtags cho IG/TikTok), không có call_to_action/landing page."""
        prompt = build_organic_content_prompt(topic_group, topic_prompt, platforms)
        raw_text = self._call_llm(prompt)
        data = self._parse_json_safely(raw_text)
        data = self._validate_and_trim_organic(data, platforms)
        return data

    def generate_weekly_insight(self, metrics_table: List[Dict[str, Any]]) -> str:
        prompt = build_report_analysis_prompt(metrics_table)
        raw_text = self._call_llm(prompt)
        # Đây là output dạng Markdown tự do (không phải JSON), trả thẳng về.
        return raw_text.strip()

    # --------------------------------------------------------------- utils
    @staticmethod
    def _parse_json_safely(raw_text: str) -> Dict[str, Any]:
        text = raw_text.strip()
        # Một số model vẫn bọc ```json ... ``` dù đã yêu cầu không — tự bóc ra.
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMGenerationError(f"LLM trả về JSON không hợp lệ: {exc}\nRaw: {raw_text[:500]}")

    @staticmethod
    def _trim(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

    def _validate_and_trim(self, data: Dict[str, Any], platforms: List[str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        if "META" in platforms and "meta" in data:
            meta = data["meta"]
            cta = str(meta.get("call_to_action", "LEARN_MORE")).upper()
            result["meta"] = {
                "primary_text": list(meta.get("primary_text", []))[:3],
                "headline": self._trim(str(meta.get("headline", "")), _LIMITS["meta.headline"]),
                "description": self._trim(str(meta.get("description", "")), _LIMITS["meta.description"]),
                "call_to_action": cta if cta in _VALID_CTAS else "LEARN_MORE",
            }

        if "TIKTOK" in platforms and "tiktok" in data:
            tiktok = data["tiktok"]
            cta = str(tiktok.get("call_to_action", "LEARN_MORE")).upper()
            result["tiktok"] = {
                "ad_text": self._trim(str(tiktok.get("ad_text", "")), _LIMITS["tiktok.ad_text"]),
                "call_to_action": cta if cta in _VALID_CTAS else "LEARN_MORE",
            }

        if "GOOGLE" in platforms and "google" in data:
            google = data["google"]
            headlines = [self._trim(str(h), _LIMITS["google.headlines"]) for h in google.get("headlines", [])][:5]
            descriptions = [
                self._trim(str(d), _LIMITS["google.descriptions"]) for d in google.get("descriptions", [])
            ][:3]
            result["google"] = {
                "headlines": headlines if len(headlines) >= 3 else headlines + [headlines[-1] if headlines else "Quảng cáo"] * (3 - len(headlines)),
                "long_headline": self._trim(str(google.get("long_headline", "")), _LIMITS["google.long_headline"]),
                "descriptions": descriptions if len(descriptions) >= 2 else descriptions + [descriptions[-1] if descriptions else "Tìm hiểu thêm."] * (2 - len(descriptions)),
                "business_name": str(google.get("business_name", "")).strip() or "Doanh nghiệp",
            }

        return result

    def _validate_and_trim_organic(self, data: Dict[str, Any], platforms: List[str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        if "YOUTUBE" in platforms and "youtube" in data:
            yt = data["youtube"]
            result["youtube"] = {
                "title": self._trim(str(yt.get("title", "")), _LIMITS["organic.youtube_title"]),
                "description": str(yt.get("description", "")).strip(),
                "tags": [str(t).strip() for t in yt.get("tags", []) if str(t).strip()][:15],
            }

        if "FACEBOOK" in platforms and "facebook" in data:
            fb = data["facebook"]
            result["facebook"] = {"caption": str(fb.get("caption", "")).strip()}

        if "INSTAGRAM" in platforms and "instagram" in data:
            ig = data["instagram"]
            hashtags = [str(h).strip() for h in ig.get("hashtags", []) if str(h).strip()][:8]
            result["instagram"] = {
                "caption": str(ig.get("caption", "")).strip(),
                "hashtags": hashtags,
            }

        if "TIKTOK" in platforms and "tiktok" in data:
            tt = data["tiktok"]
            hashtags = [str(h).strip() for h in tt.get("hashtags", []) if str(h).strip()][:8]
            result["tiktok"] = {
                "caption": self._trim(str(tt.get("caption", "")), _LIMITS["organic.tiktok_caption"]),
                "hashtags": hashtags,
            }

        return result


def build_ad_name(topic_group: str, row_id: str, created_date: datetime | None = None) -> str:
    """Quy ước đặt tên: [{Topic_Group}]_{YYYYMMDD}_{Row_ID}"""
    date_str = (created_date or datetime.utcnow()).strftime("%Y%m%d")
    return f"[{topic_group}]_{date_str}_{row_id}"


def compose_caption(caption: str, hashtags: List[str]) -> str:
    """Nối caption + hashtags thành 1 chuỗi để đăng (Instagram/TikTok)."""
    tags = [h if h.startswith("#") else f"#{h}" for h in hashtags]
    if not tags:
        return caption
    return f"{caption}\n\n{' '.join(tags)}"


def build_utm_url(landing_page_url: str, platform: str, topic_group: str, row_id: str) -> str:
    """Tự động gắn UTM:
    ?utm_source={platform}&utm_medium=paid&utm_campaign={topic_group}&utm_content={row_id}
    Giữ nguyên query string gốc (nếu có) và nối thêm UTM.
    """
    parsed = urlparse(landing_page_url)
    existing_qs = dict(parse_qsl(parsed.query))
    existing_qs.update(
        {
            "utm_source": platform.lower(),
            "utm_medium": "paid",
            "utm_campaign": topic_group,
            "utm_content": row_id,
        }
    )
    new_query = urlencode(existing_qs)
    return urlunparse(parsed._replace(query=new_query))
