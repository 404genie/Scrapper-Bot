from sqlalchemy import create_engine, Column, String, Float, DateTime, Integer, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()

class Token(Base):
    __tablename__ = "tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ca = Column(String, unique=True, nullable=False)
    ticker = Column(String)
    name = Column(String)
    migration_time = Column(DateTime)
    liquidity_at_10k = Column(Float)
    liquidity_at_100k = Column(Float)
    ath = Column(Float)
    ath_timestamp = Column(DateTime)
    bundler_pct = Column(Float)
    top10_holder_pct = Column(Float)
    time_before_dump = Column(Float)
    dumped = Column(Boolean, default=False)
    outcome = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

_engine = None
_Session = None

def get_engine():
    global _engine
    if _engine is None:
        db_url = os.getenv("DATABASE_URL", "")

        # Railway PostgreSQL URL fix
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        # No DATABASE_URL — use local SQLite
        if not db_url:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            db_url = f"sqlite:///{os.path.join(data_dir, 'trenches.db')}"

        _engine = create_engine(db_url)
    return _engine

def init_db():
    global _Session
    engine = get_engine()
    Base.metadata.create_all(engine)
    _Session = sessionmaker(bind=engine)
    return engine

def get_session():
    global _Session
    if _Session is None:
        engine = get_engine()
        _Session = sessionmaker(bind=engine)
    return _Session()