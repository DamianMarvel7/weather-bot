"""
Entry point and backward-compatibility shim.

Run directly:
    uv run src/weatherbot/weatherbet.py
    uv run src/weatherbot/weatherbet.py status
    uv run src/weatherbot/weatherbet.py report

The public names re-exported below keep backfill.py working without changes.
"""

import sys
import time
import threading

from .config import SCAN_INTERVAL, LOCATIONS

# Backward-compatibility re-exports (used by backfill.py)
from .config    import CONFIG, DATA_DIR, VC_KEY              # noqa: F401
from .config    import LOCATIONS, TIMEZONES                  # noqa: F401
from .portfolio import load_market, save_market, run_calibration, _now_iso  # noqa: F401

from .bot import WeatherBot
from .portfolio import _now_iso
from .telegram_bot import load_from_config, notify_scan_done


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    bot = WeatherBot()

    # Attach Telegram notifier if token is configured in config.json
    tg = load_from_config()
    if tg:
        tg.set_bot(bot)
        bot.tg = tg
        tg.start_polling()
    else:
        print("[Telegram] Not configured — add telegram_token + telegram_chat_id to config.json to enable.")

    if arg == "status":
        bot.cmd_status()
        return
    if arg == "report":
        bot.cmd_report()
        return
    if arg == "edge":
        bot.cmd_edge()
        return

    print("Polymarket Weather Bot starting…")
    print(f"Scanning {len(LOCATIONS)} cities every {SCAN_INTERVAL}s. Ctrl-C to stop.\n")

    def _monitor_loop() -> None:
        while True:
            time.sleep(600)
            try:
                bot.monitor_stops()
            except Exception:
                pass

    threading.Thread(target=_monitor_loop, daemon=True).start()

    while True:
        print(f"[{_now_iso()}] Running full scan…")
        try:
            bot.scan_and_update()
            if tg:
                notify_scan_done(tg, bot, len(LOCATIONS))
        except Exception as exc:
            print(f"Scan error: {exc}")
            if tg:
                tg.send(f"⚠️ <b>Scan error:</b> {exc}")
        print(f"[{_now_iso()}] Scan complete. Sleeping {SCAN_INTERVAL}s.\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
