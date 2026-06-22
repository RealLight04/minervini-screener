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
    eps_growth_yoy = Column(Float)      # 전년 동기 대비 EPS 증가율 (%)
    revenue_growth_yoy = Column(Float)  # 전년 동기 대비 매출 증가율 (%)

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

    # 거래량 / 유동성
    avg_volume = Column(Float)        # 50일 평균 거래량
    vol_vs_avg = Column(Float)        # 최근 거래량 / 50일 평균 (1.0 = 평균)
    liquidity_pass = Column(Boolean, default=True)  # 최소 주가·거래량 충족

    # 최종 결과
    final_pass = Column(Boolean, default=False)

    # 매수/매도 의견 (Minervini 매매 규칙 기반)
    signal = Column(String)         # STRONG_BUY / BUY / WATCH / SELL / AVOID
    signal_reason = Column(String)  # 신호 근거 (한국어 설명)
    pivot_price = Column(Float)     # VCP 피벗 = 매수 트리거 가격 (돌파 기준선)
    stop_loss = Column(Float)       # 권장 손절가 (피벗 -8%, Minervini 기준)

    stock = relationship("Stock", back_populates="results")
