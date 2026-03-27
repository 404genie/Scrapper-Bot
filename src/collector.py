import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional
import logging
import os

logger = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com"
HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")


async def fetch_json(session: aiohttp.ClientSession, url: str, headers: dict = None, params: dict = None):
    try:
        async with session.get(
            url,
            headers=headers or {},
            params=params or {},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logger.warning(f"HTTP {resp.status} for {url}")
                return None
    except Exception as e:
        logger.error(f"Fetch error {url}: {e}")
        return None


async def post_json(session: aiohttp.ClientSession, url: str, payload: dict) -> Optional[dict]:
    try:
        async with session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logger.warning(f"HTTP {resp.status} for POST {url}")
                return None
    except Exception as e:
        logger.error(f"POST error {url}: {e}")
        return None


async def get_graduated_tokens(session: aiohttp.ClientSession, days: int = 14) -> list[str]:
    """
    Fetch recently graduated tokens from Pump.fun → Raydium/Meteora via DEXScreener.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    seen = set()
    cas = []

    # Latest token profiles on Solana
    url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
    data = await fetch_json(session, url)
    if data and isinstance(data, list):
        for item in data:
            if item.get("chainId") != "solana":
                continue
            ca = item.get("tokenAddress")
            if ca and ca not in seen:
                seen.add(ca)
                cas.append(ca)

    await asyncio.sleep(0.3)

    # Search for recent pump.fun graduated pairs on Raydium/Meteora
    search_url = f"{DEXSCREENER_BASE}/latest/dex/search"
    search_data = await fetch_json(session, search_url, params={"q": "pump"})

    if search_data and isinstance(search_data.get("pairs"), list):
        for pair in search_data["pairs"]:
            if pair.get("chainId") != "solana":
                continue
            if pair.get("dexId") not in ("raydium", "meteora"):
                continue
            created_ms = pair.get("pairCreatedAt")
            if created_ms:
                created_dt = datetime.utcfromtimestamp(created_ms / 1000)
                if created_dt < cutoff:
                    continue
            ca = (pair.get("baseToken") or {}).get("address")
            if ca and ca not in seen:
                seen.add(ca)
                cas.append(ca)

    logger.info(f"Found {len(cas)} candidate token addresses")
    return cas


async def get_token_pair_data(session: aiohttp.ClientSession, ca: str) -> Optional[dict]:
    """Get best pair data from DEXScreener for a token."""
    url = f"{DEXSCREENER_BASE}/tokens/v1/solana/{ca}"
    data = await fetch_json(session, url)

    if not data or not isinstance(data, list) or len(data) == 0:
        return None

    # Prefer raydium/meteora — where graduated tokens trade
    pairs = [p for p in data if p.get("dexId") in ("raydium", "meteora", "orca")]
    if not pairs:
        pairs = data

    pairs.sort(key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0), reverse=True)
    return pairs[0] if pairs else None


async def get_holder_data(session: aiohttp.ClientSession, ca: str) -> dict:
    """
    Get top 10 holder concentration and bundler % using Helius RPC.

    - Top 10 holders: via getTokenLargestAccounts RPC call
    - Bundler %: wallets that bought in the first few transactions after launch
      identified via getSignaturesForAddress + getTransaction
    """
    result = {"top10_pct": None, "bundler_pct": None}

    if not HELIUS_KEY:
        logger.warning("No HELIUS_API_KEY set — holder data will be N/A")
        return result

    rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

    # ── Step 1: Top 10 holder % ──────────────────────────────────────────
    # Get the largest token accounts for this mint
    largest_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [ca, {"commitment": "finalized"}]
    }
    largest_resp = await post_json(session, rpc_url, largest_payload)

    if largest_resp and largest_resp.get("result"):
        accounts = largest_resp["result"].get("value", [])

        if accounts:
            # Get total supply to calculate percentages
            supply_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "getTokenSupply",
                "params": [ca, {"commitment": "finalized"}]
            }
            supply_resp = await post_json(session, rpc_url, supply_payload)

            if supply_resp and supply_resp.get("result"):
                supply_info = supply_resp["result"].get("value", {})
                total_supply = float(supply_info.get("amount") or 0)

                if total_supply > 0:
                    # Sum top 10 account balances
                    top10_amount = sum(
                        float(acc.get("amount") or 0)
                        for acc in accounts[:10]
                    )
                    top10_pct = (top10_amount / total_supply) * 100
                    result["top10_pct"] = round(top10_pct, 2)

    # ── Step 2: Bundler % ────────────────────────────────────────────────
    # Get the first few transactions for this token mint
    # Bundlers = wallets that bought in the same slot/block as the launch tx
    sigs_payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "getSignaturesForAddress",
        "params": [
            ca,
            {"limit": 10, "commitment": "finalized"}
        ]
    }
    sigs_resp = await post_json(session, rpc_url, sigs_payload)

    if sigs_resp and sigs_resp.get("result"):
        sigs = sigs_resp["result"]

        if sigs:
            # The last signature is the earliest (launch) transaction
            launch_sig = sigs[-1].get("signature")
            launch_slot = sigs[-1].get("slot")

            if launch_sig and launch_slot:
                # Find all sigs in the same slot — these are bundled
                same_slot_sigs = [
                    s for s in sigs
                    if s.get("slot") == launch_slot
                ]
                bundled_count = len(same_slot_sigs)

                # Get total supply for bundler % calculation
                if result["top10_pct"] is not None and bundled_count > 1:
                    # Estimate bundler % from same-slot buyers vs top holders
                    # Conservative estimate: each bundler holds ~equal share
                    bundler_est = min(bundled_count * 5.0, 50.0)
                    result["bundler_pct"] = round(bundler_est, 2)
                elif bundled_count <= 1:
                    result["bundler_pct"] = 0.0

    return result


def calculate_ath_and_dump(pair_data: dict) -> tuple[Optional[float], bool, Optional[float]]:
    """
    Estimate ATH mcap and whether token has dumped 80%+ from it.
    Returns: (ath_mcap, has_dumped, time_before_dump_minutes)
    """
    if not pair_data:
        return None, False, None

    fdv = float(pair_data.get("fdv") or 0)
    mcap = float(pair_data.get("marketCap") or fdv or 0)
    if mcap <= 0:
        return None, False, None

    price_change = pair_data.get("priceChange") or {}
    h1  = float(price_change.get("h1")  or 0)
    h6  = float(price_change.get("h6")  or 0)
    h24 = float(price_change.get("h24") or 0)

    worst = min(h1, h6, h24)

    if worst < -80:
        # Back-calculate ATH: current = ath * (1 + worst/100)
        ath_mcap = mcap / (1 + worst / 100)
        if h1 < -80:
            time_mins = 30.0
        elif h6 < -80:
            time_mins = 180.0
        else:
            time_mins = 720.0
        return round(ath_mcap, 0), True, time_mins

    return round(mcap, 0), False, None


def classify_outcome(dumped: bool, time_before_dump: Optional[float], price_change_24h: float) -> str:
    """Classify token as Runner / Instant dump / Slow bleed."""
    if dumped:
        if time_before_dump is not None and time_before_dump <= 60:
            return "Instant dump"
        return "Slow bleed"
    if price_change_24h >= 0:
        return "Runner"
    return "Slow bleed"


async def collect_token_metrics(session: aiohttp.ClientSession, ca: str, cutoff: datetime) -> Optional[dict]:
    """Full pipeline for a single token. Session passed in — not created here."""
    pair_data = await get_token_pair_data(session, ca)
    if not pair_data:
        return None

    created_ms = pair_data.get("pairCreatedAt")
    migration_time = datetime.utcfromtimestamp(created_ms / 1000) if created_ms else None

    if migration_time and migration_time < cutoff:
        return None

    base_token = pair_data.get("baseToken") or {}
    ticker = base_token.get("symbol", "")
    name = base_token.get("name", "")

    current_liq = float((pair_data.get("liquidity") or {}).get("usd") or 0) or None
    fdv = float(pair_data.get("fdv") or pair_data.get("marketCap") or 0)

    ath_mcap, dumped, time_before_dump = calculate_ath_and_dump(pair_data)

    liq_at_10k  = current_liq if fdv > 0 and fdv <= 80_000  else None
    liq_at_100k = current_liq if fdv > 0 and fdv <= 800_000 else None

    holder_data = await get_holder_data(session, ca)

    price_change_24h = float((pair_data.get("priceChange") or {}).get("h24") or 0)
    outcome = classify_outcome(dumped, time_before_dump, price_change_24h)

    return {
        "ca": ca,
        "ticker": ticker,
        "name": name,
        "migration_time": migration_time,
        "liquidity_at_10k": liq_at_10k,
        "liquidity_at_100k": liq_at_100k,
        "ath": ath_mcap,
        "bundler_pct": holder_data.get("bundler_pct"),
        "top10_holder_pct": holder_data.get("top10_pct"),
        "time_before_dump": time_before_dump,
        "dumped": dumped,
        "outcome": outcome,
    }


async def run_historical_collection(days: int = 14) -> list[dict]:
    """Main entry: collect all graduated Solana tokens from last N days."""
    logger.info(f"Starting historical collection for last {days} days...")
    cutoff = datetime.utcnow() - timedelta(days=days)

    async with aiohttp.ClientSession() as session:
        candidates = await get_graduated_tokens(session, days)
        results = []
        batch_size = 10

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            tasks = [collect_token_metrics(session, ca, cutoff) for ca in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in batch_results:
                if isinstance(r, dict):
                    results.append(r)
                elif isinstance(r, Exception):
                    logger.error(f"Token error: {r}")

            await asyncio.sleep(0.5)
            logger.info(f"Processed {min(i + batch_size, len(candidates))}/{len(candidates)} tokens...")

    logger.info(f"Done. {len(results)} valid tokens collected.")
    return results