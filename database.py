from sqlalchemy import create_engine, Column, String, Float, DateTime, Integer, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()

class Token(Base):
    __tablename__ = "tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ca = Column(String, unique=True, nullable=False)           # Contract address
    ticker = Column(String)                                     # Token symbol
    name = Column(String)                                       # Token name
    migration_time = Column(DateTime)                           # When it graduated from pump.fun
    liquidity_at_10k = Column(Float)                           # Liquidity when mcap hit 10k
    liquidity_at_100k = Column(Float)                          # Liquidity when mcap hit 100k
    ath = Column(Float)                                         # All time high mcap
    ath_timestamp = Column(DateTime)                            # When ATH was reached
    bundler_pct = Column(Float)                                 # % of supply bundled at launch
    top10_holder_pct = Column(Float)                           # % held by top 10 wallets
    time_before_dump = Column(Float)                           # Minutes from ATH to 80% drawdown
    dumped = Column(Boolean, default=False)                    # Whether it dumped 80%+ from ATH
    outcome = Column(String)                                    # Runner / Slow bleed / Instant dump
    created_at = Column(DateTime, default=datetime.utcnow)

_engine = None
_Session = None

def get_engine():
    global _engine
    if _engine is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_db = f"sqlite:///{os.path.join(base_dir, 'data', 'trenches.db')}"
        db_url = os.getenv("DATABASE_URL", default_db)
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
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