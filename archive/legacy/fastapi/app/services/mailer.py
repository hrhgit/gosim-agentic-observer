"""Optional SMTP mailer. When SMTP is not configured, messages are recorded in `outbox` (useful for tests)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config import get_settings

log = logging.getLogger("sac.mail")
outbox: list[dict] = []


def send_mail(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    outbox.append({"to": to, "subject": subject, "body": body})
    if len(outbox) > 200:
        del outbox[:-200]
    if not s.email_enabled:
        log.info("email disabled; would send to %s: %s", to, subject)
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as client:
            if s.smtp_tls:
                client.starttls()
            if s.smtp_user:
                client.login(s.smtp_user, s.smtp_password)
            client.send_message(msg)
        return True
    except Exception as exc:  # pragma: no cover - network
        log.warning("email send failed: %s", exc)
        return False
