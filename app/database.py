from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import settings

# Render 등 PostgreSQL은 'postgres://' → 'postgresql://' 로 정규화 필요
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# check_same_thread 옵션은 SQLite 전용
connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
engine = create_engine(db_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)   # 없는 테이블 생성(vcp_events 등)
    # 기존 SQLite DB에 신규 컬럼 보강(create_all은 ALTER를 못 함) — 앱 시작 시 자가치유.
    # 이게 없으면 vcp_pivot 없는 DB에서 ScreeningResult 쿼리가 'no such column'으로 깨진다.
    if db_url.startswith("sqlite"):
        migrations = {
            "screening_results": {"vcp_pivot": "FLOAT", "accum_days": "INTEGER", "distrib_days": "INTEGER",
                                   "ud_volume_ratio": "FLOAT", "dryup_ratio": "FLOAT",
                                   "vcp_base_low": "FLOAT", "vcp_base_high": "FLOAT",
                                   "vcp_last_contraction": "FLOAT"},
            "vcp_events": {"base_low": "FLOAT", "base_high": "FLOAT", "base_seq": "INTEGER DEFAULT 1",
                           "quality": "INTEGER", "peak_quality": "INTEGER", "trend_grace": "INTEGER DEFAULT 0",
                           "alert_formed_at": "DATETIME", "alert_breakout_at": "DATETIME",
                           "dryup_ratio": "FLOAT", "ud_volume_ratio": "FLOAT", "last_contraction": "FLOAT"},
            "stocks": {"eps_rev_up": "INTEGER", "eps_rev_down": "INTEGER", "eps_est_chg": "FLOAT"},
        }
        with engine.connect() as conn:
            for table, ncols in migrations.items():
                cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
                if cols:  # 테이블이 이미 있으면 없는 컬럼만 ALTER
                    for name, typ in ncols.items():
                        if name not in cols:
                            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
            conn.commit()
