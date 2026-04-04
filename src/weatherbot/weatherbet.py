"""
Entry point and backward-compatibility shim.

Run directly:
    uv run src/weatherbot/weatherbet.py
    uv run src/weatherbot/weatherbet.py status
    uv run src/weatherbot/weatherbet.py report

The public names re-exported below keep backfill.py working without changes.
"""

import os
import sys
import time
import threading
from datetime import datetime, timezone

from .config import SCAN_INTERVAL, LOCATIONS, LOG_DIR

# Backward-compatibility re-exports (used by backfill.py)
from .config    import CONFIG, DATA_DIR, VC_KEY              # noqa: F401
from .config    import LOCATIONS, TIMEZONES                  # noqa: F401
from .portfolio import load_market, save_market, run_calibration, _now_iso  # noqa: F401

from .bot import WeatherBot
from .portfolio import _now_iso
from .telegram_bot import load_from_config, notify_scan_done


class _Tee:
    """Write to multiple streams simultaneously (stdout + log file)."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()

    def fileno(self):
        return self._streams[0].fileno()


def _setup_logging() -> None:
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path  = os.path.join(LOG_DIR, f"bot_{date_str}.log")
    log_file  = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    print(f"[log] Logging to {log_path}")


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
        last_n = None
        if "--last" in sys.argv:
            idx = sys.argv.index("--last")
            try:
                last_n = int(sys.argv[idx + 1])
            except (IndexError, ValueError):
                print("Usage: report --last N")
                return
        bot.cmd_report(last_n=last_n)
        return
    if arg == "edge":
        bot.cmd_edge()
        return
    if arg == "scan":
        bot.cmd_scan_dry()
        return

    _setup_logging()
    print("Polymarket Weather Bot starting…")
    print(f"Scanning {len(LOCATIONS)} cities every {SCAN_INTERVAL}s. Ctrl-C to stop.\n")

    def _monitor_loop() -> None:
        while True:
            time.sleep(600)
            try:
                bot.monitor_stops()
            except Exception as exc:
                print(f"[monitor_stops] Error: {exc}")
                if tg:
                    tg.send(f"⚠️ <b>monitor_stops error:</b> {exc}")

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
