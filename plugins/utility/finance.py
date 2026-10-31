"""
NovaCore Plugin: Real-Time Crypto & Stock Market Ticker (.price, .stock)
Retrieves live market prices, 24h percentage changes, highs/lows, and volume
directly from Binance Global, CoinGecko, and Yahoo Finance APIs with zero keys required.
"""

from __future__ import annotations

import logging
import aiohttp
from core.dispatcher import client_cmd, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.utility.finance")

# Common crypto symbol aliases
CRYPTO_ALIASES = {
    "btc": "BTCUSDT",
    "bitcoin": "BTCUSDT",
    "eth": "ETHUSDT",
    "ethereum": "ETHUSDT",
    "sol": "SOLUSDT",
    "solana": "SOLUSDT",
    "ton": "TONUSDT",
    "doge": "DOGEUSDT",
    "dogecoin": "DOGEUSDT",
    "xrp": "XRPUSDT",
    "ripple": "XRPUSDT",
    "bnb": "BNBUSDT",
    "binance": "BNBUSDT",
    "trx": "TRXUSDT",
    "tron": "TRXUSDT",
    "ada": "ADAUSDT",
    "cardano": "ADAUSDT",
    "sui": "SUIUSDT",
    "pepe": "PEPEUSDT",
    "shib": "SHIBUSDT",
    "avax": "AVAXUSDT",
    "near": "NEARUSDT",
    "link": "LINKUSDT",
    "dot": "DOTUSDT",
    "ltc": "LTCUSDT",
}


@client_cmd(
    pattern="price",
    description="Check real-time crypto prices & 24h market stats (.price <coin>)",
    module="utility",
)
async def crypto_price_cmd(event: SafeMessageWrapper, coin: str = ""):
    query = coin.lower().strip() if coin else "btc"
    symbol = CRYPTO_ALIASES.get(query, f"{query.upper()}USDT")

    await event.edit(f"🔍 **Fetching live market quote for `{symbol}`...**")

    # Try Binance 24hr API
    binance_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(binance_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    last_price = float(data.get("lastPrice", 0))
                    change_pct = float(data.get("priceChangePercent", 0))
                    high_price = float(data.get("highPrice", 0))
                    low_price = float(data.get("lowPrice", 0))
                    quote_volume = float(data.get("quoteVolume", 0))

                    trend_emoji = "🟢 ↗" if change_pct >= 0 else "🔴 ↘"
                    sign = "+" if change_pct > 0 else ""

                    # Format numbers nicely
                    def fmt_price(p: float) -> str:
                        if p >= 1000:
                            return f"${p:,.2f}"
                        elif p >= 1:
                            return f"${p:,.4f}"
                        else:
                            return f"${p:,.6f}"

                    def fmt_vol(v: float) -> str:
                        if v >= 1_000_000_000:
                            return f"${v / 1_000_000_000:.2f}B"
                        elif v >= 1_000_000:
                            return f"${v / 1_000_000:.2f}M"
                        return f"${v:,.0f}"

                    clean_name = symbol.replace("USDT", "")
                    card = [
                        f"💎 **{clean_name} / USDT Market Ticker**",
                        "──────────────────────────────",
                        f"💵 **Price:** `{fmt_price(last_price)}`",
                        f"📊 **24h Change:** `{sign}{change_pct:.2f}%` {trend_emoji}",
                        f"⬆️ **24h High:** `{fmt_price(high_price)}`",
                        f"⬇️ **24h Low:** `{fmt_price(low_price)}`",
                        f"💰 **24h Volume:** `{fmt_vol(quote_volume)} USDT`",
                        "──────────────────────────────",
                        "⚡ *Live data via Binance Global*",
                    ]
                    await event.edit("\n".join(card))
                    return
    except Exception as e:
        logger.debug("Binance price lookup failed: %s", e)

    # Fallback to CoinGecko
    try:
        cg_url = f"https://api.coingecko.com/api/v3/simple/price?ids={query}&vs_currencies=usd&include_24hr_change=true"
        async with aiohttp.ClientSession() as session:
            async with session.get(cg_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if query in data:
                        p = data[query].get("usd", 0)
                        c = data[query].get("usd_24h_change", 0)
                        trend_emoji = "🟢" if c >= 0 else "🔴"
                        sign = "+" if c > 0 else ""
                        msg = (
                            f"💎 **{query.upper()} / USD**\n"
                            "──────────────────────────────\n"
                            f"💵 **Price:** `${p:,.4f}`\n"
                            f"📊 **24h Change:** `{sign}{c:.2f}%` {trend_emoji}\n"
                            "──────────────────────────────\n"
                            "⚡ *Live data via CoinGecko*"
                        )
                        await event.edit(msg)
                        return
    except Exception as e:
        logger.debug("CoinGecko lookup failed: %s", e)

    await event.edit(f"❌ **Symbol not found:** `{coin}`. Try `.price btc`, `.price eth`, `.price sol`.")


@client_cmd(
    pattern="stock",
    description="Check real-time stock price and 24h market stats (.stock <symbol>)",
    module="utility",
)
async def stock_price_cmd(event: SafeMessageWrapper, symbol: str = ""):
    target = symbol.upper().strip() if symbol else "AAPL"
    await event.edit(f"📈 **Fetching stock quote for `{target}`...**")

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{target}?interval=1d"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    await event.edit(f"❌ **Stock symbol not found or unavailable:** `{target}`")
                    return

                data = await resp.json()
                result = data.get("chart", {}).get("result", [])
                if not result:
                    await event.edit(f"❌ **No market data available for:** `{target}`")
                    return

                meta = result[0].get("meta", {})
                currency = meta.get("currency", "USD")
                last_price = meta.get("regularMarketPrice", 0.0)
                prev_close = meta.get("chartPreviousClose", meta.get("previousClose", last_price))
                day_high = meta.get("regularMarketDayHigh", 0.0)
                day_low = meta.get("regularMarketDayLow", 0.0)
                instrument = meta.get("instrumentType", "EQUITY")

                diff = last_price - prev_close
                diff_pct = (diff / prev_close * 100) if prev_close else 0.0
                sign = "+" if diff > 0 else ""
                trend_emoji = "🟢 ↗" if diff >= 0 else "🔴 ↘"

                card = [
                    f"📈 **{target} Market Quote ({instrument})**",
                    "──────────────────────────────",
                    f"💵 **Price:** `${last_price:,.2f} {currency}`",
                    f"📊 **Change:** `{sign}{diff:,.2f} ({sign}{diff_pct:.2f}%)` {trend_emoji}",
                    f"⬆️ **Day High:** `${day_high:,.2f}`",
                    f"⬇️ **Day Low:** `${day_low:,.2f}`",
                    f"🏁 **Prev Close:** `${prev_close:,.2f}`",
                    "──────────────────────────────",
                    "⚡ *Source: Real-time Global Market Feed*",
                ]
                await event.edit("\n".join(card))
    except Exception as e:
        logger.exception("Stock lookup failed: %s", e)
        await event.edit(f"❌ **Stock Lookup Failed:** `{e}`")
