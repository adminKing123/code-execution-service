from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import config
from models import Base

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    from settings_store import seed_settings

    seed_settings()


def get_session() -> Session:
    return SessionLocal()
