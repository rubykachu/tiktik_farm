"""How much of each list a single response carries.

Kept together so the operator-facing limits can be read as one policy rather
than hunted for across routers:

    50 channels per page
    -> 30 days of run history per channel
       -> 30 runs per day, with "load more" inside the day

Runs are bounded by day rather than by row so a day is never split across a
page boundary — the day headings would otherwise disagree with the rows under
them. 30 days matches RUN_RETENTION_DAYS in the scheduler, so that cap only
ever hides rows that are already scheduled for deletion.
"""
from __future__ import annotations

# Hard ceiling on any client-supplied `size`, so one request cannot ask for
# the whole table.
MAX_PAGE_SIZE = 200

PIPELINES_PER_PAGE = 50
VIDEOS_PER_PAGE = 50
DAYS_PER_RESPONSE = 30
RUNS_PER_DAY = 30
