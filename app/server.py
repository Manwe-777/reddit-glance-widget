"""HTTP server for the Reddit -> Glance widget.

Endpoints:
  GET /                     Glance extension HTML (all configured subreddits).
  GET /widget               Same as / (explicit alias).
  GET /api/posts            Aggregated JSON for all configured subreddits.
  GET /api/posts/<sub>      JSON for a single subreddit.
  GET /api/status           Health + per-subreddit fetch state (JSON).
  GET /healthz              Liveness probe.

Query parameters (on / and /widget), also settable via a Glance widget's
`parameters:` block:
  subreddits=a,b,c   Override which subreddits to show (must be a subset of
                     configured ones; unknown ones are ignored).
  limit=N            Cap posts shown.
  collapse=N         Collapse the list after N items (0 = no collapse).
  standalone=1       Return a full dark HTML page instead of a fragment.
  title=Text         Override the widget title.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
import render
from cache import Cache
from scheduler import Scheduler

CACHE = Cache(config.CACHE_FILE)
SCHED = Scheduler(CACHE)

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}


def _sort_posts(posts):
    # Fall back to recency if a listing came back without any scores.
    has_scores = any(p.get("score", 0) for p in posts)
    if config.SORT == "new" or not has_scores:
        return sorted(posts, key=lambda p: p.get("created_utc", 0), reverse=True)
    return sorted(posts, key=lambda p: p.get("score", 0), reverse=True)


def _resolve_subreddits(qs):
    requested = qs.get("subreddits", [None])[0]
    if not requested:
        return list(config.SUBREDDITS)
    wanted = [s.strip().lstrip("r/") for s in requested.split(",") if s.strip()]
    configured = set(config.SUBREDDITS)
    # Only serve subreddits we're actually scheduled to fetch, so a query
    # param can't turn this into an open Reddit proxy.
    return [s for s in wanted if s in configured] or list(config.SUBREDDITS)


def _collect(subreddits, limit=None):
    merged = []
    for sub in subreddits:
        merged.extend(CACHE.get_posts(sub))
    merged = _sort_posts(merged)
    if limit:
        merged = merged[:limit]
    return merged


class Handler(BaseHTTPRequestHandler):
    server_version = "RedditGlanceWidget/1.0"

    def log_message(self, fmt, *args):  # quieter, single-line logs
        print("[http] %s - %s" % (self.address_string(), fmt % args), flush=True)

    # --- response helpers -------------------------------------------------
    def _send(self, code, body, content_type, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        extra = extra_headers or {}
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if not any(k.lower() == "cache-control" for k in extra):
            self.send_header("Cache-Control", "public, max-age=60")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    # --- routing ----------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        # API + health first (these never touch the filesystem).
        if path == "/api/posts":
            return self._handle_api_all(qs)
        if path.startswith("/api/posts/"):
            return self._handle_api_one(path[len("/api/posts/"):])
        if path == "/api/status":
            return self._handle_status()
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        # Glance `extension` widget fragment (alternative to the iframe app).
        if path == "/extension" or path == "/widget":
            return self._handle_extension(qs)
        # Otherwise serve the iframe web app / static assets from public/.
        return self._handle_static(path)

    # --- handlers ---------------------------------------------------------
    def _handle_static(self, path):
        rel = path.lstrip("/") or "index.html"
        # Resolve against PUBLIC_DIR and refuse anything that escapes it.
        full = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not full.startswith(PUBLIC_DIR + os.sep) and full != PUBLIC_DIR:
            return self._json(404, {"error": "not found", "path": path})
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            return self._json(404, {"error": "not found", "path": path})
        ext = os.path.splitext(full)[1].lower()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._json(500, {"error": "read failed", "path": path})
        # index.html must not be cached hard, so theme/config changes show up.
        cache = "no-cache" if ext == ".html" else "public, max-age=300"
        return self._send(200, body, ctype, extra_headers={"Cache-Control": cache})

    def _handle_extension(self, qs):
        subs = _resolve_subreddits(qs)
        try:
            limit = int(qs.get("limit", [0])[0]) or None
        except ValueError:
            limit = None
        try:
            collapse = int(qs.get("collapse", [5])[0])
        except ValueError:
            collapse = 5
        standalone = qs.get("standalone", ["0"])[0] in ("1", "true", "yes")

        posts = _collect(subs, limit=limit)
        show_sub = len(subs) > 1
        if len(subs) == 1:
            default_title = "r/%s" % subs[0]
        else:
            default_title = "Reddit"
        title = qs.get("title", [default_title])[0]

        if standalone:
            html_body = render.render_standalone(posts, title, show_subreddit=show_sub)
            return self._send(200, html_body, "text/html; charset=utf-8")

        fragment = render.render_widget_html(posts, show_subreddit=show_sub, collapse_after=collapse)
        # Headers Glance's extension widget understands.
        return self._send(
            200, fragment, "text/html; charset=utf-8",
            extra_headers={"Widget-Content-Type": "html", "Widget-Title": title},
        )

    def _handle_api_all(self, qs):
        subs = _resolve_subreddits(qs)
        try:
            limit = int(qs.get("limit", [0])[0]) or None
        except ValueError:
            limit = None
        meta = {}
        for sub in subs:
            entry = CACHE.get(sub) or {}
            meta[sub] = {
                "updated_at": entry.get("updated_at"),
                "error": entry.get("error"),
                "count": len(CACHE.get_posts(sub)),
            }
        self._json(200, {
            "generated_at": time.time(),
            "sort": config.SORT,
            "subreddits": subs,
            "meta": meta,
            "posts": _collect(subs, limit=limit),
        })

    def _handle_api_one(self, sub):
        sub = sub.strip().lstrip("r/")
        if sub not in set(config.SUBREDDITS):
            return self._json(404, {"error": "subreddit not configured", "subreddit": sub})
        entry = CACHE.get(sub) or {}
        self._json(200, {
            "subreddit": sub,
            "updated_at": entry.get("updated_at"),
            "next_fetch": entry.get("next_fetch"),
            "error": entry.get("error"),
            "posts": CACHE.get_posts(sub),
        })

    def _handle_status(self):
        now = time.time()
        subs = {}
        for sub in config.SUBREDDITS:
            entry = CACHE.get(sub) or {}
            subs[sub] = {
                "has_data": bool(entry.get("data")),
                "post_count": len(CACHE.get_posts(sub)),
                "updated_at": entry.get("updated_at"),
                "age_seconds": (now - entry["updated_at"]) if entry.get("updated_at") else None,
                "next_fetch_in": (entry["next_fetch"] - now) if entry.get("next_fetch") else None,
                "error": entry.get("error"),
                "last_error_at": entry.get("last_error_at"),
            }
        self._json(200, {
            "now": now,
            "config": config.summary(),
            "scheduler": SCHED.status(),
            "subreddits": subs,
        })


def main():
    print("[server] config: %s" % json.dumps(config.summary()), flush=True)
    SCHED.start()
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print("[server] listening on http://%s:%d" % (config.HOST, config.PORT), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SCHED.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
