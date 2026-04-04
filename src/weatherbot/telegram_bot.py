"""
Telegram integration for WeatherBot.

Uses the Telegram Bot API directly via requests (no extra dependencies).

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Start a chat with your bot, then run:
       python -c "
       import requests
       token = 'YOUR_TOKEN_HERE'
       r = requests.get(f'https://api.telegram.org/bot{token}/getUpdates').json()
       print(r)
       "
     Send /start to your bot first, then look for 'chat' -> 'id' in the output.
  3. Add both values to config.json:
       "telegram_token": "123456:ABC...",
       "telegram_chat_id": 123456789

Usage:
  The notifier is wired up automatically in weatherbet.py when the token is set.
"""

import io
import sys
import threading
import time

import requests


class TelegramNotifier:
    """
    Sends push alerts and handles incoming commands via Telegram Bot API.

    Push notifications (bot → you):
      - Trade opened
      - Stop loss / trailing stop triggered
      - Market resolved (win or loss)
      - Hourly scan summary

    Commands (you → bot):
      /start   — welcome message
      /help    — list commands
      /status  — balance + open positions
      /report  — resolved trades P&L
      /edge    — EV calibration analysis
      /balance — quick balance check
    """

    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: int | str) -> None:
        self.token   = token
        self.chat_id = str(chat_id)
        self._offset = 0
        self._bot    = None   # WeatherBot reference set via set_bot()
        self._running = False

    # ── public API ────────────────────────────────────────────────────────────

    def set_bot(self, bot) -> None:
        """Attach the WeatherBot instance so commands can call its methods."""
        self._bot = bot

    def send(self, text: str) -> None:
        """Send a message to the configured chat."""
        try:
            url = self._API.format(token=self.token, method="sendMessage")
            r = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            data = r.json()
            if not data.get("ok"):
                print(f"[Telegram] Send failed: {data}")
        except Exception as e:
            print(f"[Telegram] Send error: {e}")

    def start_polling(self) -> None:
        """Start the background polling thread. Call once at startup."""
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
        print("[Telegram] Polling started — bot is listening for commands.")
        self.send("🤖 <b>WeatherBot online</b>\nType /help to see available commands.")

    def stop(self) -> None:
        self._running = False

    # ── internals ─────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except Exception as e:
                print(f"[Telegram] Poll error: {e}")
                time.sleep(5)

    def _get_updates(self) -> list[dict]:
        url = self._API.format(token=self.token, method="getUpdates")
        try:
            r = requests.get(
                url,
                params={"offset": self._offset, "timeout": 30},
                timeout=35,
            )
            data = r.json()
            updates = data.get("result", [])
            if updates:
                self._offset = updates[-1]["update_id"] + 1
            return updates
        except Exception:
            return []

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        # Only respond to the configured chat (security: ignore other chats)
        incoming_chat = str(msg.get("chat", {}).get("id", ""))
        if incoming_chat != self.chat_id:
            print(f"[Telegram] Ignored message from unknown chat {incoming_chat}")
            return

        text = (msg.get("text") or "").strip().lower()
        # Strip bot username suffix e.g. /status@myweather_bot → /status
        if "@" in text:
            text = text.split("@")[0]
        print(f"[Telegram] Received command: {text}")

        if text in ("/start", "start"):
            self._cmd_start()
        elif text in ("/help", "help"):
            self._cmd_help()
        elif text in ("/balance", "balance"):
            self._cmd_balance()
        elif text in ("/status", "status"):
            self._cmd_status()
        elif text in ("/report", "report"):
            self._cmd_report()
        elif text in ("/edge", "edge"):
            self._cmd_edge()
        else:
            self.send(f"❓ Unknown command: <code>{text}</code>\nType /help for the list.")

    # ── command handlers ──────────────────────────────────────────────────────

    def _cmd_start(self) -> None:
        self.send(
            "🌤 <b>WeatherBot</b> is running!\n\n"
            "I trade Polymarket weather prediction markets using:\n"
            "  • ECMWF / HRRR / METAR forecasts\n"
            "  • Kelly Criterion position sizing\n"
            "  • Expected Value (EV) edge detection\n\n"
            "Type /help to see what I can do."
        )

    def _cmd_help(self) -> None:
        self.send(
            "📋 <b>Available commands</b>\n\n"
            "/balance — current bankroll\n"
            "/status  — balance + all open positions\n"
            "/report  — resolved trades with P&amp;L\n"
            "/edge    — EV calibration &amp; win rate analysis\n"
            "/help    — this message\n\n"
            "I also push <b>automatic alerts</b> when:\n"
            "  🟢 A position is opened\n"
            "  🔴 A stop loss is triggered\n"
            "  🏁 A market resolves (win or loss)"
        )

    def _cmd_balance(self) -> None:
        if self._bot is None:
            self.send("⚠️ Bot not attached.")
            return
        bal = self._bot.state.balance
        self.send(f"💰 <b>Balance: ${bal:,.2f}</b>")

    def _cmd_status(self) -> None:
        if self._bot is None:
            self.send("⚠️ Bot not attached.")
            return
        try:
            output = _capture(self._bot.cmd_status)
            if not output:
                output = "No data yet."
            if len(output) > 3800:
                output = output[:3800] + "\n… (truncated)"
            self.send(f"📊 <b>Status</b>\n<pre>{output}</pre>")
        except Exception as e:
            print(f"[Telegram] /status error: {e}")
            self.send(f"⚠️ Error: {e}")

    def _cmd_report(self) -> None:
        if self._bot is None:
            self.send("⚠️ Bot not attached.")
            return
        try:
            output = _capture(self._bot.cmd_report)
            if len(output) > 3800:
                output = output[:3800] + "\n… (truncated)"
            self.send(f"📋 <b>Report</b>\n<pre>{output if output else 'No resolved trades yet.'}</pre>")
        except Exception as e:
            print(f"[Telegram] /report error: {e}")
            self.send(f"⚠️ Error: {e}")

    def _cmd_edge(self) -> None:
        if self._bot is None:
            self.send("⚠️ Bot not attached.")
            return
        try:
            output = _capture(self._bot.cmd_edge)
            if len(output) > 3800:
                output = output[:3800] + "\n… (truncated)"
            self.send(f"🎯 <b>Edge Analysis</b>\n<pre>{output if output else 'No closed positions yet.'}</pre>")
        except Exception as e:
            print(f"[Telegram] /edge error: {e}")
            self.send(f"⚠️ Error: {e}")


# ── push notification helpers (called from bot.py) ────────────────────────────

def notify_opened(tg: TelegramNotifier, market: dict, pos: dict) -> None:
    city  = market.get("city_name", market.get("city", ""))
    date  = market.get("date", "")
    ev    = pos.get("ev", 0)
    kelly = pos.get("kelly", 0)
    size  = pos.get("size", 0)
    entry = pos.get("entry_ask", 0)
    snaps = market.get("forecast_snapshots", [])
    fc    = snaps[-1].get("best") if snaps else None
    src   = snaps[-1].get("best_source", "") if snaps else ""
    fc_str = f"{fc:.1f}° ({src})" if fc is not None else "—"

    opened_at = pos.get("opened_at", "")
    tg.send(
        f"🟢 <b>POSITION OPENED</b>\n"
        f"🕐 {opened_at}\n"
        f"📍 {city} · {date}\n"
        f"🎯 Bucket: <b>{pos.get('bucket','?')}</b>\n"
        f"🌡 Forecast: {fc_str}\n"
        f"💡 EV: <b>{ev:+.3f}</b>  |  Kelly: {kelly*100:.1f}%\n"
        f"💵 Size: ${size:.2f}  |  Entry: {entry:.3f}"
    )


def notify_closed(tg: TelegramNotifier, market: dict, pos: dict,
                  reason: str, exit_bid: float) -> None:
    city  = market.get("city_name", market.get("city", ""))
    date  = market.get("date", "")
    size  = pos.get("size", 0)
    entry = pos.get("entry_ask", 1)
    pnl   = market.get("pnl")

    if pnl is None:
        # Estimate from exit price
        shares = size / entry if entry else 0
        pnl = shares * exit_bid - size

    icon = "✅" if pnl > 0 else "🔴"
    reason_labels = {
        "resolved":       "Market resolved",
        "stop_loss":      "🛑 Stop loss hit",
        "trailing_stop":  "📉 Trailing stop hit",
        "forecast_change":"🌡 Forecast changed",
    }
    reason_str = reason_labels.get(reason, reason)

    extra = ""
    if reason == "resolved":
        pm_resolved = market.get("resolved_outcome", "?")
        vc_actual   = market.get("actual_temp")
        vc_str      = f"{vc_actual:.1f}°" if vc_actual is not None else "?"
        extra = f"\n🏆 PM resolved: <b>{pm_resolved}</b>\n🌡 VC actual: <b>{vc_str}</b>"
    elif reason == "stop_loss":
        drop_pct = (entry - exit_bid) / entry * 100 if entry else 0
        extra = (
            f"\n📉 Bid dropped <b>{drop_pct:.1f}%</b> below entry"
            f" ({entry:.3f} → {exit_bid:.3f})"
        )
    elif reason == "trailing_stop":
        peak     = pos.get("peak_bid") or entry
        peak_pct = (peak - entry) / entry * 100 if entry else 0
        fall_pct = (peak - exit_bid) / peak * 100 if peak else 0
        extra = (
            f"\n📈 Rode up <b>+{peak_pct:.1f}%</b> (peak {peak:.3f})"
            f", then fell <b>{fall_pct:.1f}%</b> back to {exit_bid:.3f}"
        )
    elif reason == "forecast_change":
        d       = pos.get("close_detail") or {}
        fc      = d.get("forecast_temp")
        src     = d.get("best_source", "")
        bucket  = pos.get("bucket", "?")
        src_str = f" ({src})" if src else ""
        fc_str  = f"{fc:.1f}°{src_str}" if fc is not None else "—"
        extra = (
            f"\n🌡 Forecast now <b>{fc_str}</b>"
            f" — moved outside bucket <b>{bucket}</b>"
        )

    closed_at  = pos.get("closed_at", "")
    opened_at  = pos.get("opened_at", "")
    tg.send(
        f"{icon} <b>POSITION CLOSED</b>\n"
        f"🕐 {closed_at}\n"
        f"📍 {city} · {date}\n"
        f"🎯 Our bucket: <b>{pos.get('bucket','?')}</b>{extra}\n"
        f"📌 Reason: {reason_str}\n"
        f"💵 Entry: {entry:.3f}  →  Exit: {exit_bid:.3f}\n"
        f"📅 Opened: {opened_at}\n"
        f"💰 P&amp;L: <b>${pnl:+.2f}</b>"
    )


def notify_scan_done(tg: TelegramNotifier, bot, n_cities: int) -> None:
    from datetime import datetime, timezone
    bal      = bot.state.balance
    scan_ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tg.send(
        f"🔄 <b>Scan complete</b>\n"
        f"🕐 {scan_ts}\n"
        f"📡 Checked {n_cities} cities\n"
        f"💰 Balance: ${bal:,.2f}"
    )


# ── utility ───────────────────────────────────────────────────────────────────

def _capture(fn) -> str:
    """Run fn(), capture its stdout, return as string."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn()
    finally:
        sys.stdout = old
    return buf.getvalue().strip()


def load_from_config() -> "TelegramNotifier | None":
    """
    Load token + chat_id from .env via config module.
    Returns None if not configured.
    """
    from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    token   = TELEGRAM_TOKEN.strip()
    chat_id = TELEGRAM_CHAT_ID.strip()
    if not token or not chat_id:
        return None
    return TelegramNotifier(token, chat_id)
