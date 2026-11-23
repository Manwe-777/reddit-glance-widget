"""Configuration loaded from environment variables.

Everything is overridable via env so the container stays portable and no code
edits are needed to move it out of the workspace. See .env.example.
"""
import os


def _get(name, default):
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def _get_int(name, default):
    try:
        return int(_get(name, default))
    except (TypeError, ValueError):
        return default


def _read_cookie():
    """Cookie string can be supplied inline (REDDIT_COOKIE) or via a mounted
    file (REDDIT_COOKIE_FILE). A file is cleaner for long cookie blobs and keeps
    secrets out of `docker inspect`."""
    path = os.environ.get("REDDIT_COOKIE_FILE")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return _get("REDDIT_COOKIE", "").strip()


# --- Server ---------------------------------------------------------------
PORT = _get_int("PORT", 8080)
HOST = _get("HOST", "0.0.0.0")

# --- Content --------------------------------------------------------------
# Comma or whitespace separated list of subreddits.
_raw_subs = _get("SUBREDDITS", "homelab,selfhosted")
SUBREDDITS = [s.strip().lstrip("r/").strip() for s in _raw_subs.replace("\n", ",").split(",") if s.strip()]

SORT = _get("SORT", "hot").lower()            # hot | new | top | rising
TOP_PERIOD = _get("TOP_PERIOD", "day").lower()  # hour|day|week|month|year|all
POSTS_PER_SUB = _get_int("POSTS_PER_SUB", 15)
SHOW_NSFW = _get("SHOW_NSFW", "false").lower() in ("1", "true", "yes")

# --- Refresh / throttle behaviour ----------------------------------------
# Base seconds between refetches of a single subreddit (default 2h).
REFRESH_INTERVAL = _get_int("REFRESH_INTERVAL", 7200)
# Random +/- jitter added to each subreddit's schedule so they don't all
# expire at the same instant (looks less bot-like, spreads load).
REFRESH_JITTER = _get_int("REFRESH_JITTER", 900)
# Minimum spacing enforced between ANY two outbound Reddit requests, so even
# with many subreddits we never burst. Also jittered.
MIN_REQUEST_GAP = _get_int("MIN_REQUEST_GAP", 20)
MAX_REQUEST_GAP = _get_int("MAX_REQUEST_GAP", 50)
# How often the scheduler loop wakes up to check for due subreddits.
SCHEDULER_TICK = _get_int("SCHEDULER_TICK", 15)
# Backoff (seconds) after a 403/429/5xx before that subreddit is retried.
ERROR_BACKOFF = _get_int("ERROR_BACKOFF", 900)

# --- Realism (anti-throttle) ---------------------------------------------
USER_AGENT = _get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
REDDIT_COOKIE = _read_cookie()
REDDIT_BASE = _get("REDDIT_BASE", "https://old.reddit.com")
REQUEST_TIMEOUT = _get_int("REQUEST_TIMEOUT", 20)

# --- Persistence ----------------------------------------------------------
# Cache survives restarts so a redeploy doesn't trigger a fresh burst of
# requests and "refetch only when necessary" holds across container life.
CACHE_FILE = _get("CACHE_FILE", "/data/cache.json")


def summary():
    return {
        "subreddits": SUBREDDITS,
        "sort": SORT,
        "top_period": TOP_PERIOD,
        "posts_per_sub": POSTS_PER_SUB,
        "refresh_interval": REFRESH_INTERVAL,
        "refresh_jitter": REFRESH_JITTER,
        "min_request_gap": MIN_REQUEST_GAP,
        "max_request_gap": MAX_REQUEST_GAP,
        "cookie_present": bool(REDDIT_COOKIE),
        "cache_file": CACHE_FILE,
    }
