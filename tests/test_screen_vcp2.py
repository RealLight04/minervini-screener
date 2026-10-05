# -*- coding: utf-8 -*-
"""screen_stock에 붙인 VCP 점수 모델(그림자 모드) 시험. 메모리 SQLite만 쓰고 운영 DB는 건드리지 않는다.

지키려는 것
  1. 합성 VCP 일봉을 넣으면 vcp2_* 컬럼이 채워진다
  2. 그림자 모드: vcp2 계산이 있든 없든(실패하든) 기존 신호, 피벗, 손절은 똑같다
  3. 점수 모델이 예외를 내도 스크리닝은 멈추지 않고 vcp2 컬럼만 비어 있다
실행(프로젝트 루트): PYTHONUTF8=1 venv/Scripts/python.exe -m unittest tests.test_screen_vcp2 -v
"""
import json
import unittest
from datetime import date
from unittest import mock

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, screener, vcp  # noqa: F401
from app.database import Base
from tests.synth import make_vcp

SHADOW_FIELDS = ("signal", "signal_reason", "pivot_price", "stop_loss", "vcp_detected", "vcp_contractions",
                 "vcp_pivot", "technical_pass", "final_pass", "vcp_volume_dryup")


def load(db, ticker, d, ohlc_dates):
    st = models.Stock(ticker=ticker, name=ticker, market="US", is_active=True)
    db.add(st)
    db.flush()
    prev = float(d["c"][0])
    for i, dt in enumerate(ohlc_dates):
        c = float(d["c"][i])
        db.add(models.DailyPrice(stock_id=st.id, date=dt.date(), open=prev, high=float(d["h"][i]),
                                 low=float(d["l"][i]), close=c, volume=int(d["v"][i])))
        prev = c
    db.commit()
    return st


class TestScreenVcp2(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.d = make_vcp([22, 11, 5], seed=1)
        n = len(self.d["c"])
        self.dates = pd.bdate_range(end=pd.Timestamp(date.today()), periods=n)
        self.stock = load(self.db, "SYN", self.d, self.dates)
        self.screen_date = self.dates[-1].date()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def run_screen(self):
        return screener.screen_stock(self.db, self.stock, self.screen_date, 95.0)

    def test_columns_filled(self):
        r = self.run_screen()
        self.assertIsNotNone(r.vcp2_score)
        self.assertGreaterEqual(r.vcp2_score, 60)
        self.assertIn(r.vcp2_grade, ("A+", "A", "B", "C"))
        self.assertEqual(r.vcp2_state, vcp.NEAR_PIVOT)
        self.assertEqual(len(r.vcp2_depths.split("/")), 3)
        self.assertIsNotNone(r.vcp2_pivot)
        self.assertIsNotNone(r.vcp2_key)
        parts = json.loads(r.vcp2_parts)
        self.assertEqual(set(parts), set(vcp.WEIGHTS))
        self.assertIsNone(r.vcp2_age)                     # 돌파 전이라 비어 있음

    def test_key_is_base_high_date(self):
        r = self.run_screen()
        i = int(self.d["h"][:len(self.dates)].argmax())
        self.assertEqual(r.vcp2_key, self.dates[i].date())

    def test_shadow_mode_does_not_change_existing_fields(self):
        with_model = self.run_screen()
        before = {k: getattr(with_model, k) for k in SHADOW_FIELDS}
        with mock.patch.object(screener.vcp_model, "analyze", side_effect=RuntimeError("boom")), \
                self.assertLogs("app.screener", level="ERROR"):
            without = self.run_screen()
        after = {k: getattr(without, k) for k in SHADOW_FIELDS}
        self.assertEqual(before, after)

    def test_failure_is_isolated(self):
        with mock.patch.object(screener.vcp_model, "analyze", side_effect=RuntimeError("boom")), \
                self.assertLogs("app.screener", level="ERROR"):
            r = self.run_screen()
        self.assertIsNone(r.vcp2_score)
        self.assertIsNone(r.vcp2_state)
        self.assertIsNotNone(r.close)                     # 기존 스크리닝 결과는 그대로 채워짐
        self.assertIsNotNone(r.technical_pass)

    def test_breakout_fields(self):
        # 돌파가 막 일어난 종목: 돌파 거래량 비율과 경과일이 기록된다
        db2 = sessionmaker(bind=self.engine)()
        d = make_vcp([22, 11, 5], seed=1, breakout=dict(pct=0.03, mult=1.8), after=[1.035])
        dates = pd.bdate_range(end=pd.Timestamp(date.today()), periods=len(d["c"]))
        st = load(db2, "BRK", d, dates)
        r = screener.screen_stock(db2, st, dates[-1].date(), 95.0)
        self.assertEqual(r.vcp2_state, vcp.BREAKOUT)
        self.assertAlmostEqual(r.vcp2_bvr, 1.8, delta=0.05)
        self.assertEqual(r.vcp2_age, 1)
        db2.close()

    def test_short_history_leaves_columns_empty(self):
        st = models.Stock(ticker="NEW", name="NEW", market="US", is_active=True)
        self.db.add(st)
        self.db.flush()
        for i, dt in enumerate(pd.bdate_range(end=pd.Timestamp(date.today()), periods=40)):
            self.db.add(models.DailyPrice(stock_id=st.id, date=dt.date(), open=10, high=11, low=9, close=10, volume=1000))
        self.db.commit()
        r = screener.screen_stock(self.db, st, date.today(), 50.0)
        self.assertIsNone(r.vcp2_score)


if __name__ == "__main__":
    unittest.main()
