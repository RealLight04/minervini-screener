# -*- coding: utf-8 -*-
"""app/vcp_tracker.py 시험. 메모리 SQLite와 합성 가격을 하루씩 재생해서 형성 추적 규칙을 확인한다.

확인하는 규칙
  신원      같은 베이스는 한 행. first_detected 불변. 같은 날 두 번 돌려도 같음. 닫힌 형성은 같은 key로 다시 안 만든다
  돌파      종가가 피벗 위면 돌파(거래량 조건 없음). 거래량은 비율과 등급만. 첫 돌파 기록은 바뀌지 않는다
  돌파 뒤   종가가 피벗 아래면 FAILED_BREAKOUT, 베이스 저점 아래면 INVALIDATED, 10거래일 지나면 추적 종료
  유지/탈락 점수 45 미만이면 탈락(60 미만은 신규 등록 불가), 추세 이탈은 grace 2일, 다른 베이스가 보이면 밀려남
실행(프로젝트 루트): PYTHONUTF8=1 venv/Scripts/python.exe -m unittest tests.test_vcp_tracker -v
"""
import unittest
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, screener, vcp
from app.database import Base
from app.vcp_tracker import update_formation_outcomes, update_vcp_formations
from tests.synth import make_vcp

VCP2_KEYS = ("vcp2_score", "vcp2_grade", "vcp2_state", "vcp2_pivot", "vcp2_depths", "vcp2_key",
             "vcp2_parts", "vcp2_bvr", "vcp2_age")
TREND_OK = dict(cond_price_above_ma150=True, cond_price_above_ma200=True, cond_ma150_above_ma200=True,
                cond_ma200_uptrend=True, cond_price_above_ma50=True, cond_rs_rank=True)


class Base_(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def load(self, **kw):
        """합성 일봉을 DB에 넣고 (데이터, 날짜 목록, 종목)을 돌려준다."""
        d = make_vcp([22, 11, 5], seed=1, **kw)
        n = len(d["c"])
        self.dates = [t.date() for t in pd.bdate_range(end="2026-09-30", periods=n)]
        self.d = d
        self.opens = np.r_[d["c"][0], d["c"][:-1]]
        self.stock = models.Stock(ticker="SYN", name="SYN", market="US", is_active=True)
        self.db.add(self.stock)
        self.db.flush()
        for i, dt in enumerate(self.dates):
            self.db.add(models.DailyPrice(stock_id=self.stock.id, date=dt, open=float(self.opens[i]), high=float(d["h"][i]),
                                          low=float(d["l"][i]), close=float(d["c"][i]), volume=int(d["v"][i])))
        self.db.commit()
        return d

    def row(self, i, rs=95.0, trend=True, **override):
        df = pd.DataFrame({"open": self.opens[:i + 1], "high": self.d["h"][:i + 1], "low": self.d["l"][:i + 1],
                           "close": self.d["c"][:i + 1], "volume": self.d["v"][:i + 1]}, index=self.dates[:i + 1])
        fields = dict.fromkeys(VCP2_KEYS)
        fields.update(screener.vcp2_fields(df.tail(370), [True] * 8, rs))
        conds = dict(TREND_OK)
        if not trend:
            conds["cond_price_above_ma50"] = False
        r = SimpleNamespace(stock_id=self.stock.id, close=float(self.d["c"][i]), signal=None, rs_rank=rs, **conds, **fields)
        for k, v in override.items():
            setattr(r, k, v)
        return r

    def step(self, i, **kw):
        return update_vcp_formations(self.db, self.dates[i], rows=[self.row(i, **kw)])

    def replay(self, a, b):
        for i in range(a, b + 1):
            self.step(i)

    def forms(self):
        return self.db.query(models.VCPFormation).order_by(models.VCPFormation.id).all()


class TestIdentityAndLifecycle(Base_):
    def test_one_row_per_base_and_first_detected_is_fixed(self):
        d = self.load()
        pre = d["pre_idx"]
        self.replay(pre - 5, pre)
        f = self.forms()
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].first_detected, self.dates[pre - 5])        # 처음 포착한 날 그대로
        self.assertEqual(f[0].last_seen, self.dates[pre])
        base_i = int(np.argmax(d["h"][:pre + 1]))
        self.assertEqual(f[0].key_date, self.dates[base_i])                # 신원 = 베이스 고점 날짜
        self.assertEqual(f[0].status, vcp.NEAR_PIVOT)
        self.assertEqual(f[0].base_seq, 1)
        self.assertEqual(f[0].contractions, 3)
        self.assertLessEqual(f[0].base_low, float(d["l"][base_i:pre + 1].min()) + 1e-9)

    def test_same_day_twice_is_idempotent(self):
        d = self.load()
        pre = d["pre_idx"]
        self.replay(pre - 3, pre)
        before = [(x.id, x.status, x.score_last, x.last_seen, x.first_detected) for x in self.forms()]
        self.step(pre)
        self.step(pre)
        after = [(x.id, x.status, x.score_last, x.last_seen, x.first_detected) for x in self.forms()]
        self.assertEqual(before, after)

    def test_breakout_is_recorded_once_with_volume_grade(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.05])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b)
        f = self.forms()[0]
        self.assertEqual(f.status, vcp.BREAKOUT)
        self.assertEqual(f.breakout_date, self.dates[b])
        self.assertAlmostEqual(f.breakout_price, float(d["c"][b]), places=6)
        self.assertAlmostEqual(f.volume_ratio, 1.8, delta=0.05)
        self.assertEqual(f.volume_quality, "매우 좋음")
        self.assertIsNotNone(f.breakout_score)
        record = (f.breakout_date, f.breakout_price, f.volume_ratio, f.breakout_score)
        self.replay(b + 1, b + 2)                                         # 돌파 뒤 며칠이 지나도 기록은 그대로
        f = self.forms()[0]
        self.assertEqual((f.breakout_date, f.breakout_price, f.volume_ratio, f.breakout_score), record)
        self.assertEqual(len(self.forms()), 1)

    def test_breakout_needs_no_volume(self):
        d = self.load(breakout=dict(pct=0.03, mult=0.8))                    # 거래량이 평소보다 적은 돌파도 돌파다
        self.replay(d["pre_idx"] - 2, d["breakout_idx"])
        f = self.forms()[0]
        self.assertEqual(f.status, vcp.BREAKOUT)
        self.assertEqual(f.volume_quality, "약함")

    def test_failed_breakout_keeps_the_record(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.01, 0.97])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b)
        rec = self.forms()[0].breakout_date
        self.replay(b + 1, b + 3)
        f = self.forms()
        self.assertEqual(len(f), 1)                                       # 같은 형성을 새로 만들지 않음
        self.assertEqual(f[0].status, vcp.FAILED_BREAKOUT)
        self.assertEqual(f[0].breakout_date, rec)
        self.assertIsNone(f[0].resolved_date)                             # 아직 추적 기간 안

    def test_invalidated_when_close_breaks_base_low(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 0.9, 0.7])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b + 3)
        f = self.forms()[0]
        self.assertEqual(f.status, vcp.INVALIDATED)
        self.assertEqual(f.end_reason, "base_broken")
        self.assertIsNotNone(f.resolved_date)
        self.assertEqual(f.breakout_date, self.dates[b])                  # 돌파 기록은 남음

    def test_tracking_ends_after_follow_days(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04] * 12)
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b + 12)
        f = self.forms()[0]
        self.assertIsNotNone(f.resolved_date)
        self.assertEqual(f.end_reason, "follow_done")
        self.assertEqual(f.status, vcp.BREAKOUT)
        self.assertEqual(f.resolved_date, self.dates[b + 10])             # 돌파 뒤 10거래일째

    def test_closed_formation_is_not_recreated(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 0.9, 0.7])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b + 3)
        old = self.row(pre)                                               # 같은 key의 돌파 전 모습이 다시 들어와도
        update_vcp_formations(self.db, self.dates[b + 3], rows=[old])
        self.assertEqual(len(self.forms()), 1)
        self.assertEqual(self.forms()[0].status, vcp.INVALIDATED)

    def test_late_detection_backdates_the_breakout(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04])
        b = d["breakout_idx"]
        self.step(b + 1)                                                  # 돌파 하루 뒤에 처음 포착
        f = self.forms()
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].breakout_date, self.dates[b])
        self.assertEqual(f[0].first_detected, self.dates[b + 1])
        self.assertAlmostEqual(f[0].volume_ratio, 1.8, delta=0.05)

    def test_old_breakout_is_not_registered(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.05, 1.06, 1.07, 1.08])
        self.step(d["breakout_idx"] + 5)                                  # 돌파 5거래일 뒤: 새로 올리지 않음
        self.assertEqual(self.forms(), [])


class TestRules(Base_):
    def fake(self, key, score, state="NEAR_PIVOT", pivot=100.0, close=98.0, age=None, **kw):
        base = dict(stock_id=self.stock.id, close=close, signal=None, rs_rank=90.0, **TREND_OK,
                    vcp2_score=score, vcp2_grade="B", vcp2_state=state, vcp2_pivot=pivot, vcp2_depths="20.0/9.0/4.0",
                    vcp2_key=key, vcp2_parts="{}", vcp2_bvr=None, vcp2_age=age)
        base.update(kw)
        return SimpleNamespace(**base)

    def setUp(self):
        super().setUp()
        self.load()
        self.k1 = self.dates[100]
        self.k2 = self.dates[120]
        self.day = lambda i: self.dates[200 + i]

    def run_row(self, i, r):
        return update_vcp_formations(self.db, self.day(i), rows=[r])

    def test_below_candidate_score_is_not_registered(self):
        self.run_row(0, self.fake(self.k1, 59.9))
        self.assertEqual(self.forms(), [])
        self.run_row(1, self.fake(self.k1, 60.0))
        self.assertEqual(len(self.forms()), 1)

    def test_trend_gate_blocks_registration(self):
        r = self.fake(self.k1, 80, cond_price_above_ma200=False)
        self.run_row(0, r)
        self.assertEqual(self.forms(), [])

    def test_hold_threshold_absorbs_a_dip_but_not_a_collapse(self):
        self.run_row(0, self.fake(self.k1, 70))
        self.run_row(1, self.fake(self.k1, 50))                            # 60 아래지만 45 이상: 유지
        f = self.forms()[0]
        self.assertIsNone(f.resolved_date)
        self.assertEqual(f.score_peak, 70)
        self.assertEqual(f.score_last, 50)
        self.run_row(2, self.fake(self.k1, 40))
        f = self.forms()[0]
        self.assertEqual((f.status, f.end_reason), (vcp.INVALIDATED, "score_low"))

    def test_trend_grace_is_two_days(self):
        self.run_row(0, self.fake(self.k1, 70))
        for i in (1, 2):
            self.run_row(i, self.fake(self.k1, 70, cond_price_above_ma50=False))
            self.assertIsNone(self.forms()[0].resolved_date)
        self.run_row(3, self.fake(self.k1, 70, cond_price_above_ma50=False))
        f = self.forms()[0]
        self.assertEqual((f.status, f.end_reason), (vcp.INVALIDATED, "trend_lost"))

    def test_trend_recovery_resets_grace(self):
        self.run_row(0, self.fake(self.k1, 70))
        self.run_row(1, self.fake(self.k1, 70, cond_price_above_ma50=False))
        self.run_row(2, self.fake(self.k1, 70))
        self.assertEqual(self.forms()[0].trend_grace, 0)

    def test_new_base_supersedes_and_gets_next_seq(self):
        self.run_row(0, self.fake(self.k1, 70))
        self.run_row(1, self.fake(self.k2, 72))
        f = self.forms()
        self.assertEqual(len(f), 2)
        self.assertEqual((f[0].status, f[0].end_reason), (vcp.INVALIDATED, "superseded"))
        self.assertEqual((f[1].key_date, f[1].base_seq, f[1].resolved_date), (self.k2, 2, None))

    def test_formation_that_disappears_goes_stale(self):
        self.run_row(0, self.fake(self.k1, 70))
        gone = self.fake(None, None, vcp2_state=None, vcp2_pivot=None, vcp2_depths=None)
        update_vcp_formations(self.db, self.dates[200], rows=[gone])
        self.assertIsNone(self.forms()[0].resolved_date)                  # 며칠 안 보이는 건 흡수
        update_vcp_formations(self.db, self.dates[200 + 30], rows=[gone])
        self.assertEqual(self.forms()[0].end_reason, "stale")

    def others(self, rs_rank, n=25, cond_rs=True):
        """같은 시장의 다른 종목 n개의 오늘 행(형성 없음)."""
        rows = []
        for k in range(n):
            s = models.Stock(ticker=f"X{k}", market="US", is_active=True)
            self.db.add(s)
            self.db.flush()
            conds = dict(TREND_OK, cond_rs_rank=cond_rs)
            rows.append(SimpleNamespace(stock_id=s.id, close=10.0, signal=None, rs_rank=rs_rank, vcp2_score=None,
                                        vcp2_key=None, vcp2_state=None, vcp2_age=None, **conds))
        return rows

    def test_rs_collection_failure_day_does_not_kill_formations(self):
        # 2026-08~09에 실제로 있었던 일: 그날 거의 모든 종목의 RS가 0으로 저장됨 -> 추세 게이트가 전부 실패
        self.run_row(0, self.fake(self.k1, 70))
        others = self.others(0.0, cond_rs=False)
        for i in range(1, 7):                                              # 유예(2일)보다 길게 이어져도
            mine = self.fake(self.k1, 52, rs_rank=0.0, cond_rs_rank=False)   # 내 종목도 RS 0, 점수도 RS 때문에 떨어짐
            update_vcp_formations(self.db, self.day(i), rows=[mine] + others)
        f = self.forms()[0]
        self.assertIsNone(f.resolved_date)                                 # 닫히지 않음
        self.assertEqual(f.trend_grace, 0)
        self.assertEqual(f.score_last, 70)                                 # 믿을 수 없는 점수로 스냅샷을 덮지 않음
        self.run_row(8, self.fake(self.k1, 72))                            # RS가 돌아오면 정상 갱신
        self.assertEqual(self.forms()[0].score_last, 72)

    def test_rs_failure_day_still_applies_price_rules(self):
        self.run_row(0, self.fake(self.k1, 70, pivot=100.0, close=98.0))
        others = self.others(0.0, cond_rs=False)
        mine = self.fake(self.k1, 52, rs_rank=0.0, cond_rs_rank=False, close=101.0)   # 종가가 피벗 위
        update_vcp_formations(self.db, self.day(1), rows=[mine] + others)
        f = self.forms()[0]
        self.assertIsNotNone(f.breakout_date)                              # 가격으로 판정되는 돌파는 기록
        crash = self.fake(self.k1, 52, rs_rank=0.0, cond_rs_rank=False, close=1.0)    # 베이스 저점 아래로 마감
        update_vcp_formations(self.db, self.day(2), rows=[crash] + others)
        f = self.forms()[0]
        self.assertEqual((f.status, f.end_reason), (vcp.INVALIDATED, "base_broken"))   # RS가 깨진 날에도 가격 판정은 한다

    def test_no_new_registration_on_rs_failure_day(self):
        others = self.others(0.0, cond_rs=False)
        update_vcp_formations(self.db, self.day(0), rows=[self.fake(self.k1, 80, rs_rank=0.0)] + others)
        self.assertEqual(self.forms(), [])

    def test_small_samples_do_not_count_as_broken(self):
        # 시장 행이 20개 미만이면 비율로 판단하지 않는다(테스트나 신규 시장에서 오탐 방지)
        self.run_row(0, self.fake(self.k1, 70))
        for i in (1, 2, 3):
            self.run_row(i, self.fake(self.k1, 70, cond_price_above_ma50=False))
        self.assertEqual(self.forms()[0].end_reason, "trend_lost")

    def test_skipped_stocks_are_untouched(self):
        update_vcp_formations(self.db, self.day(0), rows=[self.fake(self.k1, 70)], skip_stock_ids={self.stock.id})
        self.assertEqual(self.forms(), [])

    def test_data_error_rows_are_ignored(self):
        update_vcp_formations(self.db, self.day(0), rows=[self.fake(self.k1, 70, signal="DATA")])
        self.assertEqual(self.forms(), [])

    def test_unique_constraint_on_stock_and_key(self):
        self.run_row(0, self.fake(self.k1, 70))
        self.db.add(models.VCPFormation(stock_id=self.stock.id, key_date=self.k1, first_detected=self.day(0),
                                        last_seen=self.day(0)))
        with self.assertRaises(Exception):
            self.db.commit()
        self.db.rollback()


class TestOutcomes(Base_):
    def test_target_and_stop_are_settled_with_the_product_rules(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.30])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b)
        update_formation_outcomes(self.db, self.dates[b + 2])
        f = self.forms()[0]
        self.assertEqual(f.outcome, "target")                               # 진입가의 +20%에 닿음
        self.assertAlmostEqual(f.outcome_pct, 20.0, delta=1.0)

    def test_stop(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04, 0.9])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b)
        update_formation_outcomes(self.db, self.dates[b + 2])
        f = self.forms()[0]
        self.assertEqual(f.outcome, "stop")
        self.assertLess(f.outcome_pct, 0)

    def test_pending_until_resolved(self):
        d = self.load(breakout=dict(pct=0.03, mult=1.8), after=[1.04])
        pre, b = d["pre_idx"], d["breakout_idx"]
        self.replay(pre - 3, b)
        update_formation_outcomes(self.db, self.dates[b + 1])
        self.assertIsNone(self.forms()[0].outcome)


if __name__ == "__main__":
    unittest.main()
