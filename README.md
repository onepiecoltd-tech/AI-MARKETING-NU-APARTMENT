# Omni-Channel Auto Ads & Weekly Reporter (Meta, TikTok, Google Ads)

Hệ thống tự động hóa lên chiến dịch quảng cáo từ Media + Prompt theo nhóm chủ đề,
và tổng hợp báo cáo hiệu suất hàng tuần đa nền tảng. Vận hành hoàn toàn bằng
**Python + GitHub Actions + Google Workspace** — không tốn chi phí server.

## 1. Cấu trúc thư mục

```text
auto-ads-pipeline/
├── .github/workflows/
│   ├── deploy_ads.yml        # Cron 30 phút/lần — quét Ad_Queue và lên Ad
│   └── weekly_report.yml     # Cron 08:00 Thứ 2 hàng tuần (UTC+7) — báo cáo
├── config/
│   ├── settings.py           # Load env vars + cấu hình mặc định
│   └── prompts.py            # System prompt chuẩn hóa JSON schema cho LLM
├── core/
│   ├── gdrive_handler.py     # Tải/dọn media tạm từ Google Drive
│   ├── gsheet_handler.py     # Đọc/ghi 3 tab: Ad_Queue, Ad_Registry, Weekly_Reports
│   ├── llm_generator.py      # Sinh ad copy (OpenAI/Gemini) + gắn UTM + đặt tên Ad
│   ├── notifier.py           # Gửi cảnh báo lỗi + báo cáo qua Telegram
│   └── retry.py              # Decorator retry + exponential backoff dùng chung
├── platforms/
│   ├── base_ads.py           # Abstract Base Class cho mọi connector
│   ├── meta_ads.py           # Meta Marketing API (facebook-business SDK)
│   ├── tiktok_ads.py         # TikTok Business API (REST + requests)
│   └── google_ads.py         # Google Ads API (google-ads SDK) + YouTube upload
├── jobs/
│   ├── run_deploy_ads.py     # Entrypoint workflow lên Ad
│   └── run_weekly_report.py  # Entrypoint workflow báo cáo tuần
├── .env.example
├── requirements.txt
└── README.md
```

## 2. Google Sheets Schema

Tạo 1 Google Sheet gồm 3 tab với đúng tên và thứ tự cột sau (xem chi tiết từng
cột trong `core/gsheet_handler.py` — `AD_QUEUE_COLUMNS`, `AD_REGISTRY_COLUMNS`,
`WEEKLY_REPORT_COLUMNS`):

- **`Ad_Queue`**: hàng đợi lên Ad — nhập dòng mới với `Pipeline_Status = READY`.
- **`Ad_Registry`**: hệ thống tự ghi sau khi tạo Ad thành công — đối soát & dùng cho báo cáo.
- **`Weekly_Reports`**: lịch sử snapshot số liệu mỗi tuần.

## 3. Cài đặt

### Bước 1 — Chuẩn bị Service Account & chia sẻ quyền
1. Tạo Service Account trên Google Cloud, bật Google Sheets API + Google Drive API.
2. Share Google Sheet và thư mục Drive chứa media cho email của Service Account với quyền **Editor**.

### Bước 2 — Cấu hình GitHub Secrets
Vào **Repo → Settings → Secrets and variables → Actions**, thêm toàn bộ biến
liệt kê trong `.env.example` (Google, LLM, Meta, TikTok, Google Ads, Telegram).

### Bước 3 — Test local (tùy chọn)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # rồi điền giá trị thật
python -m jobs.run_deploy_ads
python -m jobs.run_weekly_report
```

### Bước 4 — Chạy thử trên GitHub Actions
1. Nhập 1 dòng mẫu vào `Ad_Queue` với `Initial_Status=PAUSED`, `Pipeline_Status=READY`.
2. Vào tab **Actions → Deploy Ads → Run workflow** để kiểm tra toàn bộ luồng.
3. Kiểm tra `Ad_Registry` và Ad trên trình quản lý quảng cáo (ở trạng thái PAUSED để review trước khi ACTIVE).

## 4. Giới hạn & lưu ý vận hành

- **Dung lượng Runner**: media luôn được tải vào `/tmp/media_cache/`, xử lý từng
  dòng một và **xóa ngay trong khối `finally`** sau khi upload — không bao giờ
  commit media vào repo, không tràn 14GB SSD.
- **Giới hạn file**: tối đa `MAX_MEDIA_FILE_MB` (mặc định 500MB), được kiểm tra
  qua Drive metadata trước khi tải.
- **Retry**: mọi lời gọi API (Meta/TikTok/Google Ads/Drive/Sheets/LLM) đều có
  retry + exponential backoff + jitter (`core/retry.py`), tối đa `MAX_RETRIES` lần.
- **Google Ads video**: Google Ads yêu cầu video phải host trên YouTube — pipeline
  tự upload Unlisted qua YouTube Data API v3 nếu `YOUTUBE_REFRESH_TOKEN` được cấu hình.
- **An toàn khi lỗi**: lỗi ở 1 dòng/1 platform không chặn các dòng/platform khác;
  trạng thái và `Error_Log` được ghi lại chi tiết vào `Ad_Queue`, kèm cảnh báo Telegram.
- **ROAS**: Ads API không trả về revenue trực tiếp nên `ROAS` mặc định là 0 trong
  `Weekly_Reports` — nối thêm dữ liệu đơn hàng thực tế (ví dụ từ hệ thống CRM/đơn hàng)
  nếu cần tính ROAS chính xác.
