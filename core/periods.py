"""Month-based reporting periods. All passport figures are grouped by calendar
month (Gregorian) in Africa/Addis_Ababa time. Ethiopian-calendar display is a
planned localisation, not a calculation change."""

import datetime as dt
import re

from django.utils import timezone

MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def month_start(day):
    return day.replace(day=1)


def add_months(day, n):
    month_index = day.year * 12 + (day.month - 1) + n
    return dt.date(month_index // 12, month_index % 12 + 1, 1)


def month_end(day):
    return add_months(month_start(day), 1) - dt.timedelta(days=1)


def months_between(start, end):
    """Every first-of-month from start's month to end's month, inclusive."""
    months = []
    current = month_start(start)
    last = month_start(end)
    while current <= last:
        months.append(current)
        current = add_months(current, 1)
    return months


def parse_month(value):
    """'2026-03' -> date(2026, 3, 1). Raises ValueError for anything else."""
    match = MONTH_RE.match((value or "").strip())
    if not match:
        raise ValueError("Use the format YYYY-MM.")
    year, month = int(match[1]), int(match[2])
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError("That month is out of range.")
    return dt.date(year, month, 1)


def today():
    return timezone.localdate()


def default_period(months=12):
    """The last `months` months up to and including the current month."""
    end = month_end(today())
    start = add_months(month_start(end), -(months - 1))
    return start, end


def label(day):
    return day.strftime("%b %Y")
