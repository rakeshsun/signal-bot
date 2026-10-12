"""Data sources. ``tastytrade`` streams live candles; ``csvfile`` loads bars for offline backtests.

To add another source (another broker, a database), provide the same two functions as
tastytrade.py: ``fetch_history(...) -> list[Bar]`` and a streaming updater for live runs.
"""
