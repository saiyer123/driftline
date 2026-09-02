from datetime import datetime, timezone

from driftline.data.alpaca_feed import is_bar_complete


def ts(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def test_in_progress_session_bar_is_incomplete():
    bar = ts(9, 1, 4)               # Sep 1 daily bar (Alpaca stamps 04:00 UTC)
    assert not is_bar_complete(bar, now=ts(9, 1, 13, 40))  # 9:40am ET, session open
    assert not is_bar_complete(bar, now=ts(9, 1, 20, 0))   # 4:00pm EDT, not yet 16:05


def test_bar_complete_after_close_daylight_time():
    bar = ts(9, 1, 4)
    assert is_bar_complete(bar, now=ts(9, 1, 20, 10))  # 4:10pm EDT
    assert is_bar_complete(bar, now=ts(9, 2, 13, 0))   # any later day


def test_winter_bar_not_complete_at_20_utc():
    # January: 4pm ET is 21:00 UTC. A fixed 20:05 UTC rule would publish the
    # in-progress bar an hour before the close — the DST regression.
    bar = datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)
    assert not is_bar_complete(bar, now=datetime(2026, 1, 15, 20, 30, tzinfo=timezone.utc))
    assert is_bar_complete(bar, now=datetime(2026, 1, 15, 21, 10, tzinfo=timezone.utc))


def test_prior_day_bar_always_complete():
    assert is_bar_complete(ts(9, 1, 4), now=ts(9, 2, 9, 0))
