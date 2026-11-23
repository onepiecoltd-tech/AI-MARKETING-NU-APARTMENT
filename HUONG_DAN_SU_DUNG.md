# Hướng Dẫn Sử Dụng — Omni-Channel Auto Ads & Weekly Reporter

Tài liệu này hướng dẫn **từng bước** để đưa hệ thống vào vận hành thật: từ tạo
tài khoản dịch vụ Google, cấu hình API 3 nền tảng quảng cáo, đến cách dùng hàng
ngày (nhập 1 dòng → có Ad chạy tự động) và cách đọc báo cáo hàng tuần.

> Sơ đồ trực quan quy trình (có mô phỏng từng bước): xem lại artifact Claude đã
> gửi trong hội thoại — "Omni-Channel Auto Ads — Pipeline Prototype".

---

## 1. Chuẩn bị trước khi cài đặt

Cần có sẵn:
- [ ] Tài khoản Google Cloud (để tạo Service Account cho Drive + Sheets, và tùy chọn YouTube).
- [ ] Tài khoản quảng cáo đã duyệt trên **Meta Business Manager**, **TikTok Ads Manager**, **Google Ads**.
- [ ] 1 Bot Telegram (để nhận cảnh báo lỗi & báo cáo tuần).
- [ ] API Key của **OpenAI** hoặc **Google Gemini** (để sinh nội dung quảng cáo).
- [ ] Quyền Admin trên repo GitHub này (để cấu hình Secrets và theo dõi Actions).

---

## 2. Thiết lập Google Workspace (Sheets + Drive)

### 2.1. Tạo Google Sheet quản lý
Tạo 1 Google Sheet mới, đặt tên tùy ý (ví dụ `Ads_Control_Center`), tạo đúng 3 tab với **đúng tên** sau:

**Tab `Ad_Queue`** — hàng đợi lên Ad, dòng 1 là header với đúng 12 cột theo thứ tự:

| Row_ID | Topic_Group | Topic_Prompt | Media_Drive_URL | Media_Type | Platforms | Campaign_IDs | Landing_Page_URL | Initial_Status | Pipeline_Status | Created_Ad_IDs | Error_Log |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AD_001 | PainPoint_MatNgu | Quảng cáo trà thảo mộc cho dân văn phòng hay mất ngủ | https://drive.google.com/... | VIDEO | META, TIKTOK, GOOGLE | {"meta":"<adset_id>","tiktok":"<adgroup_id>","google":"<ad_group_id>"} | https://domain.com | PAUSED | READY | | |

**Tab `Ad_Registry`** — hệ thống tự ghi, chỉ cần tạo header dòng 1:
```
Created_Date | Row_ID | Topic_Group | Platform | Ad_ID | Ad_Name | Generated_Headline | Generated_Primary_Text
```

**Tab `Weekly_Reports`** — hệ thống tự ghi, chỉ cần tạo header dòng 1:
```
Week_Range | Platform | Topic_Group | Spend | Impressions | Clicks | CTR | CPC | Conversions | CPA | ROAS
```

Copy **Sheet ID** từ URL (`https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`) → dùng cho biến `GSHEET_ID`.

### 2.2. Tạo Service Account
1. Vào [Google Cloud Console](https://console.cloud.google.com/) → tạo project mới (hoặc dùng project có sẵn).
2. Bật 2 API: **Google Sheets API** và **Google Drive API** (và **YouTube Data API v3** nếu muốn tự động upload video Google Ads).
3. Vào **IAM & Admin → Service Accounts → Create Service Account**.
4. Sau khi tạo, vào tab **Keys → Add Key → Create new key → JSON** → tải file JSON về. Đây là giá trị cho biến `GCP_SERVICE_ACCOUNT_JSON`.
5. Mở file JSON, copy địa chỉ email dạng `xxx@xxx.iam.gserviceaccount.com`.

### 2.3. Chia sẻ quyền
- Mở Google Sheet vừa tạo → **Share** → dán email Service Account → chọn quyền **Editor**.
- Mở thư mục Google Drive chứa ảnh/clip quảng cáo → **Share** → dán email Service Account → quyền **Editor**.

---

## 3. Lấy API Keys cho từng nền tảng

### 3.1. Meta (Facebook/Instagram)
1. Vào [Meta for Developers](https://developers.facebook.com/) → tạo App loại **Business**.
2. Thêm sản phẩm **Marketing API**.
3. Lấy **Access Token** dài hạn (System User Token khuyến nghị, không hết hạn) có quyền `ads_management`.
4. Lấy **Ad Account ID** (dạng `act_xxxxxxxxx`) trong Business Manager.
5. Lấy **Page ID** của Fanpage sẽ đứng tên quảng cáo.

### 3.2. TikTok Ads
1. Vào [TikTok for Business API](https://business-api.tiktok.com/) → tạo App, kết nối với TikTok Ads Manager.
2. Lấy **Access Token** sau khi authorize App với tài khoản Advertiser.
3. Lấy **Advertiser ID**.
4. Lấy **Identity ID** (identity dùng để hiển thị tên thương hiệu trên ad — lấy qua API `/identity/get/` hoặc trong TikTok Ads Manager).

### 3.3. Google Ads
1. Đăng ký **Developer Token** tại [Google Ads API Center](https://ads.google.com/) (tài khoản MCC).
2. Tạo OAuth Client ID/Secret trong Google Cloud Console (loại **Desktop app**).
3. Dùng OAuth Playground hoặc script `google-ads` để lấy **Refresh Token** (chạy 1 lần, xem hướng dẫn chính thức của Google Ads API "Obtain a refresh token").
4. Lấy **Customer ID** (10 số, không gạch ngang khi điền vào biến môi trường — code tự loại bỏ dấu `-`).
5. (Tùy chọn) Nếu muốn tự động upload video lên YouTube cho Google Ads: lấy thêm **YouTube Refresh Token** với scope `youtube.upload`, dùng cùng OAuth Client ID/Secret ở trên.

### 3.4. Telegram Bot
1. Chat với **@BotFather** trên Telegram → `/newbot` → lấy **Bot Token**.
2. Thêm bot vào group/channel cần nhận báo cáo, hoặc chat trực tiếp với bot.
3. Lấy **Chat ID**: mở `https://api.telegram.org/bot<TOKEN>/getUpdates` sau khi gửi 1 tin nhắn test, đọc field `chat.id`.

### 3.5. LLM (OpenAI hoặc Gemini)
- OpenAI: lấy API Key tại platform.openai.com.
- Gemini: lấy API Key tại Google AI Studio (aistudio.google.com).
- Chỉ cần 1 trong 2 — khai báo qua biến `LLM_PROVIDER`.

---

## 4. Cấu hình GitHub Secrets

Vào **repo này → Settings → Secrets and variables → Actions → New repository secret**, thêm từng biến sau (xem đầy đủ trong `.env.example`):

```
GCP_SERVICE_ACCOUNT_JSON
GSHEET_ID
LLM_PROVIDER
LLM_API_KEY
META_ACCESS_TOKEN
META_AD_ACCOUNT_ID
META_PAGE_ID
TIKTOK_ACCESS_TOKEN
TIKTOK_ADVERTISER_ID
TIKTOK_IDENTITY_ID
GOOGLE_ADS_DEVELOPER_TOKEN
GOOGLE_ADS_CLIENT_ID
GOOGLE_ADS_CLIENT_SECRET
GOOGLE_ADS_REFRESH_TOKEN
GOOGLE_ADS_CUSTOMER_ID
YOUTUBE_REFRESH_TOKEN     (tùy chọn)
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

**Lưu ý:** `GCP_SERVICE_ACCOUNT_JSON` dán **nguyên văn nội dung file JSON** (cả dấu `{}`), không phải đường dẫn file.

---

## 5. Cách dùng hàng ngày (lên Ad mới)

1. Mở Google Sheet → tab `Ad_Queue`.
2. Thêm 1 dòng mới, điền:
   - `Row_ID`: mã tự đặt, không trùng (ví dụ `AD_002`).
   - `Topic_Group`: tên nhóm chủ đề — dùng để gom báo cáo tuần sau này, nên đặt ngắn gọn và nhất quán (ví dụ `PainPoint_MatNgu`, `Combo_GiaRe`).
   - `Topic_Prompt`: mô tả ý tưởng/góc tiếp cận bằng ngôn ngữ tự nhiên — LLM sẽ dựa vào đây để viết nội dung.
   - `Media_Drive_URL`: link chia sẻ ảnh/video trên Drive (đã share cho Service Account).
   - `Media_Type`: `IMAGE` hoặc `VIDEO`.
   - `Platforms`: liệt kê nền tảng muốn chạy, cách nhau dấu phẩy, ví dụ `META, TIKTOK` (không bắt buộc chạy cả 3).
   - `Campaign_IDs`: JSON chỉ định AdSet (Meta) / AdGroup (TikTok) / Ad Group (Google) có sẵn sẽ nhận Ad mới — ví dụ `{"meta":"23851...","tiktok":"17269..."}`. **Chỉ cần key của các platform có trong `Platforms`.**
   - `Landing_Page_URL`: link đích, hệ thống tự gắn thêm UTM.
   - `Initial_Status`: `PAUSED` (khuyến nghị để review trước) hoặc `ACTIVE`.
   - `Pipeline_Status`: gõ `READY` để đưa vào hàng đợi xử lý.
3. Để trống `Created_Ad_IDs` và `Error_Log` — hệ thống tự ghi.
4. Chờ tối đa 30 phút (hoặc vào tab **Actions → Deploy Ads → Run workflow** để chạy ngay).
5. Theo dõi cột `Pipeline_Status`: `PROCESSING` → `DONE` (thành công) hoặc `ERROR` (xem `Error_Log` để biết lý do, đồng thời sẽ có cảnh báo gửi về Telegram).
6. Nếu `Initial_Status = PAUSED`, vào trình quản lý quảng cáo tương ứng để review rồi bật ACTIVE thủ công.

---

## 6. Đọc báo cáo tuần

- Mỗi sáng Thứ 2 (08:00 giờ Việt Nam), hệ thống tự:
  1. Lấy toàn bộ Ad đang có trong `Ad_Registry`.
  2. Kéo số liệu 7 ngày gần nhất từ Meta/TikTok/Google Ads.
  3. Gộp theo `Platform` + `Topic_Group`, tính CTR/CPC/CPA.
  4. Nhờ LLM nhận xét: Top 2 nhóm chủ đề hiệu quả nhất, Top 2 cần tắt/tối ưu.
  5. Ghi snapshot vào tab `Weekly_Reports` và gửi tin nhắn Markdown về Telegram.
- Muốn xem lại số liệu cũ: mở tab `Weekly_Reports`, lọc theo `Week_Range` hoặc `Topic_Group`.
- Muốn chạy báo cáo ngay (không chờ Thứ 2): vào **Actions → Weekly Report → Run workflow**.

---

## 7. Lệnh Git để đẩy code lên GitHub

Nếu bạn tải code về máy và muốn tự push (thay vì để Claude push), chạy:

```bash
git init
git remote add origin https://github.com/onepiecoltd-tech/AI-MARKETING-NU-APARTMENT.git
git add .
git commit -m "Initial commit: Omni-Channel Auto Ads & Weekly Reporter pipeline"
git branch -M main
git push -u origin main
```

> Lưu ý: **không commit** file `.env` thật hay file JSON Service Account thật —
> `.gitignore` đã chặn các file này, chỉ `.env.example` (không chứa secret thật)
> được đưa vào repo.

---

## 8. Test nhanh trên máy local (tùy chọn, trước khi tin tưởng chạy tự động)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # rồi điền giá trị thật vào .env
python -m jobs.run_deploy_ads
python -m jobs.run_weekly_report
```

---

## 9. Xử lý lỗi thường gặp

| Lỗi | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| `Thiếu biến môi trường bắt buộc` | Chưa cấu hình đủ Secrets trên GitHub | Kiểm tra lại mục 4, đối chiếu `.env.example` |
| `Không trích xuất được Google Drive file ID` | Link Drive không đúng định dạng share | Dùng link "Share → Anyone with the link" hoặc link dạng `/d/<id>/` |
| `File có kích thước ... vượt giới hạn` | Media > 500MB | Giảm dung lượng, hoặc tăng `MAX_MEDIA_FILE_MB` trong Secrets/env |
| `Thiếu Campaign_IDs cho platform X` | Cột `Campaign_IDs` thiếu key của platform đó | Bổ sung đúng JSON, ví dụ `{"meta":"..."}` |
| Lỗi 401/403 từ Meta/TikTok/Google | Access Token hết hạn hoặc thiếu quyền | Cấp lại token, đảm bảo đúng scope `ads_management` |
| `LLM trả về JSON không hợp lệ` | Model trả sai format (hiếm) | Hệ thống tự retry; nếu lặp lại, kiểm tra `LLM_PROVIDER`/model name |
| Workflow không chạy đúng giờ | GitHub Actions cron có thể trễ vài phút ở giờ cao điểm | Bình thường, không phải lỗi hệ thống — hoặc dùng Run workflow thủ công |

---

## 10. Ghi chú an toàn & chi phí

- Hệ thống **không tốn phí server riêng** — chỉ dùng GitHub Actions (free tier: 2,000 phút/tháng cho repo private, đủ dùng cho tần suất 30 phút/lần).
- Chi phí thực tế phát sinh từ: (1) chi tiêu quảng cáo thật trên Meta/TikTok/Google do bạn kiểm soát qua `Initial_Status` và review thủ công; (2) phí gọi API LLM (rất nhỏ, vài trăm token/lần).
- Khuyến nghị luôn để `Initial_Status = PAUSED` cho các dòng mới, review nội dung do LLM sinh ra trước khi bật ACTIVE, nhất là trong giai đoạn đầu vận hành.
