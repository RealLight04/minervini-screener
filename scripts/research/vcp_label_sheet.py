# -*- coding: utf-8 -*-
"""VCP 라벨 검증용 시트 생성. 사람이 차트를 보고 판정한 라벨로 탐지와 점수가 맞는지 확인한다(연구용).

HTML 한 장(scripts/research/data/label_sheet.html)을 만든다. 서버나 포트를 쓰지 않고 파일을 브라우저로 열면 된다.
  Q1  종목명, 날짜, 점수를 가린 차트를 보고 "관심종목에 올려서 더 볼 만한가"를 먼저 판정(볼 만함/애매/패스).
      정의 판정은 전문가끼리도 갈려 어려우므로, 이 도구의 목적(사람이 차트를 볼 가치가 있는 후보를 위로 올리기)에 맞춘 질문이다
      기준 예시 3개(좋은 예, 애매한 예, 박스권)를 시트 맨 위에 붙인다
  Q2  "탐지 결과 보기"로 수축(T1~)과 피벗 표시를 겹쳐 본 뒤 "탐지가 읽은 수축과 피벗이 맞나"를 판정(맞음/부분/틀림)
시트에 나오는 번호와 실제 종목, 점수, Jev 판정의 대응표는 label_key.csv에 따로 저장한다(라벨링할 때는 보지 않는다).

표본 (모두 돌파 전 모습까지만 보여준다)
  hist  2010~2026 이벤트(vcp_score_backtest.py 결과)에서 등급별로 뽑은 과거 형성. 점수 전 구간에서 사람 판정과 맞는지 본다
  top   오늘 추세 통과 + 형성 + 60점 이상 상위 후보
  near  오늘 추세 통과 + 형성이 있지만 60점에 못 미친 아까운 종목
  none  오늘 추세 통과인데 탐지가 형성을 못 찾은 종목(사람은 VCP로 볼 수도 있는 놓친 후보 확인용)

Jev는 항목 수치만 보고 "교과서적인 VCP인가"를 판정하고 확신도를 label_key.csv에 남긴다(--no-jev로 끔).
선행: vcp_score_backtest.py 실행(이벤트 CSV), Jev는 .env 에 TYPESAFE_API_KEY
사용: python scripts/research/vcp_label_sheet.py [--seed 7] [--no-jev]
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from jinja2 import Environment

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from app import vcp  # noqa: E402

DATA = os.path.join(HERE, "data")
EVENTS = os.path.join(DATA, "vcp_score_events.csv")
PRICES = os.path.join(DATA, "prices.sqlite")
SCREENER_DB = os.path.join(ROOT, "screener.db")
OUT_HTML = os.path.join(DATA, "label_sheet.html")
OUT_KEY = os.path.join(DATA, "label_key.csv")
BARS = 180
HIST_PER_GRADE = {"A+": 8, "A": 8, "B": 8, "C": 6, "-": 5}
MODEL = os.getenv("JEV_MODEL", "jev-1.13.0")
GATEWAY_URL = os.getenv("JEV_GATEWAY_URL", "https://ai-gateway.vercel.sh/typesafe")


# ---------------------------------------------------------------- 표본

def make_item(set_, tk, asof_date, o, h, l, c, v, f, a, extra=None):
    """돌파 전 시점까지의 배열과 탐지 결과를 시트 한 칸 분량으로 정리한다."""
    n = len(c)
    s = max(0, n - BARS)
    k = 100.0 / c[-1]
    ma = {w: pd.Series(c).rolling(w).mean().to_numpy() * k for w in (50, 150, 200)}
    vol_avg = pd.Series(v).fillna(0).rolling(50, min_periods=10).mean().to_numpy()
    return {
        "set": set_, "ticker": tk, "asof": str(asof_date)[:10], "s": s, "n": n - s,
        "o": o[s:] * k, "h": h[s:] * k, "l": l[s:] * k, "c": c[s:] * k,
        "v": np.nan_to_num(v[s:]), "vavg": vol_avg[s:],
        "ma": {w: x[s:] for w, x in ma.items()},
        "formation": f, "analysis": a, "k": k, **(extra or {}),
    }


def hist_items(seed):
    ev = pd.read_csv(EVENTS, parse_dates=["date"])
    parts = [ev[ev.grade == g].sample(n=min(n, (ev.grade == g).sum()), random_state=seed) for g, n in HIST_PER_GRADE.items()]
    pick = pd.concat(parts)
    con = sqlite3.connect(PRICES)
    items = []
    for r in pick.itertuples():
        px = pd.read_sql("SELECT date, open, high, low, close, volume FROM prices WHERE ticker=? ORDER BY date", con,
                         params=(r.ticker,), parse_dates=["date"]).dropna(subset=["close", "high", "low", "open"])
        j = int(np.searchsorted(px.date.values, np.datetime64(r.date)))
        if j < 120 or j >= len(px) or px.date.iloc[j] != r.date:
            continue
        sub = px.iloc[:j]                                                    # 돌파 전날까지
        o, h, l, c, v = (sub[x].to_numpy(float) for x in ("open", "high", "low", "close", "volume"))
        f = vcp.detect(h, l, c, v)
        if f is None:
            continue
        a = vcp.analyze(h, l, c, v, rs=r.rs, formation=f)
        items.append(make_item("hist", r.ticker, sub.date.iloc[-1], o, h, l, c, v, f, a, {"r1": r.r1, "grade_ev": r.grade}))
    con.close()
    return items


def live_items(seed):
    con = sqlite3.connect(f"file:{SCREENER_DB}?mode=ro", uri=True)
    d = con.execute("select max(screen_date) from screening_results").fetchone()[0]
    rows = con.execute("select s.id, s.ticker, r.rs_rank, r.technical_pass from screening_results r "
                       "join stocks s on s.id = r.stock_id where r.screen_date = ?", (d,)).fetchall()
    cand = []
    for sid, tk, rs, tp in rows:
        if not tp:
            continue
        px = pd.read_sql("select date, open, high, low, close, volume from daily_prices where stock_id=? order by date", con,
                         params=(sid,), parse_dates=["date"]).dropna(subset=["close", "high", "low", "open"])
        if len(px) < 120:
            continue
        o, h, l, c, v = (px[x].to_numpy(float) for x in ("open", "high", "low", "close", "volume"))
        a = vcp.analyze(h, l, c, v, rs=rs)
        if a.formation is not None and a.breakout is not None:             # 돌파한 종목은 돌파 전 모습까지만 보여준다
            if a.breakout_age is None or a.breakout_age > 3:
                continue
            cut = a.breakout.idx
            o, h, l, c, v, px = o[:cut], h[:cut], l[:cut], c[:cut], v[:cut], px.iloc[:cut]
            a = vcp.analyze(h, l, c, v, rs=rs, formation=a.formation)
        cand.append((tk, px, o, h, l, c, v, a, rs))
    con.close()
    pool = [x for x in cand if x[7].formation is not None and x[7].state in (vcp.WATCH, vcp.NEAR_PIVOT)]
    top = sorted([x for x in pool if x[7].eligible], key=lambda x: -x[7].score.total)[:12]
    near = sorted([x for x in pool if not x[7].eligible and x[7].score.total >= 40], key=lambda x: -x[7].score.total)[:8]
    none = [x for x in cand if x[7].formation is None]
    rng = np.random.default_rng(seed)
    none = [none[i] for i in rng.permutation(len(none))[:8]]
    items = []
    for set_, group in (("top", top), ("near", near), ("none", none)):
        for tk, px, o, h, l, c, v, a, rs in group:
            items.append(make_item(set_, tk, px.date.iloc[-1], o, h, l, c, v, a.formation, a))
    return items, d


# ---------------------------------------------------------------- 차트

W, TOP, PH, GAP, VH, LEFT, RIGHT = 760, 10, 250, 12, 56, 46, 10
HEIGHT = TOP + PH + GAP + VH + 16


def chart_svg(it, bars=BARS, tag="a", labels=False):
    """bars거래일만 잘라 그린다. labels가 True일 때만 수축과 피벗 글자를 붙인다(전체 차트는 선만, 확대 차트는 글자까지)."""
    off = max(0, it["n"] - bars)
    it = {**it, "n": it["n"] - off, "s": it["s"] + off, **{k: it[k][off:] for k in ("o", "h", "l", "c", "v", "vavg")},
          "ma": {w: x[off:] for w, x in it["ma"].items()}}
    n = it["n"]
    step = (W - LEFT - RIGHT) / n
    hi, lo = float(np.nanmax(it["h"])), float(np.nanmin(it["l"]))
    pad = (hi - lo) * 0.04
    hi, lo = hi + pad, lo - pad

    def X(i):
        return LEFT + (i + 0.5) * step

    def Y(p):
        return TOP + (hi - p) / (hi - lo) * PH

    vmax = float(max(it["v"].max(), 1.0))
    vtop = TOP + PH + GAP

    def VY(x):
        return vtop + VH - x / vmax * VH

    p = [f'<svg class="chart" viewBox="0 0 {W} {HEIGHT}" role="img" aria-label="일봉 차트 {n}거래일">',
         f'<defs><clipPath id="clip{it["id"]}{tag}"><rect x="{LEFT}" y="{TOP}" width="{W - LEFT - RIGHT}" height="{PH}"/></clipPath></defs>']
    span = hi - lo
    tick = next((s_ for s_ in (1, 2, 5, 10, 20, 50) if span / s_ <= 8), 100)
    t = int(np.ceil(lo / tick) * tick)
    while t <= hi:
        p.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                 f'<text class="ylab" x="{LEFT - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{t}</text>')
        t += tick
    p.append(f'<g clip-path="url(#clip{it["id"]}{tag})">')
    for w, cls in ((200, "ma200"), (150, "ma150"), (50, "ma50")):
        pts = " ".join(f"{X(i):.1f},{Y(x):.1f}" for i, x in enumerate(it["ma"][w]) if not np.isnan(x))
        if pts:
            p.append(f'<polyline class="ma {cls}" points="{pts}"/>')
    p.append("</g>")
    for i in range(n):
        up = it["c"][i] >= it["o"][i]
        cls = "up" if up else "dn"
        x = X(i)
        top_b, bot_b = Y(max(it["o"][i], it["c"][i])), Y(min(it["o"][i], it["c"][i]))
        p.append(f'<line class="wick {cls}" x1="{x:.1f}" x2="{x:.1f}" y1="{Y(it["h"][i]):.1f}" y2="{Y(it["l"][i]):.1f}"/>'
                 f'<rect class="body {cls}" x="{x - step * 0.34:.1f}" y="{top_b:.1f}" width="{step * 0.68:.1f}" '
                 f'height="{max(bot_b - top_b, 0.8):.1f}"/>')
        p.append(f'<rect class="vol {cls}" x="{x - step * 0.34:.1f}" y="{VY(it["v"][i]):.1f}" width="{step * 0.68:.1f}" '
                 f'height="{vtop + VH - VY(it["v"][i]):.1f}"/>')
    vp = " ".join(f"{X(i):.1f},{VY(x):.1f}" for i, x in enumerate(it["vavg"]) if x > 0)
    if vp:
        p.append(f'<polyline class="vavg" points="{vp}"/>')
    # 탐지 결과 표시(기본은 숨김)
    f, s, k = it["formation"], it["s"], it["k"]
    if f is not None:
        p.append('<g class="ov">')
        for m, cn in enumerate(f.contractions, 1):
            a_, b_ = cn.peak_idx - s, cn.low_idx - s
            if b_ < 0:
                continue
            a_ = max(a_, 0)
            edge = X(b_) > W - RIGHT - 50                          # 오른쪽 끝이면 라벨이 잘리지 않게 끝 정렬
            p.append(f'<line class="leg" x1="{X(a_):.1f}" y1="{Y(cn.peak_price * k):.1f}" x2="{X(b_):.1f}" y2="{Y(cn.low_price * k):.1f}"/>')
            if labels:
                p.append(f'<text class="ovlab" x="{X(b_) + (4 if edge else 0):.1f}" y="{Y(cn.low_price * k) + 14 + 12 * (m % 2):.1f}" '
                         f'text-anchor="{"end" if edge else "middle"}">T{m} {cn.depth_pct:.0f}%</text>')
        py = Y(f.pivot * k)
        px0 = max(f.pivot_idx - s, 0)
        p.append(f'<line class="pivot" x1="{X(px0):.1f}" x2="{W - RIGHT}" y1="{py:.1f}" y2="{py:.1f}"/>')
        if labels:
            p.append(f'<text class="ovlab" x="{X(px0):.1f}" y="{py - 5:.1f}" text-anchor="start">피벗 {f.pivot * k:.1f}</text>')
        bi = f.base_high_idx - s
        if bi >= 0 and labels:
            p.append(f'<text class="ovlab" x="{X(bi):.1f}" y="{Y(f.base_high * k) - 6:.1f}" text-anchor="middle">▼ 베이스 고점</text>')
        p.append("</g>")
    p.append(f'<text class="ylab" x="{LEFT}" y="{HEIGHT - 3}">거래량</text>')
    p.append("</svg>")
    return "".join(p)


def example_items():
    """판정 기준을 보여주는 만든 예시 3개. 합성 데이터라 실제 차트보다 깔끔하다."""
    sys.path.insert(0, ROOT)
    from tests.synth import make_vcp
    out = []
    for title, depths, cap in (
        ("좋은 예: 볼 만함", [22, 11, 5], "조정 폭이 점점 작아지고, 바닥이 올라오고, 끝이 조용함"),
        ("애매한 예", [20, 8, 14], "조정 폭이 줄다가 마지막에 다시 커짐. 줄어드는 흐름이 깨짐"),
        ("패스할 예: 박스권", [15, 14, 16, 13], "조정 크기가 계속 비슷함. 줄어드는 게 없는 박스권"),
    ):
        d = make_vcp(depths, noise=0.004, seed=3, highs_drop=0.03)
        h, l, c, v = d["h"], d["l"], d["c"], d["v"]
        o = np.r_[c[0], c[:-1]]
        f = vcp.detect(h, l, c, v)
        a = vcp.analyze(h, l, c, v, rs=90, formation=f)
        it = make_item("ex", "-", "-", o, h, l, c, v, f, a)
        it["id"] = f"ex{len(out) + 1}"
        it["svg"] = chart_svg(it, 90, "e", labels=True)
        read = "→".join(f"{c.depth_pct:.0f}" for c in f.contractions)
        it["title"], it["caption"] = title, f"{cap}. 탐지가 읽은 조정 폭 {read}%"
        out.append(it)
    return out


# ---------------------------------------------------------------- Jev

INSTRUCTIONS = (
    "The state describes a stock's consolidation base measured the day before a possible breakout. A textbook Mark Minervini "
    "volatility contraction pattern (VCP) has a prior uptrend, two to four pullbacks each clearly smaller than the previous one, "
    "rising pullback lows, volume that dries up toward the end, a quiet final tight area, and price just below the pivot. "
    "Decide: this base is a textbook VCP."
)


def jev_state(it):
    a, f = it["analysis"], it["formation"]
    cons = f.contractions
    vols = [c.avg_volume for c in cons]
    comp = a.score.components
    s = {
        "pullback_depths_pct": [round(c.depth_pct, 1) for c in cons],
        "pullback_lows_relative_to_first_low": [round(c.low_price / cons[0].low_price, 3) for c in cons],
        "pullback_avg_volume_relative_to_first": [round(x / vols[0], 2) for x in vols] if all(vols) else None,
        "volume_last_10d_to_50d_ratio": comp["volume"].detail["dry_ratio"],
        "price_range_last_5d_pct": comp["tightness"].detail["range5_pct"],
        "price_range_last_10d_pct": comp["tightness"].detail["range10_pct"],
        "distance_below_pivot_pct": round(a.distance_to_pivot * 100, 1),
        "base_length_days": int(f.detected_idx - f.base_high_idx),
        "trend_template_conditions_passed_ratio": comp["trend"].detail.get("ratio"),
    }
    return {k: x for k, x in s.items() if x is not None}


async def jev_probe(states):
    from pydantic_ai import Agent
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider

    gw_key = os.getenv("AI_GATEWAY_API_KEY")
    provider = TypeSafeProvider(api_key=gw_key, base_url=GATEWAY_URL) if gw_key else TypeSafeProvider()
    agent = Agent(TypeSafeModel(MODEL, provider=provider), output_type=bool, instructions=INSTRUCTIONS)
    sem = asyncio.Semaphore(8)

    async def one(st):
        async with sem:
            try:
                res = await agent.run(str(st))
                conf = res.response.provider_details["confidence"]
                c = next(iter(conf.values())) if isinstance(conf, dict) else float(conf)
                return c if res.output else 1 - c
            except Exception as ex:
                print(f"  Jev 실패: {ex}", flush=True)
                return None

    return await asyncio.gather(*(one(s) for s in states))


# ---------------------------------------------------------------- 출력

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VCP 라벨 시트</title>
<style>
:root{--bg:#0f1216;--panel:#171c22;--line:#2a323b;--fg:#e6e9ed;--mute:#9aa4af;--accent:#e8c547;--up:#5fb89a;--dn:#d1695f;
--ma50:#e8c547;--ma150:#7aa2d6;--ma200:#9aa4af;--leg:#f08a3c;--ok:#5fb89a;--bad:#d1695f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,"Malgun Gothic",sans-serif}
main{max-width:820px;margin:0 auto;padding:16px}
h1{font-size:1.25rem;margin:8px 0}
.guide{background:var(--panel);border:1px solid var(--line);border-radius:3px;padding:12px 16px;margin:12px 0}
.guide ol{margin:6px 0 0;padding-left:20px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.bar .n{font-weight:700;color:var(--accent)}
button{min-height:44px;padding:0 16px;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:3px;font:inherit;cursor:pointer}
button:hover{border-color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:3px;margin:16px 0;padding:12px}
.card h2{font-size:1rem;margin:0 0 6px;display:flex;justify-content:space-between}
.card h2 .tag{color:var(--mute);font-weight:400;font-size:.85rem}
.chart{width:100%;height:auto;display:block;background:#0b0e11}
.grid{stroke:#1e252d;stroke-width:1}.ylab{fill:var(--mute);font-size:11px}
.wick.up,.body.up,.vol.up{stroke:var(--up);fill:var(--up)}.wick.dn,.body.dn,.vol.dn{stroke:var(--dn);fill:var(--dn)}
.vol{opacity:.55;stroke:none}.body{stroke:none}.wick{stroke-width:1}
.ma{fill:none;stroke-width:1.2}.ma50{stroke:var(--ma50)}.ma150{stroke:var(--ma150)}.ma200{stroke:var(--ma200)}
.vavg{fill:none;stroke:#cfd5db;stroke-width:1;opacity:.6}
.ov{display:none}.show-ov .ov{display:inline}
.leg{stroke:var(--leg);stroke-width:2.2}.pivot{stroke:var(--accent);stroke-width:1.5;stroke-dasharray:5 4}
.ovlab{fill:var(--leg);font-size:12px;font-weight:700;paint-order:stroke;stroke:#0b0e11;stroke-width:3px}
.legend{color:var(--mute);font-size:.82rem;margin:4px 0 8px}
.legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin:0 4px 0 10px}
fieldset{border:0;margin:8px 0 0;padding:0}legend{font-weight:700;margin-bottom:4px;padding:0}
.opts{display:flex;gap:8px;flex-wrap:wrap}
.opts label{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 16px;border:1px solid var(--line);border-radius:3px;cursor:pointer}
.opts input{accent-color:var(--accent);width:18px;height:18px}
.opts label:has(input:checked){border-color:var(--accent);background:#222a31}
.q2{display:none}.show-ov .q2{display:block}
input[type=text]{width:100%;min-height:44px;margin-top:8px;padding:0 12px;background:#0b0e11;color:var(--fg);border:1px solid var(--line);border-radius:3px;font:inherit}
textarea{width:100%;height:120px;background:#0b0e11;color:var(--fg);border:1px solid var(--line);border-radius:3px;padding:8px;font:12px/1.4 monospace}
.done{border-color:var(--ok)}
@media (prefers-reduced-motion:no-preference){.card{transition:border-color .15s}}
</style></head>
<body><main>
<h1>VCP 라벨 시트 · {{ items|length }}개</h1>
<div class="guide">
<b>이렇게 답하면 됨</b>
<p style="margin:6px 0">정확한 VCP 판정이 아님. "이 종목을 관심종목에 올려서 더 볼 만한가"를 감으로 답하면 됨. 고민되면 애매.</p>
<p style="margin:6px 0 2px">볼 때 세 가지만 확인</p>
<ol>
<li>오른쪽으로 갈수록 출렁임(조정 폭)이 작아지나</li>
<li>조정 바닥이 점점 올라오나</li>
<li>맨 끝 며칠이 조용하고 거래량이 줄었나</li>
</ol>
<p class="legend" style="margin:8px 0 0">차트는 돌파 전 모습까지만 나옴. 가격은 마지막 종가를 100으로 맞춤.
<i style="background:var(--ma50)"></i>50일선<i style="background:var(--ma150)"></i>150일선<i style="background:var(--ma200)"></i>200일선<i style="background:#cfd5db"></i>거래량 50일 평균</p>
<p style="margin:8px 0 0">Q1을 먼저 답함. "탐지 결과 보기"로 표시를 겹친 뒤 Q2를 답함(Q2는 건너뛰어도 됨). 답은 이 브라우저에 자동 저장됨. 일부만 답해도 됨.</p>
</div>
<div class="guide">
<b>기준 예시 3개</b> <span class="legend">만들어낸 예시라 실제 차트보다 깔끔함. 주황 선이 조정, 노랑 점선이 피벗</span>
{% for e in examples %}
<div class="ex show-ov"><p style="margin:12px 0 2px"><b>{{ e.title }}</b> <span class="legend">{{ e.caption }}</span></p>{{ e.svg|safe }}</div>
{% endfor %}
</div>
<div class="bar"><span>답한 수 <span class="n" id="cnt">0</span> / {{ items|length }}</span>
<button type="button" id="copy">CSV 복사</button><button type="button" id="dl">JSON 저장</button><span id="msg" class="legend"></span></div>
{% for it in items %}
<section class="card" id="c{{ it.id }}" data-id="{{ it.id }}">
<h2><span>#{{ it.id }}</span><span class="tag">일봉 {{ it.nbars }}거래일</span></h2>
{{ it.svg|safe }}
<p class="legend zoomcap">아래는 최근 {{ it.zoom_bars }}거래일 확대 (수축 글자는 확대 차트에만 표시)</p>
{{ it.svg_zoom|safe }}
<fieldset><legend>Q1. 이 차트, 관심종목에 올려서 더 볼 만한가?</legend>
<div class="opts">
<label><input type="radio" name="q1_{{ it.id }}" value="2">볼 만함</label>
<label><input type="radio" name="q1_{{ it.id }}" value="1">애매</label>
<label><input type="radio" name="q1_{{ it.id }}" value="0">패스</label>
</div></fieldset>
<div style="margin-top:10px"><button type="button" class="tog" aria-expanded="false">탐지 결과 보기</button></div>
<fieldset class="q2"><legend>Q2. 탐지가 읽은 수축(T1~)과 피벗이 맞나? <span class="legend">(건너뛰어도 됨) 주황 선: 수축, 노랑 점선: 피벗</span></legend>
<div class="opts">
<label><input type="radio" name="q2_{{ it.id }}" value="2">맞음</label>
<label><input type="radio" name="q2_{{ it.id }}" value="1">부분만</label>
<label><input type="radio" name="q2_{{ it.id }}" value="0">틀림</label>
</div></fieldset>
<input type="text" class="note" placeholder="메모 (선택)" aria-label="#{{ it.id }} 메모">
</section>
{% endfor %}
<div class="guide"><b>내보내기 미리보기</b><textarea id="out" readonly aria-label="라벨 CSV"></textarea></div>
</main>
<script>
const SHEET = "{{ sheet_id }}", KEY = "vcp_label_sheet:" + SHEET;
let labels = {};
try { labels = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const cards = [...document.querySelectorAll(".card")];
function csv() {
  const q = s => '"' + String(s || "").replace(/"/g, '""') + '"';
  return "id,q1,q2,note\n" + cards.map(c => { const l = labels[c.dataset.id] || {};
    return [c.dataset.id, l.q1 ?? "", l.q2 ?? "", q(l.note)].join(","); }).join("\n");
}
function refresh() {
  document.getElementById("cnt").textContent = cards.filter(c => (labels[c.dataset.id] || {}).q1 !== undefined).length;
  cards.forEach(c => c.classList.toggle("done", (labels[c.dataset.id] || {}).q1 !== undefined));
  document.getElementById("out").value = csv();
  try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch (e) {}
}
cards.forEach(c => {
  const id = c.dataset.id, l = labels[id] || {};
  for (const k of ["q1", "q2"]) if (l[k] !== undefined) { const r = c.querySelector(`input[name=${k}_${id}][value="${l[k]}"]`); if (r) r.checked = true; }
  if (l.note) c.querySelector(".note").value = l.note;
  if (l.shown) c.classList.add("show-ov");
  c.addEventListener("change", e => { if (e.target.type !== "radio") return;
    const [k] = e.target.name.split("_"); labels[id] = Object.assign(labels[id] || {}, {[k]: e.target.value}); refresh(); });
  c.querySelector(".note").addEventListener("input", e => { labels[id] = Object.assign(labels[id] || {}, {note: e.target.value}); refresh(); });
  const tog = c.querySelector(".tog");
  tog.addEventListener("click", () => { const on = c.classList.toggle("show-ov");
    tog.setAttribute("aria-expanded", on); tog.textContent = on ? "탐지 결과 숨기기" : "탐지 결과 보기";
    labels[id] = Object.assign(labels[id] || {}, {shown: on}); refresh(); });
  if (c.classList.contains("show-ov")) { tog.setAttribute("aria-expanded", true); tog.textContent = "탐지 결과 숨기기"; }
});
const msg = t => { document.getElementById("msg").textContent = t; };
document.getElementById("copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(csv()); msg("복사됨"); }
  catch (e) { const t = document.getElementById("out"); t.select(); document.execCommand("copy"); msg("복사됨(아래 칸에서 직접 복사해도 됨)"); }
});
document.getElementById("dl").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(labels, null, 1)], {type: "application/json"}));
  a.download = "vcp_labels_" + SHEET + ".json"; a.click(); msg("저장됨");
});
refresh();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--keep-jev", action="store_true", help="기존 label_key.csv의 Jev 판정을 재사용")
    ap.add_argument("--ids", help="쉼표로 구분한 번호만 뽑은 부분 시트를 만든다(번호는 전체 시트와 같고 label_key.csv는 건드리지 않는다)")
    args = ap.parse_args()

    items = hist_items(args.seed)
    live, screen_date = live_items(args.seed)
    items += live
    rng = np.random.default_rng(args.seed)
    items = [items[i] for i in rng.permutation(len(items))]
    for n, it in enumerate(items, 1):
        it["id"] = f"{n:02d}"
        it["nbars"] = it["n"]
        it["svg"] = chart_svg(it, BARS, "a", labels=False)
        base_len = (it["formation"].detected_idx - it["formation"].base_high_idx) if it["formation"] else 60
        it["zoom_bars"] = int(min(max(base_len + 12, 50), 120))
        it["svg_zoom"] = chart_svg(it, it["zoom_bars"], "z", labels=True)
    print(f"표본 {len(items)}개: {pd.Series([i['set'] for i in items]).value_counts().to_dict()}  (오늘 스크리닝 {screen_date})")

    if args.ids:
        want = [x.strip() for x in args.ids.split(",")]
        key = pd.read_csv(OUT_KEY, dtype={"id": str}).set_index("id")
        for it in items:                                             # 데이터가 갱신돼 표본이 달라졌으면 번호가 어긋나므로 중단한다
            if it["id"] in want and (key.at[it["id"], "ticker"], key.at[it["id"], "asof"]) != (it["ticker"], it["asof"]):
                sys.exit(f"표본이 전체 시트와 달라졌습니다(#{it['id']}). 전체 시트부터 다시 만들어야 합니다.")
        items = [it for it in items if it["id"] in want]
        out = OUT_HTML.replace("label_sheet.html", "label_sheet_subset.html")
        html = Environment(autoescape=True).from_string(PAGE).render(
            items=items, examples=example_items(), sheet_id=f"v2s{args.seed}sub{len(items)}")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"부분 시트 {len(items)}개: {out}")
        return

    pj = [None] * len(items)
    if args.keep_jev and os.path.exists(OUT_KEY):                    # 레이아웃만 고칠 때 Jev를 다시 부르지 않는다
        old = pd.read_csv(OUT_KEY).dropna(subset=["p_jev"])
        known = {(r.ticker, r.asof): r.p_jev for r in old.itertuples()}
        pj = [known.get((it["ticker"], it["asof"])) for it in items]
        print(f"Jev 판정 {sum(x is not None for x in pj)}건 재사용")
    elif not args.no_jev:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        if os.getenv("AI_GATEWAY_API_KEY") or os.getenv("TYPESAFE_API_KEY"):
            idx = [i for i, it in enumerate(items) if it["formation"] is not None]
            res = asyncio.run(jev_probe([jev_state(items[i]) for i in idx]))
            for i, r in zip(idx, res):
                pj[i] = r
            print(f"Jev 응답 {sum(r is not None for r in res)}/{len(res)}건")
        else:
            print("Jev API 키 없음. Jev 판정은 건너뜀")

    rows = []
    for it, p in zip(items, pj):
        a, f = it["analysis"], it["formation"]
        rows.append({
            "id": it["id"], "set": it["set"], "ticker": it["ticker"], "asof": it["asof"],
            "total": a.score.total if a and a.score else np.nan, "grade": (a.score.grade or "-") if a and a.score else "",
            "state": a.state if a else "", "eligible": bool(a.eligible) if a else False,
            "n_contractions": len(f.contractions) if f else 0,
            "depths": "/".join(f"{c.depth_pct:.1f}" for c in f.contractions) if f else "",
            "pivot": f.pivot if f else np.nan,
            "dist_pivot_pct": a.distance_to_pivot * 100 if a and a.distance_to_pivot is not None else np.nan,
            "r1": it.get("r1", np.nan), "p_jev": p,
            **({f"p_{k}": c.points for k, c in a.score.components.items()} if a and a.score else {}),
        })
    pd.DataFrame(rows).to_csv(OUT_KEY, index=False, encoding="utf-8-sig")

    html = Environment(autoescape=True).from_string(PAGE).render(
        items=items, examples=example_items(), sheet_id=f"v2s{args.seed}n{len(items)}")
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"시트: {OUT_HTML}\n대응표(라벨링 후에 볼 것): {OUT_KEY}")


if __name__ == "__main__":
    main()
