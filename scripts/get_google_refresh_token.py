"""
scripts/get_google_refresh_token.py
------------------------------------
Chạy LOCAL (trên máy bạn, KHÔNG chạy trên GitHub Actions) MỘT LẦN DUY NHẤT để
lấy refresh token OAuth dùng cho GoogleWorkspaceConfig (Sheets/Drive) và
YouTubeConfig (đăng organic YouTube).

Vì sao cần script này:
- Org GCP của bạn (onepieco-ltd-org) bật policy `iam.disableServiceAccountKeyCreation`
  nên không tải được Service Account JSON key.
- Thay vào đó pipeline xác thực bằng OAuth Client (Desktop app) + refresh token.
  Refresh token chỉ cần lấy 1 lần — sau đó dùng mãi (trừ khi bạn revoke quyền
  truy cập trong https://myaccount.google.com/permissions).

Cách dùng:
    1. Cài thư viện (nếu chưa có):
         pip install google-auth-oauthlib google-auth google-api-python-client
    2. Đặt file JSON tải từ Google Cloud Console (OAuth client "Desktop app")
       cùng thư mục, đặt tên là client_secret.json (hoặc sửa CLIENT_SECRET_FILE
       bên dưới / truyền qua tham số --client-secret).
    3. Chạy:
         python scripts/get_google_refresh_token.py
       Trình duyệt sẽ tự mở để bạn đăng nhập Google & bấm "Allow". Đăng nhập
       đúng tài khoản sở hữu Google Sheet / kênh YouTube bạn muốn pipeline
       thao tác thay (ví dụ onepieco.ltd@gmail.com), và tài khoản đó PHẢI nằm
       trong danh sách Test users của OAuth consent screen (nếu app ở chế độ
       Testing).
    4. Script in ra client_id, client_secret, refresh_token — copy các giá trị
       này vào GitHub Secrets:
         GOOGLE_OAUTH_CLIENT_ID
         GOOGLE_OAUTH_CLIENT_SECRET
         GOOGLE_OAUTH_REFRESH_TOKEN   (dùng cho Sheets/Drive)
         YOUTUBE_REFRESH_TOKEN        (dùng cho đăng organic YouTube — nếu
                                        bạn consent cả scope youtube khi chạy
                                        script này thì 2 refresh token có thể
                                        TRÙNG NHAU, dùng chung 1 giá trị)

Lưu ý bảo mật: refresh_token có giá trị tương đương mật khẩu cho các scope đã
cấp — không commit vào Git, không dán vào nơi công khai. Chỉ lưu trong GitHub
Secrets hoặc file .env (đã có trong .gitignore).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# Toàn bộ scope pipeline cần: Sheets + Drive (control plane) + YouTube (đăng
# organic, cả xem lẫn upload). Nếu bạn chỉ cần Sheets/Drive (chưa dùng YouTube
# organic), vẫn có thể consent hết — không có hại gì, chỉ là xin quyền rộng
# hơn mức dùng ngay lúc này.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]

DEFAULT_CLIENT_SECRET_FILE = "client_secret.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-secret",
        default=DEFAULT_CLIENT_SECRET_FILE,
        help=(
            "Đường dẫn file JSON OAuth client (Desktop app) tải từ Google Cloud "
            "Console > APIs & Services > Credentials. Mặc định: client_secret.json"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port local server dùng để nhận callback OAuth (0 = tự chọn port trống).",
    )
    args = parser.parse_args()

    client_secret_path = Path(args.client_secret)
    if not client_secret_path.exists():
        print(
            f"Không tìm thấy file '{client_secret_path}'. Tải file JSON của OAuth "
            "Client (Desktop app) từ Google Cloud Console rồi đặt đúng tên/đường dẫn, "
            "hoặc truyền --client-secret <đường_dẫn>.",
            file=sys.stderr,
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    # run_local_server mở trình duyệt, chờ bạn đăng nhập + Allow, rồi tự bắt
    # callback trên localhost — không cần copy/paste code thủ công.
    creds = flow.run_local_server(port=args.port)

    with client_secret_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    installed = raw.get("installed") or raw.get("web") or {}
    client_id = installed.get("client_id", "")
    client_secret = installed.get("client_secret", "")

    print("\n" + "=" * 70)
    print("THÀNH CÔNG! Copy các giá trị dưới đây vào GitHub Secrets:")
    print("=" * 70)
    print(f"GOOGLE_OAUTH_CLIENT_ID      = {client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET  = {client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN  = {creds.refresh_token}")
    print(f"YOUTUBE_REFRESH_TOKEN       = {creds.refresh_token}")
    print("=" * 70)
    print(
        "\nLưu ý: nếu sau này pipeline báo lỗi 'invalid_grant' hoặc token hết hạn, "
        "chạy lại script này để lấy refresh token mới (thường do bạn đã revoke "
        "quyền truy cập, đổi mật khẩu Google, hoặc app ở Testing mode quá 7 ngày "
        "không dùng)."
    )


if __name__ == "__main__":
    main()
