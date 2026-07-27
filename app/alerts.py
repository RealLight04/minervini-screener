"""이메일 알림 구독 시스템.

구독자 정보는 screener.db가 아니라 **별도 alerts.db**에 저장한다 — screener.db는
봇이 매일 덮어쓰고 git으로 동기화돼서 웹 폼으로 넣은 구독자가 날아가기 때문.
alerts.db는 .gitignore 처리해 git pull에도 보존된다.

발송은 Gmail SMTP(앱 비밀번호). 스팸 방지를 위해 더블 옵트인(확인 링크) 적용.
"""
import os
import smtplib
import sqlite3
import ssl
import secrets
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formataddr

from config import settings

ALERTS_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alerts.db")


def _conn():
    c = sqlite3.connect(ALERTS_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS subscribers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        market TEXT DEFAULT 'US',
        confirmed INTEGER DEFAULT 0,
        token TEXT NOT NULL,
        created_at TEXT)""")
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    return c


# ─── 구독자 관리 ───
def add_subscriber(email: str, market: str = "US") -> tuple[str, bool]:
    """구독 신청(미확인 상태로 추가/갱신). 반환: (확인용 token, 이미확인여부)."""
    email = email.strip().lower()
    token = secrets.token_urlsafe(24)
    with _conn() as c:
        row = c.execute("SELECT confirmed FROM subscribers WHERE email=?", (email,)).fetchone()
        if row:
            c.execute("UPDATE subscribers SET token=?, market=? WHERE email=?", (token, market, email))
            return token, bool(row[0])
        c.execute(
            "INSERT INTO subscribers(email, market, confirmed, token, created_at) VALUES(?,?,0,?,?)",
            (email, market, token, datetime.utcnow().isoformat()),
        )
        return token, False


def confirm(token: str) -> str | None:
    """확인 링크 클릭 → 구독 확정. 반환: 확정된 이메일 또는 None."""
    with _conn() as c:
        row = c.execute("SELECT email FROM subscribers WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        c.execute("UPDATE subscribers SET confirmed=1 WHERE token=?", (token,))
        return row[0]


def unsubscribe(token: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT email FROM subscribers WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM subscribers WHERE token=?", (token,))
        return row[0]


def list_confirmed() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT email, market, token FROM subscribers WHERE confirmed=1"
        ).fetchall()
    return [{"email": e, "market": m, "token": t} for (e, m, t) in rows]


def get_meta(key: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(key: str, value: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ─── 확인메일 재발송 쿨다운 (이메일 폭탄/발송 쿼터 소진 방지) ───
def recently_sent(email: str, within_seconds: int = 600) -> bool:
    """최근 within_seconds 이내에 이 주소로 확인 메일을 보낸 적 있는지."""
    val = get_meta(f"resend_cd:{email.strip().lower()}")
    if not val:
        return False
    try:
        last = datetime.fromisoformat(val)
    except ValueError:
        return False
    return (datetime.utcnow() - last).total_seconds() < within_seconds


def mark_sent(email: str) -> None:
    set_meta(f"resend_cd:{email.strip().lower()}", datetime.utcnow().isoformat())


# ─── 이메일 발송 ───
def email_enabled() -> bool:
    return bool(settings.GMAIL_USER and settings.GMAIL_APP_PASSWORD)


def send_email(to: str, subject: str, html: str) -> tuple[bool, str]:
    """Gmail SMTP(SSL 465)로 HTML 메일 발송."""
    if not email_enabled():
        return False, "GMAIL_USER/GMAIL_APP_PASSWORD 미설정"
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Minervini Screener", settings.GMAIL_USER))
    msg["To"] = to
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
            s.login(settings.GMAIL_USER, settings.GMAIL_APP_PASSWORD)
            s.sendmail(settings.GMAIL_USER, [to], msg.as_string())
        return True, "ok"
    except Exception as e:
        return False, str(e)


def send_confirmation(email: str, token: str) -> tuple[bool, str]:
    url = f"{settings.ALERT_BASE_URL}/alerts/confirm?token={token}"
    html = f"""
    <div style="font-family:sans-serif; max-width:520px; margin:auto; color:#0f172a;">
      <h2 style="color:#1d4ed8;">📈 Minervini Screener 알림 구독 확인</h2>
      <p>돌파·매도신호 일일 알림을 신청하셨습니다. 아래 버튼을 눌러 <b>구독을 확정</b>해 주세요.</p>
      <p style="margin:24px 0;">
        <a href="{url}" style="background:#1d4ed8; color:#fff; text-decoration:none;
           padding:12px 22px; border-radius:8px; font-weight:700;">구독 확정하기</a>
      </p>
      <p style="color:#64748b; font-size:0.85rem;">본인이 신청하지 않았다면 이 메일을 무시하세요. 확정 전에는 어떤 알림도 오지 않습니다.</p>
    </div>"""
    return send_email(email, "[Minervini] 알림 구독 확인", html)
