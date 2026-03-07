"""
APEX Market-Hours Filter — services/signal_engine/filters.py

Fixes implemented in this file
───────────────────────────────
  HI-5   DST-aware market-hours check.
         Previous implementation used a fixed UTC-5 offset which was wrong
         during Eastern Daylight Time (UTC-4, Mar–Nov).
         Fix: use ZoneInfo("America/New_York") which handles DST automatically.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo  # HI-5: stdlib, no pytz dependency

import structlog

logger = structlog.get_logger(__name__)

# HI-5 FIX 2026-02-27: DST-aware New York timezone
NY_TZ = ZoneInfo("America/New_York")

# NYSE regular session: 09:30 – 16:00 ET
MARKET_OPEN  = time(9,  30)
MARKET_CLOSE = time(16, 0)


class MarketHoursFilter:
    """
    DST-aware US equity market hours gate.

    HI-5 FIX: All comparisons are done in America/New_York local time,
    which automatically accounts for Eastern Standard / Daylight transitions.

    Usage
    ─────
    f = MarketHoursFilter()
    f.is_market_open(datetime.now(ZoneInfo("America/New_York")))
    # Or pass a UTC datetime — it will be converted automatically.
    """

    def is_market_open(self, dt: datetime | None = None) -> bool:
        """
        Return True if dt falls within NYSE regular session (Mon–Fri, 09:30–16:00 ET).

        Parameters
        ----------
        dt : datetime (any timezone or naive-UTC).
             Defaults to now() if None.
        """
        if dt is None:
            dt = datetime.now(NY_TZ)

        # HI-5 FIX: convert to NY local time regardless of input timezone
        dt_ny = dt.astimezone(NY_TZ)

        # Weekday: 0=Monday … 4=Friday
        if dt_ny.weekday() >= 5:
            return False  # weekend

        t = dt_ny.time().replace(tzinfo=None)
        in_session = MARKET_OPEN <= t < MARKET_CLOSE

        logger.debug(
            "market_hours_check",
            dt_ny=dt_ny.isoformat(),
            in_session=in_session,
        )
        return in_session

    def minutes_to_open(self, dt: datetime | None = None) -> float:
        """
        Returns minutes until next market open (negative if already open).
        Useful for scheduling pre-market warm-up tasks.
        """
        if dt is None:
            dt = datetime.now(NY_TZ)
        dt_ny = dt.astimezone(NY_TZ)

        # Construct today's open in NY time
        from datetime import timedelta
        open_today = dt_ny.replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
            second=0, microsecond=0
        )
        diff = (open_today - dt_ny).total_seconds() / 60.0
        # If open already passed today, add one business day
        if diff < 0 and dt_ny.weekday() < 5:
            diff += 24 * 60
        return diff
