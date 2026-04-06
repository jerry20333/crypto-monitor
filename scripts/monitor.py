#!/usr/bin/env python3
"""
Crypto Market Monitor
每小時抓取熱門板塊行情，偵測1小時內漲跌超過10%並推送 Telegram
資料來源：Binance 公開 API（免費、無 API Key）
state.json 透過 git commit 持久化，確保1h漲跌計算正確
"""

import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ALERT_THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", "10.0"))
STATE_FILE = "state.json"
TW_TZ = timezone(timedelta(hours=8))

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SECTORS = {
    "🤖 AI":     ["FETUSDT", "AGIXUSDT", "OCEANUSDT", "RENDERUSDT",
                  "WORLDCOINUSDT", "ARKMUSDT", "NFPUSDT", "AIUSDT", "GRTUSDT"],
    "🏦 DeFi":   ["UNIUSDT", "AAVEUSDT", "MKRUSDT", "CRVUSDT", "SNXUSDT",
                  "COMPUSDT", "DYDXUSDT", "SUSHIUSDT", "1INCHUSDT", "BALUSDT"],
    "⚡ Layer2": ["MATICUSDT", "OPUSDT", "ARBUSDT", "STRKUSDT",
                  "METISUSDT", "LRCUSDT", "IMXUSDT"],
    "🐸 Meme":   ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT", "BONKUSDT",
                  "WIFUSDT", "MEMEUSDT", "TURBOUSDT", "BOMEUSDT"],
    "🎮 GameFi": ["AXSUSDT", "SANDUSDT", "MANAUSDT", "ENJUSDT",
                  "GALAUSDT", "YGGUSDT", "BEAMUSDT"],
    "🔗 Layer1": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT",
                  "DOTUSDT", "NEARUSDT", "ATOMUSDT", "FTMUSDT", "ALGOUSDT"],
}


def fetch_smart_money_signals(chain_id="CT_501", page_size=5):
    url = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai"
    payload = json.dumps({
        "smartSignalType": "", "page": 1,
        "pageSize": page_size, "chainId": chain_id
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "binance-web3/1.1"
    })
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        data = json.loads(resp.read())
    if data.get("success") and data.get("data"):
        return data["data"]
    return []


def fmt_signal(s):
    direction = "🟢 買入" if s.get("direction") == "buy" else "🔴 賣出"
    ticker = s.get("ticker", "?")
    alert_price = fmt_price(s.get("alertPrice"))
    current_price = fmt_price(s.get("currentPrice"))
    max_gain = s.get("maxGain", "N/A")
    exit_rate = s.get("exitRate", "N/A")
    smart_count = s.get("smartMoneyCount", "?")
    status_map = {"active": "🟡 進行中", "timeout": "⏰ 已逾時", "completed": "✅ 已完成"}
    status = status_map.get(s.get("status", ""), s.get("status", ""))
    platform = s.get("launchPlatform") or ""
    platform_str = f" | {platform}" if platform else ""
    tags = []
    for tag_list in (s.get("tokenTag") or {}).values():
        for t in tag_list:
            tags.append(t.get("tagName", ""))
    tag_str = " · ".join(tags[:2]) if tags else ""
    return (
        f"  <b>{ticker}</b> {direction}{platform_str}\n"
        f"  觸發價 {alert_price} → 現價 {current_price}\n"
        f"  最大漲幅 <b>{max_gain}%</b> | 出場率 {exit_rate}% | {smart_count}個大戶\n"
        f"  {status}" + (f" | {tag_str}" if tag_str else "")
    )


def fetch_tickers():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    req = urllib.request.Request(url, headers={"User-Agent": "CryptoMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        data = json.loads(resp.read())
    return {item["symbol"]: item for item in data}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
        return json.loads(resp.read())


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def fmt_pct(val):
    if val is None:
        return "N/A"
    sign = "🔺" if float(val) > 0 else "🔻"
    return f"{sign}{abs(float(val)):.2f}%"


def fmt_price(val):
    if val is None:
        return "N/A"
    v = float(val)
    return f"${v:,.3f}" if v >= 1 else f"${v:.6f}"


def run_monitor():
    now = datetime.now(TW_TZ)
    is_daily_report = now.hour == 8
    state = load_state()
    new_state = {}
    alerts = []
    daily_tops = []

    try:
        all_tickers = fetch_tickers()
    except Exception as e:
        print(f"[ERROR] 無法抓取 Binance 資料: {e}")
        return

    for sector_name, symbols in SECTORS.items():
        sector_alerts = []
        top3 = []

        for symbol in symbols:
            ticker = all_tickers.get(symbol)
            if not ticker:
                continue
            price = ticker.get("lastPrice")
            pct_24h = ticker.get("priceChangePercent")
            prev_price = state.get(symbol, {}).get("price")
            new_state[symbol] = {"price": price}

            if prev_price and float(prev_price) > 0:
                pct_1h = (float(price) - float(prev_price)) / float(prev_price) * 100
                if abs(pct_1h) >= ALERT_THRESHOLD:
                    sym_short = symbol.replace("USDT", "")
                    sector_alerts.append(
                        f"  <b>{sym_short}</b> {fmt_pct(pct_1h)} (1h) | {fmt_price(price)}"
                    )

            if len(top3) < 3 and pct_24h is not None:
                top3.append((symbol.replace("USDT", ""), pct_24h, price))

        if sector_alerts:
            alerts.append(f"\n{sector_name}\n" + "\n".join(sector_alerts))

        if is_daily_report and top3:
            top3_sorted = sorted(top3, key=lambda x: float(x[1]), reverse=True)
            lines = "\n".join(
                f"  {i+1}. <b>{s}</b> {fmt_pct(p)} | {fmt_price(pr)}"
                for i, (s, p, pr) in enumerate(top3_sorted)
            )
            daily_tops.append(f"{sector_name}\n{lines}")

    save_state(new_state)

    smart_money_lines = []
    for chain_id, chain_name in [("CT_501", "Solana"), ("56", "BSC")]:
        try:
            signals = fetch_smart_money_signals(chain_id=chain_id)
            active = [s for s in signals if s.get("status") == "active"]
            to_show = active[:3] if active else signals[:3]
            if to_show:
                smart_money_lines.append(f"\n<b>── {chain_name} ──</b>")
                for s in to_show:
                    smart_money_lines.append(fmt_signal(s))
        except Exception as e:
            print(f"[WARN] 聰明錢 {chain_name} 抓取失敗: {e}")

    if smart_money_lines:
        msg = (f"🧠 <b>聰明錢訊號</b>\n⏰ {now.strftime('%m/%d %H:%M')}\n"
               + "\n".join(smart_money_lines))
        send_telegram(msg)
        print("[OK] 推送聰明錢訊號")

    if alerts:
        msg = (f"🚨 <b>行情異動警報</b>\n"
               f"⏰ {now.strftime('%m/%d %H:%M')} | 漲跌 ≥ {ALERT_THRESHOLD:.0f}%\n"
               + "\n".join(alerts))
        send_telegram(msg)
        print(f"[OK] 推送 {len(alerts)} 個板塊異動警報")
    else:
        print(f"[OK] {now.strftime('%H:%M')} 本輪無異動（門檻 {ALERT_THRESHOLD}%）")

    if is_daily_report and daily_tops:
        msg = (f"📊 <b>每日行情早報</b>\n"
               f"📅 {now.strftime('%Y/%m/%d')} 各板塊 Top3（24h）\n\n"
               + "\n\n".join(daily_tops))
        send_telegram(msg)
        print("[OK] 推送每日早報")


if __name__ == "__main__":
    run_monitor()
