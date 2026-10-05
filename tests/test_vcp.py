# -*- coding: utf-8 -*-
"""app/vcp.py 시험. 표준 라이브러리 unittest만 쓴다.

실행(프로젝트 루트에서): PYTHONUTF8=1 venv/Scripts/python.exe -m unittest discover -s tests -t . -v

세 층으로 나눴다.
  TestComponents   점수 항목 하나씩(다른 항목과 독립)
  TestRuleScenarios  규칙 문서의 시험 6종
  TestDetection    책 사례(GRA), 깊이 왕복, 베이스 없음 같은 탐지 자체
이 시험이 보증하는 건 "코드가 규칙대로 동작한다"까지다. 실제 차트에서 사람 눈과 맞는지는 별도 라벨 검증으로 본다.
"""
import unittest

import numpy as np

from app import vcp
from tests.synth import GRA_BOOK_DEPTHS, build_gra, make_vcp, trim


def run(d, rs=92):
    return vcp.analyze(d["h"], d["l"], d["c"], d["v"], rs=rs)


class TestComponents(unittest.TestCase):
    def test_contraction_order(self):
        good = vcp.score_contraction([0.25, 0.12, 0.07, 0.03]).points
        mid = vcp.score_contraction([0.25, 0.18, 0.15]).points
        expand = vcp.score_contraction([0.25, 0.30, 0.20]).points
        single = vcp.score_contraction([0.20]).points
        self.assertGreaterEqual(good, 17)
        self.assertLess(mid, good - 3)             # 규칙 문서: 25→18→15는 수축이 있지만 낮은 점수
        self.assertGreater(mid, expand)
        self.assertLessEqual(expand, 10)           # 수축폭 확대는 낮은 점수
        self.assertLessEqual(single, 2)            # 수축 1개는 낮게

    def test_contraction_no_fixed_numbers(self):
        # 20→10→5 같은 숫자에 묶이지 않는다: 비율이 달라도 줄어들기만 하면 높은 점수
        a = vcp.score_contraction([0.20, 0.10, 0.05]).points
        b = vcp.score_contraction([0.30, 0.12, 0.06]).points
        c = vcp.score_contraction([0.18, 0.11, 0.07, 0.04]).points
        for x in (a, b, c):
            self.assertGreaterEqual(x, 17)

    def test_contraction_count_not_forced(self):
        # 수축 2개도 후보다(3개 이상을 강제하지 않는다), 3~4개가 더 높다
        two = vcp.score_contraction([0.20, 0.08]).points
        three = vcp.score_contraction([0.20, 0.10, 0.05]).points
        self.assertGreater(two, 8)
        self.assertGreater(three, two)

    def test_volume_shrink(self):
        shrinking = vcp.score_volume([10e6, 7e6, 4e6], 0.6).points
        growing = vcp.score_volume([4e6, 7e6, 10e6], 0.6).points
        self.assertGreaterEqual(shrinking, 12)
        self.assertLess(growing, 8)

    def test_volume_dry_up_is_graded_not_required(self):
        dry = vcp.score_volume([10e6, 7e6, 4e6], 0.55)
        wet = vcp.score_volume([10e6, 7e6, 4e6], 1.1)
        self.assertGreater(dry.points, wet.points)
        self.assertGreater(wet.points, 0)          # 안 말라도 0점으로 제외하지 않는다
        self.assertEqual(vcp.score_volume([None, None], None).points, 0)

    def test_higher_low(self):
        self.assertEqual(vcp.score_higher_low([100, 105, 108]).points, 5)
        self.assertLess(vcp.score_higher_low([100, 95, 90]).points, 1.5)
        self.assertEqual(vcp.score_higher_low([100]).points, 0)

    def test_tightness_bands(self):
        self.assertGreaterEqual(vcp.score_tightness(0.02, 0.025).points, 9.5)
        self.assertLess(vcp.score_tightness(0.10, 0.15).points, 2)
        a = vcp.score_tightness(0.03, 0.04).points
        b = vcp.score_tightness(0.05, 0.06).points
        c = vcp.score_tightness(0.08, 0.09).points
        self.assertGreater(a, b)
        self.assertGreater(b, c)

    def test_proximity_bands(self):
        self.assertEqual(vcp.score_proximity(0.01).points, 5)
        self.assertGreater(vcp.score_proximity(0.04).points, vcp.score_proximity(0.06).points)
        self.assertLess(vcp.score_proximity(0.10).points, 2)
        self.assertEqual(vcp.score_proximity(-0.01).points, 5)       # 막 돌파
        self.assertLess(vcp.score_proximity(-0.08).points, 2)        # 많이 연장

    def test_rs_bands(self):
        self.assertGreaterEqual(vcp.score_rs(95).points, 9)
        self.assertLess(vcp.score_rs(45).points, 2.5)
        self.assertGreater(vcp.score_rs(85).points, vcp.score_rs(75).points)
        self.assertEqual(vcp.score_rs(None).points, 0)

    def test_trend_ratio(self):
        up = np.linspace(50, 100, 300)
        r, _ = vcp.trend_ratio(up * 1.01, up * 0.99, up)
        self.assertEqual(r, 1.0)
        down = np.linspace(100, 50, 300)
        r, _ = vcp.trend_ratio(down * 1.01, down * 0.99, down)
        self.assertEqual(r, 0.0)

    def test_total_is_scaled_to_100(self):
        # 규칙 문서 점수표의 항목 합이 95라서 총점은 100점으로 환산한다
        self.assertEqual(vcp.MAX_RAW, 95)
        s = run(make_vcp([22, 11, 5])).score
        self.assertAlmostEqual(s.total, s.raw * 100 / vcp.MAX_RAW, delta=0.06)
        self.assertAlmostEqual(sum(c.points for c in s.components.values()), s.raw, delta=0.06)

    def test_grade_edges(self):
        for total, g in ((95, "A+"), (90, "A+"), (89.9, "A"), (80, "A"), (79.9, "B"), (70, "B"), (69.9, "C"), (60, "C"), (59.9, None)):
            self.assertEqual(vcp.grade_of(total), g, total)

    def test_component_points_never_exceed_max(self):
        a = run(make_vcp([18, 9, 4], vol_decay=0.6, breakout=dict(pct=0.03, mult=2.0)), rs=99)
        for c in a.score.components.values():
            self.assertLessEqual(c.points, c.max_points + 1e-9, c.name)
        self.assertLessEqual(a.score.total, 100)


class TestRuleScenarios(unittest.TestCase):
    """규칙 문서 24장의 시험 6종."""

    def test1_normal_vcp(self):
        a = run(make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.035, 1.04]))
        self.assertEqual(a.state, vcp.BREAKOUT)
        self.assertTrue(a.eligible)
        self.assertGreaterEqual(a.score.total, 80)
        self.assertGreater(a.score.components["volume"].points, 12)       # 거래량 감소
        self.assertEqual(a.score.components["higher_low"].points, 5)      # 저점 상승
        self.assertEqual(a.score.components["tightness"].points, 10)      # 타이트함
        self.assertGreaterEqual(a.breakout.quality, 3)                    # 돌파 거래량 1.5배 이상

    def test2_expanding_contractions(self):
        good = run(make_vcp([22, 11, 5])).score
        # T3가 T2보다 커진 경우
        regrow = run(make_vcp([20, 8, 14])).score
        self.assertLess(regrow.components["contraction"].points, 11)
        self.assertLess(regrow.total, good.total - 8)
        # T1 < T2 < T3로 계속 깊어지면 가장 깊은 저점까지가 T1이 되어 수축 하나뿐이고, 정상 VCP보다 크게 낮은 점수를 받는다
        deeper = run(make_vcp([10, 22, 30]))
        self.assertEqual(len(deeper.formation.contractions), 1)
        self.assertLessEqual(deeper.score.components["contraction"].points, 2)
        self.assertLess(deeper.score.total, good.total - 25)

    def test3_breakout_without_volume(self):
        weak = run(make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.0)))
        strong = run(make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8)))
        self.assertEqual(weak.state, vcp.BREAKOUT)                        # 돌파는 감지한다
        self.assertLessEqual(weak.breakout.quality, 1)                    # 품질은 낮게 평가한다
        self.assertGreater(strong.breakout.quality, weak.breakout.quality)
        # VCP 점수와 돌파 점수는 분리: 돌파 거래량이 달라도 VCP 점수는 같다
        self.assertAlmostEqual(weak.score.total, strong.score.total, places=1)

    def test4_strong_vcp_and_strong_breakout(self):
        a = run(make_vcp([18, 9, 4], vol_decay=0.6, breakout=dict(pct=0.03, mult=2.0)), rs=97)
        self.assertGreater(a.score.total, 90)
        self.assertEqual(a.score.grade, "A+")
        self.assertGreater(a.breakout.volume_ratio, 1.5)
        self.assertEqual(a.state, vcp.BREAKOUT)
        self.assertEqual(a.breakout.quality_label, "매우 강함")

    def test5_formation_identity(self):
        d = make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.035, 1.04])
        pre = d["pre_idx"]
        keys = {vcp.detect(d["h"], d["l"], d["c"], d["v"], asof=t).key for t in range(pre - 8, pre + 1)}
        self.assertEqual(len(keys), 1)                                    # 같은 형성에 새 ID가 계속 생기지 않는다
        f_pre = vcp.detect(d["h"], d["l"], d["c"], d["v"], asof=pre)
        # 돌파 당일과 돌파 뒤에도 같은 형성으로 이어진다
        for upto in (d["breakout_idx"] + 1, len(d["c"])):
            t = trim(d, upto)
            f, _ = vcp.detect_latest(t["h"], t["l"], t["c"], t["v"])
            self.assertEqual(f.key, f_pre.key, upto)
        # 레지스트리가 저장한 형성을 넘기면 그대로 이어서 평가한다
        a = vcp.analyze(d["h"], d["l"], d["c"], d["v"], rs=92, formation=f_pre)
        self.assertEqual(a.formation.key, f_pre.key)
        self.assertEqual(a.state, vcp.BREAKOUT)
        # 돌파 뒤 새 고점을 만들고 다시 베이스를 쌓으면 그건 새 형성이다
        climb = list(np.linspace(1.03, 1.25, 25))
        consolidate = list(np.linspace(1.25, 1.15, 20) + 0.01 * np.sin(np.arange(20) * 1.3))
        d2 = make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=climb + consolidate)
        f2, _ = vcp.detect_latest(d2["h"], d2["l"], d2["c"], d2["v"])
        self.assertGreater(f2.key, f_pre.key)

    def test6_breakout_failure(self):
        base = make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.04])
        f = vcp.detect(base["h"], base["l"], base["c"], base["v"], asof=base["pre_idx"])
        state, rec = vcp.evaluate_state(f, base["c"], base["v"])
        self.assertEqual(state, vcp.BREAKOUT)
        # 같은 형성이 돌파 뒤 피벗 아래로 내려가면 FAILED_BREAKOUT, 돌파 기록은 그대로 남는다
        fail = make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.01, 0.97])
        state2, rec2 = vcp.evaluate_state(f, fail["c"], fail["v"])
        self.assertEqual(state2, vcp.FAILED_BREAKOUT)
        self.assertEqual(rec2.idx, rec.idx)
        self.assertAlmostEqual(rec2.volume_ratio, rec.volume_ratio, places=6)
        # 베이스 저점 아래로 무너지면 INVALIDATED
        crash = make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.04, 0.9, 0.7])
        self.assertEqual(vcp.evaluate_state(f, crash["c"], crash["v"])[0], vcp.INVALIDATED)

    def test_states_before_breakout(self):
        near = run(make_vcp([22, 11, 5]))
        far = run(make_vcp([22, 11, 5], final=0.90))
        self.assertEqual(near.state, vcp.NEAR_PIVOT)
        self.assertEqual(far.state, vcp.WATCH)
        self.assertIsNone(near.breakout)

    def test_breakout_record_fields(self):
        a = run(make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8)))
        r = a.breakout
        self.assertAlmostEqual(r.price / r.pivot, 1.03, delta=0.002)
        self.assertAlmostEqual(r.volume_ratio, 1.8, places=2)
        self.assertAlmostEqual(r.volume / r.average_volume, r.volume_ratio, places=6)
        self.assertEqual(a.breakout_age, 0)
        later = run(make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.05, 1.07]))
        self.assertEqual(later.breakout_age, 3)
        self.assertLess(later.distance_to_pivot, -0.05)                   # 피벗 위로 5% 넘게 연장
        self.assertIsNone(run(make_vcp([22, 11, 5])).breakout_age)

    def test_too_deep_base_is_not_a_formation(self):
        # 150일 고점에서 35% 넘게 빠진 종목은 후보였던 적이 없으므로 INVALIDATED가 아니라 형성 없음
        d = make_vcp([40, 18, 8])
        a = run(d)
        self.assertIsNone(a.formation)
        self.assertEqual(a.reason, "베이스가 너무 깊음")
        self.assertIsNone(a.state)
        self.assertFalse(a.eligible)

    def test_works_without_volume(self):
        d = make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8))
        a = vcp.analyze(d["h"], d["l"], d["c"], None, rs=92)
        self.assertEqual(a.state, vcp.BREAKOUT)
        self.assertIsNone(a.breakout.volume_ratio)
        self.assertEqual(a.score.components["volume"].points, 0)


class TestDetection(unittest.TestCase):
    def test_gra_book_case(self):
        # 책 그림 10.5의 T1~T3를 사진에서 읽은 값(29, 11, 10%)과 비교. 사진 읽기 오차를 감안해 허용폭이 크다
        for seed in range(10):
            d = build_gra(seed)
            f = vcp.detect(d["h"], d["l"], d["c"])
            self.assertIsNotNone(f, seed)
            got = [c.depth_pct for c in f.contractions]
            self.assertGreaterEqual(len(got), 3, (seed, got))
            for g, b in zip(got[:3], GRA_BOOK_DEPTHS):
                self.assertLessEqual(abs(g - b), 5, (seed, got))

    def test_depth_round_trip(self):
        for depths in ([25, 12, 6], [30, 15, 8, 4], [20, 10]):
            for noise in (0.002, 0.005):
                for seed in range(6):
                    # 수축마다 고점이 3%씩 낮아지게 한다. 1%면 잡음 스파이크가 다음 고점을 베이스 고점보다 높게 만든다
                    d = make_vcp(depths, noise=noise, seed=seed, highs_drop=0.03)
                    f = vcp.detect(d["h"], d["l"], d["c"])
                    got = [c.depth_pct for c in f.contractions]
                    # 마지막 타이트 구간의 잡음이 3%를 넘으면 더 작은 수축이 하나 더 읽힐 수 있다(실제 차트에서도 그렇게 센다)
                    self.assertIn(len(got), (len(depths), len(depths) + 1), (depths, noise, seed, got))
                    if len(got) > len(depths):
                        self.assertLess(got[-1], depths[-1], (depths, noise, seed, got))
                    for g, w in zip(got, depths):
                        self.assertLessEqual(abs(g - w), 2.5, (depths, noise, seed, got))

    def test_pivot_is_last_contraction_top(self):
        d = make_vcp([22, 11, 5])
        f = vcp.detect(d["h"], d["l"], d["c"])
        self.assertAlmostEqual(f.pivot, d["pivot"], delta=0.3)          # 고점 봉에 잡음이 얹혀 몇 bp 어긋난다
        self.assertEqual(f.pivot, f.contractions[-1].peak_price)

    def test_contraction_fields(self):
        d = make_vcp([22, 11, 5])
        f = vcp.detect(d["h"], d["l"], d["c"], d["v"])
        for c in f.contractions:
            self.assertGreater(c.duration, 0)
            self.assertGreater(c.peak_price, c.low_price)
            self.assertIsNotNone(c.avg_volume)
        vols = [c.avg_volume for c in f.contractions]
        self.assertGreater(vols[0], vols[1])
        self.assertGreater(vols[1], vols[2])

    def test_no_base_when_making_new_highs(self):
        up = np.linspace(50, 100, 200)
        self.assertIsNone(vcp.detect(up * 1.01, up * 0.99, up))
        a = vcp.analyze(up * 1.01, up * 0.99, up, None)
        self.assertFalse(a.eligible)
        self.assertEqual(a.reason, vcp.RECENT_HIGH)

    def test_short_history(self):
        x = np.linspace(10, 20, 30)
        self.assertIsNone(vcp.detect(x, x, x))

    def test_box_range_scores_lower_structure(self):
        good = run(make_vcp([22, 11, 5])).score.components["contraction"].points
        box = run(make_vcp([15, 14, 16, 13])).score.components["contraction"].points
        self.assertLess(box, good - 5)

    def test_volume_decay_raises_score(self):
        shrinking = run(make_vcp([22, 11, 5], vol_decay=0.7)).score
        flat = run(make_vcp([22, 11, 5], vol_decay=1.0, dry=1.0)).score
        self.assertGreater(shrinking.total, flat.total + 5)
        self.assertGreater(flat.components["volume"].points, -1)          # 거래량이 안 줄어도 제외하지 않는다
        self.assertTrue(run(make_vcp([22, 11, 5], vol_decay=1.0, dry=1.0)).eligible)

    def test_score_uses_pre_breakout_picture(self):
        pre = run(make_vcp([22, 11, 5])).score.total
        post = run(make_vcp([22, 11, 5], breakout=dict(pct=0.03, mult=1.8), after=[1.04, 1.05])).score.total
        self.assertAlmostEqual(pre, post, places=1)


if __name__ == "__main__":
    unittest.main()
