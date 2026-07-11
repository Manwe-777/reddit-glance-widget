"""Background refresh loop.

Design goals:
  * One request per subreddit roughly every REFRESH_INTERVAL (default 2h).
  * Never burst: enforce a jittered minimum gap between ANY two outbound
    Reddit requests, so adding more subreddits spreads them out in time
    rather than firing them together.
  * Stagger each subreddit's schedule with random jitter so they don't all
    come due at the same instant.
  * On error (403/429/etc.) back off but keep serving the last good data.
  * Survive restarts: schedules live in the cache file, so a redeploy resumes
    instead of refetching everything at once.
"""
import random
import threading
import time

import config
import reddit


class Scheduler(threading.Thread):
    def __init__(self, cache):
        super().__init__(daemon=True)
        self.cache = cache
        self._stop = threading.Event()
        self._last_request_at = 0.0
        self._next_allowed_request = 0.0

    def stop(self):
        self._stop.set()

    # --- scheduling helpers ----------------------------------------------
    def _schedule_after_success(self):
        jitter = random.randint(-config.REFRESH_JITTER, config.REFRESH_JITTER)
        return time.time() + max(60, config.REFRESH_INTERVAL + jitter)

    def _schedule_after_error(self):
        # Shorter backoff than a normal interval so we recover, but jittered.
        jitter = random.randint(0, config.REFRESH_JITTER)
        return time.time() + config.ERROR_BACKOFF + jitter

    def _due_subreddits(self, now):
        """Return configured subreddits whose next_fetch has passed, oldest
        (most overdue) first.

        A never-attempted subreddit has next_fetch == 0, so it's due on the
        first tick. After a fetch (success OR error) next_fetch is pushed into
        the future, so this naturally respects both the normal interval and the
        error backoff — crucially, a subreddit that keeps failing is NOT
        retried every tick just because it has no cached data yet."""
        due = []
        for sub in config.SUBREDDITS:
            nxt = self.cache.next_fetch(sub)
            if nxt <= now:
                due.append((nxt, sub))
        due.sort(key=lambda t: t[0])
        return [sub for _, sub in due]

    def _fetch_one(self, sub):
        try:
            data = reddit.fetch(sub)
            self.cache.store_success(sub, data, self._schedule_after_success())
            print("[scheduler] fetched r/%s (%d posts)" % (sub, len(data["posts"])), flush=True)
        except reddit.FetchError as e:
            self.cache.store_error(sub, str(e), self._schedule_after_error())
            print("[scheduler] error r/%s: %s (backing off)" % (sub, e), flush=True)

    # --- main loop --------------------------------------------------------
    def run(self):
        # Reconcile cache with current config on boot.
        removed = self.cache.prune(config.SUBREDDITS)
        if removed:
            print("[scheduler] pruned removed subreddits: %s" % ", ".join(removed), flush=True)
        print("[scheduler] started; %d subreddits, interval=%ss" % (
            len(config.SUBREDDITS), config.REFRESH_INTERVAL), flush=True)

        while not self._stop.is_set():
            now = time.time()
            if now >= self._next_allowed_request:
                due = self._due_subreddits(now)
                if due:
                    # Fetch exactly one due subreddit per allowed slot; the gap
                    # below spaces the next one out. This is what keeps us from
                    # bursting when many subreddits are due at once (e.g. boot).
                    self._fetch_one(due[0])
                    gap = random.randint(config.MIN_REQUEST_GAP, config.MAX_REQUEST_GAP)
                    self._last_request_at = time.time()
                    self._next_allowed_request = self._last_request_at + gap
            # Sleep in short slices so stop() is responsive.
            self._stop.wait(min(config.SCHEDULER_TICK, 5))

    # --- introspection ----------------------------------------------------
    def status(self):
        return {
            "last_request_at": self._last_request_at,
            "next_allowed_request": self._next_allowed_request,
        }
