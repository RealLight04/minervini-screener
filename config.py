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

    # VCP 레지스트리 튜닝 (초기 휴리스틱 — 보존된 결과로 캘리브레이션 예정)
    VCP_BREAKOUT_VOL: float = 1.4      # 돌파 확정에 필요한 거래량 배수(50일평균 대비)
    VCP_BREAKOUT_WATCH_DAYS: int = 3   # 거래량 미달이라도 피벗 위에서 이만큼 버티면 돌파로 확정
    VCP_RESET_UNDERCUT: float = 0.04   # 베이스 저점이 이만큼 더 깨지면 리셋(새 베이스)
    VCP_TREND_GRACE_DAYS: int = 2      # 추세게이트 이탈 유예(일) — 하루 깜빡임 흡수
    VCP_QUALITY_FORM: float = 60.0     # 품질밴드 진입(형성 노출)
    VCP_QUALITY_HOLD: float = 45.0     # 품질밴드 유지(churn 흡수)
    VCP_QUALITY_ALERT: float = 70.0    # '첫 형성' 알림 임계
    VCP_ALERT_MIN_BASE_DAYS: int = 15  # 알림 전 최소 형성 기간(거래일)
    DART_API_KEY: str = ""   # OpenDART 인증키 (한국 종목 재무 수집용, .env에 보관)
    ALPHAVANTAGE_API_KEY: str = ""   # Alpha Vantage 인증키 (미국 분기 EPS 이력 백필용, .env에 보관)
    # 이메일 알림(Gmail SMTP). 앱 비밀번호는 .env에만 보관.
    GMAIL_USER: str = ""             # 발송 Gmail 주소
    GMAIL_APP_PASSWORD: str = ""     # Gmail 앱 비밀번호(16자리, 공백 제거)
    ALERT_BASE_URL: str = "https://liam.tail6fe9f6.ts.net"  # 확인/구독취소 링크용 사이트 주소
    # /api/screen-now(수동 재스크리닝) 인증 토큰. 미설정 시 해당 엔드포인트 완전 차단(404) — Funnel로
    # 공개된 앱에서 인증 없는 GET이 전체 재계산을 트리거하지 않도록 방어.
    ADMIN_TRIGGER_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
