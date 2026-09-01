"""Tunable parameters for the baseline momentum rotation.

This file is the unit of strategy evolution: the strategist proposes new
values as a `candidate/<date>` git branch editing ONLY this file, backed by
its walk-forward report. Promotion = a human reviews and merges the branch
(then restarts the engine). Never edited by automation on main.
"""

LOOKBACK = 63        # momentum lookback, trading days
TOP_N = 3            # ETFs held
TARGET_GROSS = 0.9   # target gross exposure fraction (pre risk-appetite scaling)
REBALANCE_DAYS = 7   # minimum days between rebalances
