"""The operator's timezone.

Everything the operator reads — caption `{now}`, Telegram timestamps, the day
headings in a pipeline's run log — has to agree on where a day starts. Grouping
in UTC while rendering in local time silently files a run at 00:30 under the
previous day, which is only visible as a heading that disagrees with the rows
under it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ_OPERATOR = ZoneInfo("Asia/Ho_Chi_Minh")


def to_local(value: datetime) -> datetime:
    """Convert to the operator's timezone, assuming UTC when tzinfo is absent.

    SQLite drops tzinfo, so values read back from the DB are naive UTC.
    """
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(TZ_OPERATOR)


def local_day(value: datetime) -> str:
    """The calendar day this instant falls on, as the operator sees it."""
    return to_local(value).strftime("%Y-%m-%d")


def sqlite_day_offset() -> str:
    """SQLite date modifier that shifts a UTC timestamp into the operator's day.

    SQLite has no timezone support, so bucketing rows by local day in SQL needs
    a literal offset such as "+7 hours". That is only sound for a zone whose
    offset never changes; under DST it would mis-bucket two days a year, and
    silently. Asia/Ho_Chi_Minh has been a fixed +07:00 since 1975, so this
    asserts the property rather than assuming it — swapping TZ_OPERATOR for a
    DST zone should fail loudly here, not produce wrong day headings.
    """
    year = datetime.now(timezone.utc).year
    offsets = {
        datetime(year, month, 15, tzinfo=timezone.utc)
        .astimezone(TZ_OPERATOR)
        .utcoffset()
        for month in range(1, 13)
    }
    if len(offsets) != 1:
        raise RuntimeError(
            f"{TZ_OPERATOR} observes DST; day bucketing in SQL needs a fixed "
            "offset. Group in Python instead."
        )
    # Minutes, not hours: SQLite takes one modifier per argument, and a zone
    # at a half-hour offset would otherwise need two.
    return f"{int(offsets.pop().total_seconds() // 60):+d} minutes"
