import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from database import init_db
from collector import run_historical_collection
from analytics import (
    save_tokens,
    get_summary_stats,
    get_pattern_analysis,
    get_safe_filter_suggestions,
    get_recent_tokens,
    search_token,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def fmt_usd(val) -> str:
    if val is None:
        return "N/A"
    if val >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.2f}"

def fmt_pct(val) -> str:
    return f"{val:.1f}%" if val is not None else "N/A"

def fmt_time(mins) -> str:
    if mins is None:
        return "N/A"
    if mins < 60:
        return f"{int(mins)}m"
    return f"{mins/60:.1f}h"

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Summary", callback_data="summary"),
            InlineKeyboardButton("🔍 Patterns", callback_data="patterns"),
        ],
        [
            InlineKeyboardButton("🎯 Filter Tips", callback_data="filters"),
            InlineKeyboardButton("🕐 Recent Tokens", callback_data="recent"),
        ],
        [
            InlineKeyboardButton("🔄 Collect Data", callback_data="collect"),
        ]
    ])


# ─────────────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Trenches Research Bot*\n\n"
        "I track graduated Solana tokens and help you find patterns "
        "that separate runners from dumps.\n\n"
        "What do you want to do?"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*Commands:*\n\n"
        "/start — Main menu\n"
        "/collect — Fetch last 14 days of graduated tokens\n"
        "/summary — Overview of all data collected\n"
        "/patterns — What runners vs dumps look like\n"
        "/filters — Suggested entry filters based on data\n"
        "/recent — Last 10 tokens added\n"
        "/token `<CA>` — Look up a specific token\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def collect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔄 Starting collection of last 14 days of graduated tokens...\n"
        "This will take a few minutes. I'll update you when done."
    )
    try:
        tokens = await run_historical_collection(days=14)
        saved, skipped = save_tokens(tokens)
        await msg.edit_text(
            f"✅ *Collection complete*\n\n"
            f"• Tokens found: `{len(tokens)}`\n"
            f"• Saved to DB: `{saved}`\n"
            f"• Duplicates skipped: `{skipped}`\n\n"
            f"Use /summary to see your data.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Collection error: {e}")
        await msg.edit_text(f"❌ Collection failed: {str(e)}")


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_summary_stats()

    if stats["total"] == 0:
        await update.message.reply_text(
            "No data yet. Run /collect first.",
            reply_markup=main_keyboard()
        )
        return

    text = (
        "📊 *Database Summary*\n\n"
        f"Total tokens tracked: `{stats['total']}`\n\n"
        f"🟢 Runners: `{stats['runners']}` ({stats['runner_rate']}%)\n"
        f"🔴 Instant dumps: `{stats['instant_dump']}`\n"
        f"🟡 Slow bleeds: `{stats['slow_bleed']}`\n\n"
        f"Avg ATH mcap: `{fmt_usd(stats['avg_ath_mcap'])}`\n"
        f"Avg time before dump: `{fmt_time(stats['avg_dump_time_mins'])}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def patterns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_summary_stats()
    if stats["total"] < 5:
        await update.message.reply_text("Need more data. Run /collect first.")
        return

    p = get_pattern_analysis()
    r = p["runners"]
    d = p["instant_dumps"]

    text = (
        "🔍 *Pattern Analysis*\n\n"
        "*Runners avg metrics:*\n"
        f"  Liquidity at 10k: `{fmt_usd(r['avg_liq_at_10k'])}`\n"
        f"  Liquidity at 100k: `{fmt_usd(r['avg_liq_at_100k'])}`\n"
        f"  Bundler %: `{fmt_pct(r['avg_bundler_pct'])}`\n"
        f"  Top 10 holders: `{fmt_pct(r['avg_top10_pct'])}`\n\n"
        "*Instant dump avg metrics:*\n"
        f"  Liquidity at 10k: `{fmt_usd(d['avg_liq_at_10k'])}`\n"
        f"  Liquidity at 100k: `{fmt_usd(d['avg_liq_at_100k'])}`\n"
        f"  Bundler %: `{fmt_pct(d['avg_bundler_pct'])}`\n"
        f"  Top 10 holders: `{fmt_pct(d['avg_top10_pct'])}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = get_safe_filter_suggestions()

    if "message" in result:
        await update.message.reply_text(result["message"])
        return

    suggestions = result["suggestions"]
    lines = "\n".join([f"  ✅ {s}" for s in suggestions]) if suggestions else "  Not enough variance in data yet."

    text = (
        "🎯 *Suggested Entry Filters*\n"
        f"_(based on {result['based_on']} tokens, {result['runner_rate']}% runner rate)_\n\n"
        f"{lines}\n\n"
        "These thresholds split runners from instant dumps in your data. "
        "Refine them as more data comes in."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tokens = get_recent_tokens(10)
    if not tokens:
        await update.message.reply_text("No tokens in database yet. Run /collect first.")
        return

    lines = []
    for t in tokens:
        outcome_emoji = {"Runner": "🟢", "Instant dump": "🔴", "Slow bleed": "🟡"}.get(t.outcome, "⚪")
        lines.append(
            f"{outcome_emoji} *{t.ticker or 'Unknown'}* — ATH: `{fmt_usd(t.ath)}`\n"
            f"   Liq@10k: `{fmt_usd(t.liquidity_at_10k)}` | Top10: `{fmt_pct(t.top10_holder_pct)}`\n"
            f"   `{t.ca[:8]}...{t.ca[-4:]}`"
        )

    text = "🕐 *Recent Tokens*\n\n" + "\n\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def token_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /token <contract_address>")
        return

    ca = context.args[0].strip()
    token = search_token(ca)

    if not token:
        await update.message.reply_text(
            f"Token `{ca}` not in database.\n\nRun /collect to refresh data.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    outcome_emoji = {"Runner": "🟢", "Instant dump": "🔴", "Slow bleed": "🟡"}.get(token.outcome, "⚪")

    text = (
        f"{outcome_emoji} *{token.name} (${token.ticker})*\n\n"
        f"CA: `{token.ca}`\n"
        f"Migrated: `{token.migration_time.strftime('%Y-%m-%d %H:%M') if token.migration_time else 'N/A'}`\n\n"
        f"Liquidity at 10k: `{fmt_usd(token.liquidity_at_10k)}`\n"
        f"Liquidity at 100k: `{fmt_usd(token.liquidity_at_100k)}`\n"
        f"ATH mcap: `{fmt_usd(token.ath)}`\n"
        f"Bundler %: `{fmt_pct(token.bundler_pct)}`\n"
        f"Top 10 holders: `{fmt_pct(token.top10_holder_pct)}`\n"
        f"Time before dump: `{fmt_time(token.time_before_dump)}`\n"
        f"Outcome: `{token.outcome}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# CALLBACK HANDLER (inline buttons)
# ─────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Route directly using query.message — no fake Update needed
    async def reply(text, **kwargs):
        await query.message.reply_text(text, **kwargs)

    if query.data == "collect":
        await reply("Use the /collect command to start data collection.")

    elif query.data == "summary":
        stats = get_summary_stats()
        if stats["total"] == 0:
            await reply("No data yet. Run /collect first.", reply_markup=main_keyboard())
            return
        text = (
            "📊 *Database Summary*\n\n"
            f"Total tokens tracked: `{stats['total']}`\n\n"
            f"🟢 Runners: `{stats['runners']}` ({stats['runner_rate']}%)\n"
            f"🔴 Instant dumps: `{stats['instant_dump']}`\n"
            f"🟡 Slow bleeds: `{stats['slow_bleed']}`\n\n"
            f"Avg ATH mcap: `{fmt_usd(stats['avg_ath_mcap'])}`\n"
            f"Avg time before dump: `{fmt_time(stats['avg_dump_time_mins'])}`"
        )
        await reply(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

    elif query.data == "patterns":
        stats = get_summary_stats()
        if stats["total"] < 5:
            await reply("Need more data. Run /collect first.")
            return
        p = get_pattern_analysis()
        r = p["runners"]
        d = p["instant_dumps"]
        text = (
            "🔍 *Pattern Analysis*\n\n"
            "*Runners avg metrics:*\n"
            f"  Liquidity at 10k: `{fmt_usd(r['avg_liq_at_10k'])}`\n"
            f"  Liquidity at 100k: `{fmt_usd(r['avg_liq_at_100k'])}`\n"
            f"  Bundler %: `{fmt_pct(r['avg_bundler_pct'])}`\n"
            f"  Top 10 holders: `{fmt_pct(r['avg_top10_pct'])}`\n\n"
            "*Instant dump avg metrics:*\n"
            f"  Liquidity at 10k: `{fmt_usd(d['avg_liq_at_10k'])}`\n"
            f"  Liquidity at 100k: `{fmt_usd(d['avg_liq_at_100k'])}`\n"
            f"  Bundler %: `{fmt_pct(d['avg_bundler_pct'])}`\n"
            f"  Top 10 holders: `{fmt_pct(d['avg_top10_pct'])}`"
        )
        await reply(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

    elif query.data == "filters":
        result = get_safe_filter_suggestions()
        if "message" in result:
            await reply(result["message"])
            return
        suggestions = result["suggestions"]
        lines = "\n".join([f"  ✅ {s}" for s in suggestions]) if suggestions else "  Not enough variance in data yet."
        text = (
            "🎯 *Suggested Entry Filters*\n"
            f"_(based on {result['based_on']} tokens, {result['runner_rate']}% runner rate)_\n\n"
            f"{lines}\n\n"
            "These thresholds split runners from instant dumps in your data."
        )
        await reply(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

    elif query.data == "recent":
        tokens = get_recent_tokens(10)
        if not tokens:
            await reply("No tokens in database yet. Run /collect first.")
            return
        lines = []
        for t in tokens:
            outcome_emoji = {"Runner": "🟢", "Instant dump": "🔴", "Slow bleed": "🟡"}.get(t.outcome, "⚪")
            lines.append(
                f"{outcome_emoji} *{t.ticker or 'Unknown'}* — ATH: `{fmt_usd(t.ath)}`\n"
                f"   Liq@10k: `{fmt_usd(t.liquidity_at_10k)}` | Top10: `{fmt_pct(t.top10_holder_pct)}`\n"
                f"   `{t.ca[:8]}...{t.ca[-4:]}`"
            )
        text = "🕐 *Recent Tokens*\n\n" + "\n\n".join(lines)
        await reply(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

    init_db()
    logger.info("Database initialized")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("collect", collect_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("patterns", patterns_cmd))
    app.add_handler(CommandHandler("filters", filters_cmd))
    app.add_handler(CommandHandler("recent", recent_cmd))
    app.add_handler(CommandHandler("token", token_lookup))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()