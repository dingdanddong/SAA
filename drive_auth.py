"""Google Drive 최초 승인 도구 (1회).

    python3 drive_auth.py

secrets.json 의 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 으로 브라우저 승인을 받고 refresh token 을 터미널에 출력한다.
출력된 값을 secrets.json 의 GOOGLE_REFRESH_TOKEN 에 넣고, GitHub Secret MY_SECRET_KEY 도 같은 내용으로 갱신한다.
토큰은 채팅·커밋에 붙여넣지 말 것.
"""
from __future__ import annotations

import http.server
import sys
import urllib.parse
import webbrowser

import requests

from stock_agent.config import load_config
from stock_agent.drive import TOKEN_URL

SCOPE = "https://www.googleapis.com/auth/drive.file"
PORT = 8765


def main() -> int:
    s = load_config()["secrets"]
    if not (s["GOOGLE_CLIENT_ID"] and s["GOOGLE_CLIENT_SECRET"]):
        sys.exit("secrets.json 에 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 을 먼저 넣으세요.")
    redirect = f"http://127.0.0.1:{PORT}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": s["GOOGLE_CLIENT_ID"],
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("승인이 끝났습니다. 터미널로 돌아가세요.".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print("브라우저에서 승인하세요 (자동으로 열리지 않으면 아래 주소를 복사해 여세요):\n" + url)
    webbrowser.open(url)
    server.handle_request()
    if "code" not in got:
        sys.exit(f"승인 실패: {got.get('error', ['알 수 없음'])[0]}")

    resp = requests.post(
        TOKEN_URL,
        data={
            "code": got["code"][0],
            "client_id": s["GOOGLE_CLIENT_ID"],
            "client_secret": s["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    token = resp.json().get("refresh_token") if resp.ok else None
    if not token:
        sys.exit("refresh token 을 받지 못했습니다. 계정의 앱 연결 해제 후 다시 시도하세요.")
    print("\nGOOGLE_REFRESH_TOKEN 값 (secrets.json 에 넣으세요):\n" + token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
