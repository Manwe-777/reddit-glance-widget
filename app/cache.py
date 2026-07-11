"""Thread-safe in-memory cache with disk persistence.

The cache is the source of truth that the HTTP handlers read from — they never
touch Reddit directly. This is what lets Glance poll as often as it likes while
Reddit only gets hit by the background scheduler every couple of hours
(stale-while-revalidate).
"""
import json
import os
import tempfile
import threading
import time


class Cache:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        # subreddit -> {"data": <fetch result>, "error": str|None,
        #               "next_fetch": epoch, "last_error_at": epoch|None}
        self._store = {}
        self._load()

    # --- persistence ------------------------------------------------------
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self._store = json.load(fh)
        except (ValueError, OSError):
            self._store = {}

    def _flush_locked(self):
        if not self.path:
            return
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        # Atomic write so a crash mid-write can't corrupt the cache file.
        fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._store, fh)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # --- reads ------------------------------------------------------------
    def get(self, subreddit):
        with self._lock:
            entry = self._store.get(subreddit)
            return dict(entry) if entry else None

    def get_posts(self, subreddit):
        entry = self.get(subreddit)
        if entry and entry.get("data"):
            return entry["data"].get("posts", [])
        return []

    def all_entries(self):
        with self._lock:
            return {k: dict(v) for k, v in self._store.items()}

    def next_fetch(self, subreddit):
        entry = self.get(subreddit)
        return entry.get("next_fetch", 0) if entry else 0

    def has_data(self, subreddit):
        entry = self.get(subreddit)
        return bool(entry and entry.get("data"))

    # --- writes -----------------------------------------------------------
    def store_success(self, subreddit, data, next_fetch):
        with self._lock:
            self._store[subreddit] = {
                "data": data,
                "error": None,
                "last_error_at": None,
                "next_fetch": next_fetch,
                "updated_at": time.time(),
            }
            self._flush_locked()

    def store_error(self, subreddit, message, next_fetch):
        with self._lock:
            entry = self._store.get(subreddit, {})
            entry["error"] = message
            entry["last_error_at"] = time.time()
            entry["next_fetch"] = next_fetch
            # Keep any previously-good data so we can serve stale content.
            self._store[subreddit] = entry
            self._flush_locked()

    def prune(self, keep_subreddits):
        """Drop cache entries for subreddits no longer configured."""
        keep = set(keep_subreddits)
        with self._lock:
            removed = [k for k in self._store if k not in keep]
            for k in removed:
                del self._store[k]
            if removed:
                self._flush_locked()
            return removed
