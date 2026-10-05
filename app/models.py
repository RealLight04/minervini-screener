from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, unique=True, nullable=False, index=True)
    name = Column(String)
    sector = Column(String)
    market = Column(String, default="US", index=True)  # US / KOSPI / KOSDAQ
    is_active = Column(Boolean, default=True)

    # 기업 기본정보(밸류에이션·수익성) — yfinance .info 스냅샷
    roe = Column(Float)               # 자기자본이익률 (소수, 0.17 = 17%)
    profit_margin = Column(Float)     # 순이익률 (소수)
    operating_margin = Column(Float)  # 영업이익률 (소수)
    forward_eps = Column(Float)       # 선행 EPS
    trailing_eps = Column(Float)      # 후행 EPS
    target_price = Column(Float)      # 증권사 평균 목표주가
    recommendation = Column(String)   # 투자의견 (buy/hold/sell 등)
    next_earnings = Column(String)    # 예상 실적 발표일 (ISO date)
    eps_rev_up = Column(Integer)      # 최근 30일 EPS 추정치 상향 애널리스트 수(당해연도)
    eps_rev_down = Column(Integer)    # 최근 30일 EPS 추정치 하향 애널리스트 수
    eps_est_chg = Column(Float)       # 당해연도 EPS 컨센서스 90일 변화율(%) — 상향=강세

    prices = relationship("DailyPrice", back_populates="stock", cascade="all, delete-orphan")
    fundamentals = relationship("Fundamental", back_populates="stock", cascade="all, delete-orphan")
    results = relationship("ScreeningResult", back_populates="stock", cascade="all, delete-orphan")


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("stock_id", "date"),)

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float, nullable=False)
    volume = Column(Integer)

    stock = relationship("Stock", back_populates="prices")


class Fundamental(Base):
    """분기/연간 EPS 및 매출 데이터"""
    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("stock_id", "period_type", "period_date"),)

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    period_type = Column(String, nullable=False)   # 'Q' 또는 'Y'
    period_date = Column(Date, nullable=False)
    eps = Column(Float)
    revenue = Column(Float)
    operating_income = Column(Float)          # 영업이익
    operating_margin = Column(Float)          # 영업이익률 (영업이익/매출, %)
    eps_growth_yoy = Column(Float)            # 전년 동기 대비 EPS 증가율 (%)
    revenue_growth_yoy = Column(Float)        # 전년 동기 대비 매출 증가율 (%)
    operating_income_growth_yoy = Column(Float)  # 전년 동기 대비 영업이익 증가율 (%)
    eps_estimated = Column(Float)             # 컨센서스 추정 EPS (Alpha Vantage, 미국)
    eps_surprise_pct = Column(Float)          # 어닝 서프라이즈 (실제 대비 추정 초과율, %)

    stock = relationship("Stock", back_populates="fundamentals")


class ScreeningResult(Base):
    """매일 스크리닝 결과 저장"""
    __tablename__ = "screening_results"
    __table_args__ = (UniqueConstraint("stock_id", "screen_date"),)

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    screen_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 현재 지표
    close = Column(Float)
    ma50 = Column(Float)
    ma150 = Column(Float)
    ma200 = Column(Float)
    ma200_month_ago = Column(Float)   # 1개월 전 200일선 (추세 확인용)
    week52_high = Column(Float)
    week52_low = Column(Float)
    rs_rank = Column(Float)           # RS 백분위 (높을수록 강함, 0~100)

    # 기술적 필터 세부 결과
    cond_price_above_ma150 = Column(Boolean)
    cond_price_above_ma200 = Column(Boolean)
    cond_ma150_above_ma200 = Column(Boolean)
    cond_ma50_above_ma150_200 = Column(Boolean)
    cond_ma200_uptrend = Column(Boolean)
    cond_price_above_ma50 = Column(Boolean)
    cond_above_52w_low_30pct = Column(Boolean)
    cond_within_52w_high_25pct = Column(Boolean)
    cond_rs_rank = Column(Boolean)

    technical_pass = Column(Boolean, default=False)

    # 펀더멘털 필터
    latest_q_eps_growth = Column(Float)
    latest_q_rev_growth = Column(Float)
    annual_eps_uptrend = Column(Boolean)

    fundamental_pass = Column(Boolean, default=False)

    # VCP 탐지
    vcp_detected = Column(Boolean, default=False)
    vcp_contractions = Column(Integer)  # 조정 횟수
    vcp_volume_dryup = Column(Boolean, default=False)  # 베이스 우측 거래량 축소(dry-up)
    vcp_pivot = Column(Float)  # VCP 피벗(돌파선) — 매수신호 여부와 무관하게 저장(레지스트리용)
    vcp_base_low = Column(Float)   # VCP 베이스 저점 — 레지스트리 리셋 판정 입력
    vcp_base_high = Column(Float)  # VCP 베이스 고점 — 레지스트리 정체성 앵커
    vcp_last_contraction = Column(Float)  # 마지막 조정폭(%) — 품질점수 '타이트함' 입력
    # 미너비니식 dry-up: 마지막 수축 구간의 최저 거래량이 베이스 전체에서 가장 낮은가
    # (app.screener.vcp_last_contraction_volume). 아직 점수엔 안 쓰고 기록만 — 표본외 검증용.
    vdu_lowest_in_last = Column(Boolean)
    vdu_last_vs_prior = Column(Float)   # 마지막 수축 최저 / 이전 베이스 최저 (작을수록 조용)
    vdu_last_vs_base = Column(Float)    # 마지막 수축 최저 / 베이스 평균 (작을수록 조용)

    # VCP 점수 모델(app/vcp.py) 결과 — 그림자 모드: 신호·피벗·레지스트리에는 쓰지 않고 기록·표시만 한다.
    # 점수는 '교과서적 모양에 얼마나 가까운가'이지 수익 예측이 아니다(2010~2026 백테스트: 점수와 성과 무관).
    vcp2_score = Column(Float)      # 0~100 모양 적합도
    vcp2_grade = Column(String)     # A+ / A / B / C, 60점 미만은 '-'
    vcp2_state = Column(String)     # WATCH / NEAR_PIVOT / BREAKOUT / FAILED_BREAKOUT / INVALIDATED
    vcp2_pivot = Column(Float)      # 마지막 수축의 상단
    vcp2_depths = Column(String)    # 수축 낙폭(%) 목록 "22/11/5"
    vcp2_key = Column(Date)         # 베이스 고점 날짜 = 형성 신원(같은 베이스면 같은 값)
    vcp2_parts = Column(String)     # 항목별 점수 JSON
    vcp2_bvr = Column(Float)        # 돌파일 거래량 / 직전 50일 평균(돌파가 있을 때만)
    vcp2_age = Column(Integer)      # 돌파 후 지난 거래일(돌파 당일 0)

    # 거래량 / 유동성
    avg_volume = Column(Float)        # 50일 평균 거래량
    vol_vs_avg = Column(Float)        # 최근 거래량 / 50일 평균 (1.0 = 평균)
    accum_days = Column(Integer)      # 최근 25일 매집일수(상승+대량거래)
    distrib_days = Column(Integer)    # 최근 25일 분산일수(하락+대량거래)
    ud_volume_ratio = Column(Float)   # 50일 상승일 거래량합 / 하락일 거래량합 (>1 매집, IBD U/D)
    dryup_ratio = Column(Float)       # 최근10일 / 50일 평균 거래량 (<0.85 = dry-up, 낮을수록 마름)
    liquidity_pass = Column(Boolean, default=True)  # 최소 주가·거래량 충족

    # 최종 결과
    final_pass = Column(Boolean, default=False)

    # 매수/매도 의견 (Minervini 매매 규칙 기반)
    signal = Column(String)         # STRONG_BUY / BUY / WATCH / SELL / AVOID
    signal_reason = Column(String)  # 신호 근거 (한국어 설명)
    pivot_price = Column(Float)     # VCP 피벗 = 매수 트리거 가격 (돌파 기준선)
    stop_loss = Column(Float)       # 권장 손절가 (진입가 -8%, Minervini 기준)
    price_date = Column(Date)       # 이 결과를 계산한 마지막 종가 날짜 (스크리닝일과 다를 수 있음)

    stock = relationship("Stock", back_populates="results")


class VCPEvent(Base):
    """VCP(변동성 축소 패턴) 발생 종목의 이력·돌파 추적 레지스트리.

    ScreeningResult는 90일 후 정리되고 전체에 섞여 있어, VCP만 따로 누적 관리하기
    위해 별도 테이블로 둔다. 한 베이스를 '최초발생일~최근일'로 한 행에 누적하고,
    돌파/실패 시 상태를 확정한다(중복 행 방지)."""
    __tablename__ = "vcp_events"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    first_detected = Column(Date, nullable=False)   # 이 VCP 베이스 최초 탐지일 (= '첫 형성', 불변)
    last_detected = Column(Date, nullable=False)     # 마지막으로 VCP가 유지된 날
    status = Column(String, default="forming", index=True)  # forming / breakout_watch / broke_out / failed / reset

    # 최신(last_detected 시점) 스냅샷
    contractions = Column(Integer)
    pivot_price = Column(Float)      # 돌파 트리거 가격
    stop_loss = Column(Float)
    base_low = Column(Float)         # 베이스 저점(실제 조정 저점) — 리셋 판정 기준
    base_high = Column(Float)        # 베이스 고점 — 정체성 앵커
    base_seq = Column(Integer, default=1)    # 종목별 베이스 순번(리셋마다 +1) — 후행베이스 감점용
    quality = Column(Integer)        # 0~100 품질점수 — 정렬·알림 임계
    peak_quality = Column(Integer)   # 형성 중 최고 품질(랭킹 보조)
    trend_grace = Column(Integer, default=0)  # 추세게이트 이탈 유예 카운터
    rs_rank = Column(Float)
    close = Column(Float)
    volume_dryup = Column(Boolean, default=False)
    # v2 품질점수 표본외 검증용 스냅샷 — ScreeningResult는 90일 후 정리되므로 여기 보존.
    dryup_ratio = Column(Float)       # 최근10/50일 거래량 (dry-up 등급)
    ud_volume_ratio = Column(Float)   # 50일 상승/하락 거래량 비율
    last_contraction = Column(Float)  # 마지막 조정폭(%)
    # 미너비니식 dry-up 3종 — ScreeningResult 값의 마지막 스냅샷(돌파·실패 직전). 점수엔 미반영.
    vdu_lowest_in_last = Column(Boolean)
    vdu_last_vs_prior = Column(Float)
    vdu_last_vs_base = Column(Float)

    # 결과 추적
    breakout_date = Column(Date)     # 피벗 돌파 확정일
    breakout_price = Column(Float)   # 돌파 시 종가
    resolved_date = Column(Date)     # 실패/만료/리셋 확정일
    alert_formed_at = Column(DateTime)    # '첫 형성' 알림 발송 워터마크(exactly-once)
    alert_breakout_at = Column(DateTime)  # '돌파' 알림 발송 워터마크(exactly-once)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 표본외 검증용 — 돌파 시점 국면과 실제 체결 결과. 진입은 돌파일 종가, 손절은 그 아래
    # STOP_LOSS_PCT, 목표는 2.5R(제품 안내 규칙 그대로).
    # 나중에 다시 계산 불가(screening_results는 90일 후 삭제)하므로 돌파 때 바로 찍어야 한다.
    regime_at_breakout = Column(String)   # BULL/NEUTRAL/BEAR — 돌파일 그 시장의 국면
    pct_above_200_at_breakout = Column(Float)  # 돌파일 200일선 위 종목 비율(%) — 국면 세부치
    outcome = Column(String)        # None(진행 중) / stop / target / timeout
    outcome_date = Column(Date)     # 결과 확정일
    outcome_pct = Column(Float)     # 돌파가 대비 실현 수익률(%)

    stock = relationship("Stock")


class VCPFormation(Base):
    """VCP 모양 점수 모델(app/vcp.py)의 형성 추적 레지스트리. 한 베이스를 한 행으로 누적한다.

    vcp_events(구 판정)와 달리 신원이 베이스 고점 날짜(key_date)로 고정이고 (stock_id, key_date)가 유니크라
    같은 베이스가 중복 기록되거나 first_detected가 바뀌지 않는다. 돌파는 가격(종가 > 피벗)만으로 잡고
    거래량은 등급으로만 기록한다. 규칙은 app/vcp_tracker.py 참고. 구 vcp_events는 건드리지 않는다."""
    __tablename__ = "vcp_formations"
    __table_args__ = (UniqueConstraint("stock_id", "key_date"),)

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    key_date = Column(Date, nullable=False)          # 베이스 고점 날짜 = 형성의 신원(불변)
    first_detected = Column(Date, nullable=False)    # 처음 포착한 날(불변)
    last_seen = Column(Date, nullable=False)         # 같은 형성을 마지막으로 확인한 날
    status = Column(String, default="WATCH", index=True)   # WATCH / NEAR_PIVOT / BREAKOUT / FAILED_BREAKOUT / INVALIDATED
    resolved_date = Column(Date)                     # 추적 종료일(비어 있으면 아직 추적 중)
    end_reason = Column(String)                      # follow_done / base_broken / trend_lost / score_low / stale / superseded
    base_seq = Column(Integer, default=1)            # 종목별 형성 순번

    # 최신 스냅샷
    base_high = Column(Float)
    base_low = Column(Float)         # 베이스 저점(실제 조정 저점) — 종가가 이 아래로 마감하면 무효
    pivot = Column(Float)            # 마지막 수축의 상단
    contractions = Column(Integer)
    depths = Column(String)          # 수축 낙폭(%) "22/11/5"
    score_first = Column(Float)
    score_peak = Column(Float)
    score_last = Column(Float)
    grade_last = Column(String)
    parts_last = Column(String)      # 항목별 점수 JSON
    trend_grace = Column(Integer, default=0)   # 추세게이트 이탈 유예 카운터
    rs_rank = Column(Float)
    close = Column(Float)

    # 돌파 기록(첫 돌파만 기록하고 이후 바꾸지 않는다)
    breakout_date = Column(Date)
    breakout_price = Column(Float)           # 돌파일 종가
    breakout_volume = Column(Float)
    breakout_avg_volume = Column(Float)      # 돌파 전 50일 평균 거래량
    volume_ratio = Column(Float)
    volume_quality = Column(String)          # 약함 / 보통 / 좋음 / 매우 좋음 / 매우 강함
    breakout_score = Column(Float)           # 돌파 직전 모습의 VCP 점수

    # 표본외 검증용 — 돌파일 국면과 실제 체결 결과(진입은 돌파일 종가, 규칙은 vcp_events와 같음)
    regime_at_breakout = Column(String)
    pct_above_200_at_breakout = Column(Float)
    outcome = Column(String)         # None(진행 중) / stop / target / timeout
    outcome_date = Column(Date)
    outcome_pct = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock")
