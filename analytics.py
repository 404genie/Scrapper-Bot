from sqlalchemy import func
from database import get_session, Token
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def save_tokens(tokens: list[dict]):
    """Save collected token data to database, skip duplicates."""
    session = get_session()
    saved = 0
    skipped = 0

    for t in tokens:
        existing = session.query(Token).filter_by(ca=t["ca"]).first()
        if existing:
            skipped += 1
            continue

        token = Token(
            ca=t["ca"],
            ticker=t.get("ticker"),
            name=t.get("name"),
            migration_time=t.get("migration_time"),
            liquidity_at_10k=t.get("liquidity_at_10k"),
            liquidity_at_100k=t.get("liquidity_at_100k"),
            ath=t.get("ath"),
            ath_timestamp=t.get("ath_timestamp"),
            bundler_pct=t.get("bundler_pct"),
            top10_holder_pct=t.get("top10_holder_pct"),
            time_before_dump=t.get("time_before_dump"),
            dumped=t.get("dumped", False),
            outcome=t.get("outcome", "Unknown"),
        )
        session.add(token)
        saved += 1

    session.commit()
    session.close()
    logger.info(f"Saved {saved} tokens, skipped {skipped} duplicates.")
    return saved, skipped


def get_summary_stats() -> dict:
    """Overall database summary."""
    session = get_session()
    total = session.query(Token).count()
    runners = session.query(Token).filter_by(outcome="Runner").count()
    instant_dump = session.query(Token).filter_by(outcome="Instant dump").count()
    slow_bleed = session.query(Token).filter_by(outcome="Slow bleed").count()

    avg_ath = session.query(func.avg(Token.ath)).scalar()
    avg_dump_time = session.query(func.avg(Token.time_before_dump)).filter(Token.dumped == True).scalar()

    session.close()
    return {
        "total": total,
        "runners": runners,
        "instant_dump": instant_dump,
        "slow_bleed": slow_bleed,
        "runner_rate": round((runners / total * 100), 1) if total else 0,
        "avg_ath_mcap": round(avg_ath, 0) if avg_ath else 0,
        "avg_dump_time_mins": round(avg_dump_time, 0) if avg_dump_time else 0,
    }


def get_pattern_analysis() -> dict:
    """Analyze what separates runners from dumps."""
    session = get_session()

    def avg_metric(outcome: str, field):
        result = session.query(func.avg(field)).filter(Token.outcome == outcome).scalar()
        return round(result, 2) if result else None

    runners_liq10k = avg_metric("Runner", Token.liquidity_at_10k)
    dumps_liq10k = avg_metric("Instant dump", Token.liquidity_at_10k)

    runners_bundler = avg_metric("Runner", Token.bundler_pct)
    dumps_bundler = avg_metric("Instant dump", Token.bundler_pct)

    runners_top10 = avg_metric("Runner", Token.top10_holder_pct)
    dumps_top10 = avg_metric("Instant dump", Token.top10_holder_pct)

    runners_liq100k = avg_metric("Runner", Token.liquidity_at_100k)
    dumps_liq100k = avg_metric("Instant dump", Token.liquidity_at_100k)

    session.close()

    return {
        "runners": {
            "avg_liq_at_10k": runners_liq10k,
            "avg_liq_at_100k": runners_liq100k,
            "avg_bundler_pct": runners_bundler,
            "avg_top10_pct": runners_top10,
        },
        "instant_dumps": {
            "avg_liq_at_10k": dumps_liq10k,
            "avg_liq_at_100k": dumps_liq100k,
            "avg_bundler_pct": dumps_bundler,
            "avg_top10_pct": dumps_top10,
        }
    }


def get_safe_filter_suggestions() -> dict:
    """
    Based on data, suggest filter thresholds that historically
    separate runners from instant dumps.
    """
    session = get_session()
    total = session.query(Token).count()
    session.close()

    if total < 20:
        return {"message": "Not enough data yet. Need at least 20 tokens for reliable patterns."}

    patterns = get_pattern_analysis()
    r = patterns["runners"]
    d = patterns["instant_dumps"]

    suggestions = []

    # Liquidity filter
    if r["avg_liq_at_10k"] and d["avg_liq_at_10k"]:
        threshold = (r["avg_liq_at_10k"] + d["avg_liq_at_10k"]) / 2
        suggestions.append(f"Liquidity at 10k mcap > ${threshold:,.0f}")

    # Bundler filter
    if r["avg_bundler_pct"] and d["avg_bundler_pct"]:
        threshold = (r["avg_bundler_pct"] + d["avg_bundler_pct"]) / 2
        suggestions.append(f"Bundler % < {threshold:.1f}%")

    # Top 10 holder filter
    if r["avg_top10_pct"] and d["avg_top10_pct"]:
        threshold = (r["avg_top10_pct"] + d["avg_top10_pct"]) / 2
        suggestions.append(f"Top 10 holder % < {threshold:.1f}%")

    return {
        "suggestions": suggestions,
        "based_on": total,
        "runner_rate": get_summary_stats()["runner_rate"]
    }


def get_recent_tokens(limit: int = 10) -> list[Token]:
    """Get most recently added tokens."""
    session = get_session()
    tokens = session.query(Token).order_by(Token.migration_time.desc()).limit(limit).all()
    session.close()
    return tokens


def search_token(ca: str) -> Optional[Token]:
    """Look up a specific token by contract address."""
    session = get_session()
    token = session.query(Token).filter_by(ca=ca).first()
    session.close()
    return token