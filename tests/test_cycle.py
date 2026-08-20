from datetime import datetime, timedelta, timezone

from steam_freebie_collector.cycle import operational_cycle


SGT = timezone(timedelta(hours=8))


def test_cycle_immediately_before_2100_uses_previous_date():
    cycle = operational_cycle(datetime(2026, 8, 20, 20, 59, 59, 999999, tzinfo=SGT))
    assert cycle.cycle_id == "2026-08-19"
    assert cycle.start_local == datetime(2026, 8, 19, 21, 0, tzinfo=SGT)
    assert cycle.end_local == datetime(2026, 8, 20, 21, 0, tzinfo=SGT)


def test_cycle_at_2100_uses_current_date():
    cycle = operational_cycle(datetime(2026, 8, 20, 21, 0, 0, tzinfo=SGT))
    assert cycle.cycle_id == "2026-08-20"
    assert cycle.start_local == datetime(2026, 8, 20, 21, 0, tzinfo=SGT)

