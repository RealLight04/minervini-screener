from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./screener.db"
    ENABLE_SCHEDULER: bool = False  # 무료 플랜은 디스크가 임시라 기본 off (TFT와 동일 패턴)
    SCHEDULE_HOUR: int = 17      # 장 마감 후 오후 5시 (ET 기준)
    SCHEDULE_MINUTE: int = 0
    RS_TOP_PERCENTILE: float = 30.0   # RS 상위 30% 이내
    MIN_PRICE: float = 10.0           # 최소 주가 필터 (미국, USD)
    MIN_PRICE_KR: float = 1_000.0     # 최소 주가 필터 (한국, KRW — 동전주 제외)
    MIN_VOLUME: int = 100_000         # 최소 평균 거래량
    # 한국 종목 유니버스 크기 (시가총액 상위 N)
    KOSPI_TOP_N: int = 200
    KOSDAQ_TOP_N: int = 100
    # 시장 국면 게이트: 약세장(BEAR)인 시장의 BUY/STRONG_BUY를 보류(WATCH)로 강등.
    # 백테스트 근거: 지수<200MA(≈BEAR)에서 트렌드 신호는 시장평균 대비 -2.5~3.5%p 열위.
    REGIME_GATE: bool = True
    DART_API_KEY: str = ""   # OpenDART 인증키 (한국 종목 재무 수집용, .env에 보관)
    ALPHAVANTAGE_API_KEY: str = ""   # Alpha Vantage 인증키 (미국 분기 EPS 이력 백필용, .env에 보관)

    class Config:
        env_file = ".env"


settings = Settings()
