"""Gmail SMTP 발송 (앱 비밀번호 필요).

Gmail 계정 설정: 2단계 인증 켜기 → 앱 비밀번호 생성 → secrets.json 의 GMAIL_APP_PASSWORD.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from . import http

log = logging.getLogger(__name__)


class MailNotConfigured(RuntimeError):
    pass


def configured(cfg: dict) -> bool:
    s = cfg["secrets"]
    return bool(s["GMAIL_USER"] and s["GMAIL_APP_PASSWORD"] and s["REPORT_TO"])


def send(
    cfg: dict,
    subject: str,
    html: str,
    text: str | None = None,
    to: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """attachments: [(파일명, 내용, 'text/plain')]"""
    s = cfg["secrets"]
    if not configured(cfg):
        raise MailNotConfigured("GMAIL_USER / GMAIL_APP_PASSWORD 미설정")
    recipients = [a.strip() for a in (to or s["REPORT_TO"]).split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s["GMAIL_USER"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(text or "HTML 메일을 지원하는 클라이언트에서 확인하세요.")
    msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)

    mail_cfg = cfg["mail"]

    def _deliver():
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(mail_cfg["smtp_host"], mail_cfg["smtp_port"], context=context, timeout=30) as smtp:
            smtp.login(s["GMAIL_USER"], s["GMAIL_APP_PASSWORD"])
            smtp.send_message(msg)

    http.retry_call(_deliver, label="Gmail 발송")
    log.info("메일 발송 완료: %s → %s", subject, msg["To"])
