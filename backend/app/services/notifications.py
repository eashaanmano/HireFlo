"""Targeted notifications: an in-app inbox per student, plus optional e-mail and Telegram delivery.

Only the students a message concerns are notified — never the whole batch.
"""
from __future__ import annotations

import logging
import smtplib
import threading
from collections.abc import Iterable
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import Notification
from .events import emit

log = logging.getLogger("driveops.notify")


def notify(db: Session, student_ids: Iterable[int], title: str, body: str, link: str | None = None) -> int:
    count = 0
    for sid in dict.fromkeys(student_ids):  # de-duplicate, keep order
        db.add(Notification(student_id=sid, title=title[:160], body=body, link=link))
        emit(db, "notification.created", "system", entity="student", entity_id=sid, student_id=sid)
        count += 1
    return count


def _send_email_sync(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("email to %s failed: %s", to, exc)


def send_email(to: str, subject: str, body: str) -> bool:
    """Fire-and-forget e-mail. Returns False when it isn't sent: no SMTP, or `to` is outside EMAIL_ONLY_TO."""
    s = get_settings()
    if not s.smtp_host:
        return False
    if s.email_allowlist and to.strip().lower() not in s.email_allowlist:
        return False
    threading.Thread(target=_send_email_sync, args=(to, subject, body), daemon=True).start()
    return True


def send_telegram(chat_id: str, text: str) -> bool:
    token = get_settings().telegram_bot_token
    if not token or not chat_id:
        return False
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat_id, "text": text}, timeout=10).raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("telegram send failed: %s", exc)
        return False
