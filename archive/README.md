# Archived scripts

Scripts that once had a job but don't anymore. Kept here (rather than
deleted) because each one may come back one day when the problem it
solved returns.

## `iracing_auth_members_ng.py`

A members-ng.iracing.com API client built for the first version of
`iracing_trackmap.py`, which fetched official SVG track maps over the
network. Dead since 2025-12-09, when iRacing retired the `/auth`
endpoint in favour of OAuth2 with client credentials they've paused
issuing.

Current `iracing_trackmap.py` is fully offline (uses SIMRacingApps'
open-source track library), so this file is not imported anywhere.
When iRacing resumes issuing OAuth2 client credentials, this file
gives us a head start on the updated trackmap code path.

## `apply_patch.py`

A one-time migration script run on 2026-04-23 to fix the dashboard's
`replay_5s_of_car` implementation (the old negative-frame-offset path
was unreliable; switched to `replay_search_session_time`). The fix has
been in the codebase for weeks, so the patcher is no longer needed.
Kept for reference in case a similar surgical fix is useful later.
