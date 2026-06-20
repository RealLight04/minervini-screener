from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./screener.db"
    ENABLE_SCHEDULER: bool = False  # 무료 플랜은 디스크가 임시라 기본 off (TFT와 동일 패턴)
    SCHEDULE_HOUR: int = 17      # 장 마감 후 오후 5시 (ET 기준)
    SCHEDULE_MINUTE: int = 0
    RS_TOP_PERCENTILE: float = 30.0   # RS 상위 30% 이내
    MIN_PRICE: float = 10.0           # 최소 주가 필터 (페니 스톡 제외)
    MIN_VOLUME: int = 100_000         # 최소 평균 거래량

    class Config:
        env_file = ".env"


settings = Settings()
