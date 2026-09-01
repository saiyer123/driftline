from datetime import datetime, timezone

from driftline.data.alpaca_feed import is_bar_complete


def ts(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def test_in_progress_session_bar_is_incomplete():
    bar = ts(1, 4)               # Sep 1 daily bar (Alpaca stamps 04:00 UTC)
    assert not is_bar_complete(bar, now=ts(1, 13, 40))  # 9:40am ET, session open
    assert not is_bar_complete(bar, now=ts(1, 19, 59))


def test_bar_complete_after_close():
    bar = ts(1, 4)
    assert is_bar_complete(bar, now=ts(1, 20, 10))  # just after 4pm ET
    assert is_bar_complete(bar, now=ts(2, 13, 0))   # any later day


def test_prior_day_bar_always_complete():
    assert is_bar_complete(ts(1, 4), now=ts(2, 9, 0))
