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
    # 유니버스 목록(시총 상위 N·S&P500)에서 빠진 기존 종목은 is_active=False로 끈다.
    # 단, 목록 자체가 스크래핑 실패로 텅 비거나 급감했을 때 시장 전체를 잘못 끄지 않도록,
    # 새 목록이 그 시장의 기존 활성 종목 수 대비 이 비율 미만이면 탈락 처리를 건너뛴다.
    UNIVERSE_SHRINK_GUARD: float = 0.5
    # 시장 국면 게이트: 약세장(BEAR)인 시장의 BUY/STRONG_BUY를 보류(WATCH)로 강등.
    # 판정은 '200일선 위 종목 비율 < 40%'(breadth). 근거 백테스트는 '지수 < 200MA' 기준이라
    # 이 breadth 정의 자체는 아직 검증되지 않았다 — 조정은 표본외 결과를 보고 한다.
    REGIME_GATE: bool = True

    STOP_LOSS_PCT: float = 8.0         # 진입가 대비 최대 손절폭(%) — 미너비니 7~8% 원칙
    EXTENDED_NEAR_HIGH: float = 0.90   # 피벗 없는 통과 종목: 52주 고점의 이 비율 이상=연장, 미만=베이스 미형성 (초기 휴리스틱)

    # 가격 데이터 이상 격리 (초기 휴리스틱)
    PRICE_JUMP_LIMIT: float = 0.40     # 하루 종가 변동이 이보다 크면 분할 미반영·출처 혼합 의심
    PRICE_JUMP_LOOKBACK: int = 60      # 최근 N거래일 안에 이상 변동이 있으면 신호 보류
    STALE_WARN_SESSIONS: int = 3       # 가격 기준일이 예상 최신 거래일보다 이만큼 뒤처지면 화면 경고
    PRICE_STALE_DAYS: int = 7          # 종목 마지막 종가가 자기 시장 최신일보다 이 달력일수 넘게 늦으면 신호 보류

    # VCP 레지스트리 튜닝 (초기 휴리스틱 — 보존된 결과로 캘리브레이션 예정)
    VCP_BREAKOUT_VOL: float = 1.4      # 돌파 확정에 필요한 거래량 배수(50일평균 대비)
    VCP_BREAKOUT_WATCH_DAYS: int = 3   # 거래량 미달이라도 피벗 위에서 이만큼 버티면 돌파로 확정
    VCP_RESET_UNDERCUT: float = 0.04   # 베이스 저점이 이만큼 더 깨지면 리셋(새 베이스)
    VCP_TREND_GRACE_DAYS: int = 2      # 추세게이트 이탈 유예(일) — 하루 깜빡임 흡수
    VCP_QUALITY_FORM: float = 60.0     # 품질밴드 진입(형성 노출)
    VCP_QUALITY_HOLD: float = 45.0     # 품질밴드 유지(churn 흡수)
    VCP_QUALITY_ALERT: float = 70.0    # '첫 형성' 알림 임계
    VCP_ALERT_MIN_BASE_DAYS: int = 15  # 알림 전 최소 형성 기간(거래일)
    VCP_OUTCOME_TIMEOUT_DAYS: int = 120  # 돌파 후 이 달력일 넘도록 손절·목표 둘 다 미도달이면 timeout 확정 (초기 휴리스틱)
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
        extra = "ignore"  # .env에 research 스크립트용 키(TYPESAFE_API_KEY 등)가 더 있어도 무시


settings = Settings()
