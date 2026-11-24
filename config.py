"""Configuration for the Luma Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT         port the API listens on (default 8000)
    LUMA_PROXY   proxy URL for every request, e.g. http://user:pass@host:port
                 (default: none). No proxy needed: Luma answers your own
                 connection.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

LUMA_PROXY = os.environ.get("LUMA_PROXY") or None


# ---- what the code reads off config: defaults for a direct run ----
LUMA_CLIENT_VERSION = '2026-11-21T03:53:06Z|db79afe6fec3'
LUMA_EXIT = LUMA_PROXY      # the exit every session leaves through: your proxy, or direct
LUMA_KEYWORD_REACH_KM = 200
LUMA_MAX_PAGE = 60
LUMA_SHOW_UNSHOWN_FIELDS = False
LUMA_TIMEZONE = 'America/Chicago'
MAX_RETRIES = 3
RETRY_BACKOFF = 2
