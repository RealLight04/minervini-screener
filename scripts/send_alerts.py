"""매일 스크리닝 후 '새 돌파·매도신호'를 구독자에게 이메일로 발송.

그램의 데이터 갱신(screener-refresh.sh) 뒤에 실행된다. 새 screen_date에 대해서만
1회 발송(alerts.db meta로 중복 방지)하므로, 30분마다 호출돼도 하루 한 번만 나간다.
Gmail 미설정이거나 구독자가 없으면 조용히 종료.

  새 돌파 = 오늘 STRONG_BUY인데 직전 스크리닝엔 아니었던 종목(피벗 갓 돌파)
  새 매도 = 오늘 SELL인데 직전엔 아니었던 종목(50일선 이탈)
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("send_alerts")

from app import alerts as A
from app.database import SessionLocal
from app.models import ScreeningResult, Stock
from config import settings

MARKET_LABEL = {"US": "미국", "KOSPI": "코스피", "KOSDAQ": "코스닥"}


def _fmt(v, market):
    if v is None:
        return "-"
    return f"${v:,.2f}" if market == "US" else f"₩{v:,.0f}"


def _ids(db, d, signals):
    return {r[0] for r in db.query(ScreeningResult.stock_id).filter(
        ScreeningResult.screen_date == d, ScreeningResult.signal.in_(signals)).all()}


def _details(db, d, ids):
    if not ids:
        return []
    return (db.query(Stock.ticker, Stock.name, Stock.market, ScreeningResult.close,
                     ScreeningResult.pivot_price, ScreeningResult.stop_loss, ScreeningResult.rs_rank)
            .join(ScreeningResult, ScreeningResult.stock_id == Stock.id)
            .filter(ScreeningResult.screen_date == d, ScreeningResult.stock_id.in_(ids))
            .order_by(ScreeningResult.rs_rank.desc()).all())


def _list_html(rows, kind, base):
    if not rows:
        return "<p style='color:#64748b; margin:4px 0 0;'>해당 없음</p>"
    items = ""
    for tk, name, market, close, pivot, stop, rs in rows:
        rs_s = f"{int(rs)}" if rs is not None else "-"
        if kind == "br":
            extra = f"매수가 {_fmt(pivot, market)} · 손절 {_fmt(stop, market)}"
        else:
            extra = f"현재가 {_fmt(close, market)}"
        items += (f"<li style='margin:0 0 7px;'>"
                  f"<a href='{base}/stock/{tk}' style='color:#2563eb; font-weight:700; text-decoration:none;'>{tk}</a> "
                  f"<span style='color:#334155;'>{name or ''}</span> — {extra} · RS {rs_s}</li>")
    return f"<ul style='padding-left:18px; margin:6px 0 0;'>{items}</ul>"


def _vcp_formations(db, latest):
    """알림 대상 '새 VCP 형성' 이벤트 — alert_formed_at 워터마크로 이벤트당 정확히 1회.

    품질(≥VCP_QUALITY_ALERT)·후행베이스 아님(base_seq<3)·최소 형성기간(≥VCP_ALERT_MIN_BASE_DAYS)을
    충족하고 아직 알림을 안 보낸(forming) 이벤트만. 워터마크라 US/KR 분할 런에도 중복/누락 0.
    """
    from app.models import VCPEvent
    rows = (db.query(VCPEvent, Stock)
            .join(Stock, Stock.id == VCPEvent.stock_id)
            .filter(VCPEvent.status == "forming",
                    VCPEvent.alert_formed_at.is_(None),
                    VCPEvent.quality >= settings.VCP_QUALITY_ALERT,
                    VCPEvent.base_seq < 3)
            .order_by(VCPEvent.quality.desc())
            .all())
    return [(ev, s) for ev, s in rows
            if (latest - ev.first_detected).days >= settings.VCP_ALERT_MIN_BASE_DAYS]


def _vcp_list_html(rows, base):
    if not rows:
        return "<p style='color:#64748b; margin:4px 0 0;'>해당 없음</p>"
    items = ""
    for ev, s in rows:
        mk = s.market or "US"
        rs_s = f"{int(ev.rs_rank)}" if ev.rs_rank is not None else "-"
        gap = (f"돌파까지 +{round((ev.pivot_price / ev.close - 1) * 100, 1)}%"
               if (ev.pivot_price and ev.close and ev.close < ev.pivot_price) else "돌파권 진입")
        items += (f"<li style='margin:0 0 7px;'>"
                  f"<a href='{base}/stock/{s.ticker}' style='color:#2563eb; font-weight:700; text-decoration:none;'>{s.ticker}</a> "
                  f"<span style='color:#334155;'>{s.name or ''}</span> — 피벗 {_fmt(ev.pivot_price, mk)} · {gap} "
                  f"· 품질 {ev.quality} · RS {rs_s} · 수축 {ev.contractions or '-'}회</li>")
    return f"<ul style='padding-left:18px; margin:6px 0 0;'>{items}</ul>"


def _build_html(date_, mk, br, sl, vcp, token):
    base = settings.ALERT_BASE_URL
    unsub = f"{base}/alerts/unsubscribe?token={token}"
    return f"""
    <div style="font-family:sans-serif; max-width:600px; margin:auto; color:#0f172a;">
      <h2 style="color:#2563eb; margin-bottom:2px;">📈 {MARKET_LABEL.get(mk, mk)} 매매 신호 · {date_}</h2>
      <p style="color:#64748b; margin-top:0;">Minervini Screener 일일 알림</p>

      <h3 style="color:#16a34a; margin-bottom:2px;">🚀 새로 돌파한 종목 ({len(br)})</h3>
      {_list_html(br, "br", base)}

      <h3 style="color:#7c3aed; margin:20px 0 2px;">🌱 새 VCP 형성 · 돌파 대기 ({len(vcp)})</h3>
      {_vcp_list_html(vcp, base)}

      <h3 style="color:#ea580c; margin:20px 0 2px;">📉 새 매도신호 · 50일선 이탈 ({len(sl)})</h3>
      {_list_html(sl, "sl", base)}

      <p style="margin-top:22px;"><a href="{base}/" style="background:#2563eb; color:#fff; text-decoration:none; padding:10px 18px; border-radius:8px; font-weight:700;">사이트에서 전체 보기</a></p>
      <hr style="border:none; border-top:1px solid #e2e8f0; margin:22px 0 10px;">
      <p style="color:#94a3b8; font-size:0.8rem;">이 메일은 교육·참고용이며 투자 권유가 아닙니다.
        · <a href="{unsub}" style="color:#94a3b8;">구독취소</a></p>
    </div>"""


def main():
    if not A.email_enabled():
        log.info("GMAIL 미설정 — 알림 발송 건너뜀")
        return 0
    subs = A.list_confirmed()
    if not subs:
        log.info("확정 구독자 없음 — 건너뜀")
        return 0

    db = SessionLocal()
    try:
        dates = [d[0] for d in db.query(ScreeningResult.screen_date).distinct()
                 .order_by(ScreeningResult.screen_date.desc()).limit(2).all()]
        if not dates:
            return 0
        latest, prev = dates[0], (dates[1] if len(dates) > 1 else None)
        if A.get_meta("last_notified_date") == str(latest):
            log.info(f"{latest} 이미 발송함 — 건너뜀")
            return 0

        br_ids, sl_ids = _ids(db, latest, ["STRONG_BUY"]), _ids(db, latest, ["SELL"])
        if prev:
            br_ids -= _ids(db, prev, ["STRONG_BUY"])
            sl_ids -= _ids(db, prev, ["SELL"])
        breakouts, sells = _details(db, latest, br_ids), _details(db, latest, sl_ids)
        vcp_all = _vcp_formations(db, latest)
        log.info(f"{latest}: 새 돌파 {len(breakouts)}, 새 VCP형성 {len(vcp_all)}, 새 매도 {len(sells)}")

        # 시장별 가드: 하루에 시장별 런이 따로 돈다(한국 15:50 KST, 미국 새벽 KST).
        # 한국 런 시점엔 미국 데이터가 아직 전일이라 미국 diff가 비어 있고, 이때 날짜 전체를
        # '발송됨'으로 잠그면 저녁 미국 런의 새 돌파가 영영 안 나간다 → 시장 단위로 잠근다.
        # diff가 빈 시장은 잠그지 않아, 그 시장 데이터가 늦게 갱신되면 그때 발송된다.
        from datetime import datetime
        sent = 0
        for mk in ("US", "KOSPI", "KOSDAQ"):
            mk_subs = [s for s in subs if s["market"] == mk]
            if not mk_subs:
                continue          # 이 시장 구독자 없음 — 발송/워터마크 표시 안 함
            # 돌파·매도는 시장별 date-lock으로 하루 1회. VCP 형성은 워터마크로 독립 발송.
            locked = A.get_meta(f"last_notified_{mk}") == str(latest)
            br = [] if locked else [r for r in breakouts if (r[2] or "US") == mk]
            sl = [] if locked else [r for r in sells if (r[2] or "US") == mk]
            vcp = [(ev, s) for ev, s in vcp_all if (s.market or "US") == mk]
            if not br and not sl and not vcp:
                continue          # 이 시장에 새 소식 없음 — 잠그지 않고 다음 갱신을 기다림
            for sub in mk_subs:
                subject = f"[Minervini] {MARKET_LABEL.get(mk, mk)} 돌파 {len(br)}·VCP형성 {len(vcp)}·매도 {len(sl)} ({latest})"
                ok, msg = A.send_email(sub["email"], subject, _build_html(latest, mk, br, sl, vcp, sub["token"]))
                if ok:
                    sent += 1
                else:
                    log.warning(f"{sub['email']} 발송 실패: {msg}")
            # VCP 형성 워터마크: 이 시장 이벤트를 발송 완료로 표시(이벤트당 정확히 1회)
            now = datetime.utcnow()
            for ev, _ in vcp:
                ev.alert_formed_at = now
            db.commit()
            # 돌파/매도 date-lock은 실제 그 신호를 포함했을 때만 설정
            if br or sl:
                A.set_meta(f"last_notified_{mk}", str(latest))
            log.info(f"{mk}: 돌파 {len(br)}·VCP형성 {len(vcp)}·매도 {len(sl)} → 발송({latest})")

        log.info(f"발송 완료: {sent}명")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
