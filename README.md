# Omni-Channel Auto Ads & Weekly Reporter (Meta, TikTok, Google Ads) + Organic Poster

Hệ thống gồm **2 luồng song song**: (1) tự động hóa lên chiến dịch quảng cáo từ
Media + Prompt theo nhóm chủ đề, tổng hợp báo cáo hiệu suất hàng tuần; (2) tự
động **đăng organic** (không qua Ads) lên chính kênh YouTube/Facebook/Instagram/
TikTok của mình. Vận hành hoàn toàn bằng **Python + GitHub Actions + Google
Workspace** — không tốn chi phí server.

## 1. Cấu trúc thư mục

```text
ai-marketing-nu-apartment/
├── .github/workflows/
│   ├── deploy_ads.yml        # Cron 30 phút/lần — quét Ad_Queue và lên Ad
│   ├── weekly_report.yml     # Cron 08:00 Thứ 2 hàng tuần (UTC+7) — báo cáo Ads
│   └── organic_post.yml      # Cron mỗi giờ — quét Content_Queue, đăng organic
├── config/
│   ├── settings.py           # Load env vars + cấu hình mặc định (lazy — chỉ validate nhóm thực dùng)
│   └── prompts.py            # System prompt JSON schema cho LLM (Ad copy + Organic caption)
├── core/
│   ├── gdrive_handler.py     # Tải/dọn media tạm từ Google Drive
│   ├── gsheet_handler.py     # Đọc/ghi 5 tab: Ad_Queue/Ad_Registry/Weekly_Reports + Content_Queue/Content_Registry
│   ├── llm_generator.py      # Sinh ad copy / organic caption (OpenAI/Gemini) + UTM + đặt tên Ad
│   ├── notifier.py           # Gửi cảnh báo lỗi + báo cáo qua Telegram
│   └── retry.py              # Decorator retry + exponential backoff dùng chung
├── platforms/
│   ├── base_ads.py           # Abstract Base Class cho connector Ads
│   ├── meta_ads.py           # Meta Marketing API (Ads)
│   ├── tiktok_ads.py         # TikTok Business API (Ads)
│   ├── google_ads.py         # Google Ads API + YouTube upload (asset cho Ads)
│   └── organic/              # Connector đăng ORGANIC — không qua Ads
│       ├── base_organic.py
│       ├── youtube_organic.py    # YouTube Data API v3 (videos.insert, public/unlisted/private)
│       ├── facebook_organic.py   # Facebook Graph API (/photos, /videos lên Page)
│       ├── instagram_organic.py  # Instagram Graph API Content Publishing (cần media_url công khai)
│       └── tiktok_organic.py     # TikTok Content Posting API (Direct Post, FILE_UPLOAD)
├── jobs/
│   ├── run_deploy_ads.py     # Entrypoint workflow lên Ad
│   ├── run_weekly_report.py  # Entrypoint workflow báo cáo tuần
│   └── run_organic_post.py   # Entrypoint workflow đăng organic
├── scripts/
│   └── get_google_refresh_token.py  # Chạy LOCAL 1 lần để lấy OAuth refresh token (thay thế Service Account key)
├── .env.example
├── requirements.txt
└── README.md
```

## 2. Google Sheets Schema

Tạo 1 Google Sheet gồm 5 tab (xem chi tiết từng cột trong `core/gsheet_handler.py`):

- **`Ad_Queue`** / **`Ad_Registry`** / **`Weekly_Reports`**: luồng Ads (xem `HUONG_DAN_SU_DUNG.md` mục 2).
- **`Content_Queue`**: hàng đợi đăng organic — nhập dòng mới với `Pipeline_Status = READY`, không cần Campaign_IDs/budget.
- **`Content_Registry`**: hệ thống tự ghi link bài đã đăng (`Post_URL`) sau khi thành công.

## 3. Cài đặt

### Bước 1 — Xác thực Google Sheets/Drive & chia sẻ quyền
Chọn 1 trong 2 cách (chi tiết ở `HUONG_DAN_SU_DUNG.md` mục 2.2):
- **Service Account** (đơn giản nhất): tạo Service Account trên Google Cloud,
  bật Google Sheets API + Google Drive API, tải key JSON, share Sheet/Drive
  cho email service account quyền **Editor**.
- **OAuth Client + refresh token** (dùng khi org GCP chặn tạo service account
  key, ví dụ policy `iam.disableServiceAccountKeyCreation`): tạo OAuth Client
  (Desktop app), chạy `python scripts/get_google_refresh_token.py` một lần để
  lấy refresh token, rồi share Sheet/Drive cho chính tài khoản Google đó.

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
- **Organic — Instagram**: Graph API chỉ nhận `image_url`/`video_url` công khai, không
  nhận upload file nhị phân — `Media_Drive_URL` trong `Content_Queue` phải được share
  "Anyone with the link", không chỉ share riêng cho Service Account.
- **Organic — TikTok**: dùng app "Login Kit + Content Posting API" **riêng biệt**,
  khác hoàn toàn app TikTok Business/Marketing API dùng cho Ads. Trước khi được
  TikTok audit, chỉ đăng được ở chế độ riêng tư (`SELF_ONLY`).
- **Organic — YouTube/TikTok**: chỉ hỗ trợ `Media_Type = VIDEO` (không hỗ trợ IMAGE).
- Chi tiết đăng ký API + hướng dẫn dùng hàng ngày cho luồng organic: xem mục 6b
  trong `HUONG_DAN_SU_DUNG.md`.
