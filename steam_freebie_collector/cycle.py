from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


CYCLE_START_TIME = time(21, 0, 0)


@dataclass(frozen=True, slots=True)
class OperationalCycle:
    cycle_id: str
    start_local: datetime
    end_local: datetime


def operational_cycle(now: datetime | None = None) -> OperationalCycle:
    """Return the 21:00-to-21:00 operational cycle in the host's local timezone."""
    local_now = now if now is not None else datetime.now().astimezone()
    if local_now.tzinfo is None or local_now.utcoffset() is None:
        local_now = local_now.astimezone()

    cycle_date = local_now.date()
    if local_now.timetz().replace(tzinfo=None) < CYCLE_START_TIME:
        cycle_date -= timedelta(days=1)

    start = datetime.combine(cycle_date, CYCLE_START_TIME, tzinfo=local_now.tzinfo)
    end = start + timedelta(days=1)
    return OperationalCycle(cycle_id=cycle_date.isoformat(), start_local=start, end_local=end)

