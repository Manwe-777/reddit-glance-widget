"""Fetches and normalizes posts from Reddit's public JSON endpoints.

We deliberately use the plain web endpoints (www.reddit.com/r/<sub>/<sort>.json)
with browser-like headers + an optional session cookie rather than the OAuth API,
because the goal is to look like an ordinary logged-in browser making the
occasional request. Combined with the low request rate in the scheduler, this
keeps us under Reddit's throttle radar.
"""
import gzip
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config


class FetchError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _build_url(subreddit):
    sort = config.SORT if config.SORT in ("hot", "new", "top", "rising") else "hot"
    url = "%s/r/%s/%s.json?limit=%d&raw_json=1" % (
        config.REDDIT_BASE.rstrip("/"),
        urllib.parse.quote(subreddit),
        sort,
        max(1, config.POSTS_PER_SUB),
    )
    if sort == "top":
        url += "&t=%s" % config.TOP_PERIOD
    return url


def _headers():
    """Headers that mirror a real browser navigation to reddit."""
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "%s/" % config.REDDIT_BASE.rstrip("/"),
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "DNT": "1",
    }
    if config.REDDIT_COOKIE:
        h["Cookie"] = config.REDDIT_COOKIE
    return h


def _read_body(resp):
    raw = resp.read()
    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return raw.decode("utf-8", errors="replace")


def _thumbnail(data):
    thumb = data.get("thumbnail") or ""
    if thumb.startswith("http"):
        return thumb
    # Fall back to a preview image if available.
    try:
        images = data["preview"]["images"]
        if images:
            src = images[0]["source"]["url"]
            return src.replace("&amp;", "&")
    except (KeyError, IndexError, TypeError):
        pass
    return ""


def _normalize(child, subreddit):
    d = child.get("data", {})
    permalink = d.get("permalink", "")
    return {
        "id": d.get("id", ""),
        "title": d.get("title", "").strip(),
        "subreddit": d.get("subreddit", subreddit),
        "author": d.get("author", ""),
        "score": int(d.get("score", 0) or 0),
        "comments": int(d.get("num_comments", 0) or 0),
        "created_utc": float(d.get("created_utc", 0) or 0),
        "permalink": "https://www.reddit.com" + permalink if permalink else d.get("url", ""),
        "url": d.get("url", ""),
        "domain": d.get("domain", ""),
        "flair": (d.get("link_flair_text") or "").strip(),
        "thumbnail": _thumbnail(d),
        "is_self": bool(d.get("is_self", False)),
        "over_18": bool(d.get("over_18", False)),
        "stickied": bool(d.get("stickied", False)),
    }


def fetch(subreddit):
    """Fetch and normalize posts for one subreddit. Raises FetchError on failure."""
    url = _build_url(subreddit)
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
            body = _read_body(resp)
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %s for r/%s" % (e.code, subreddit), status=e.code)
    except urllib.error.URLError as e:
        raise FetchError("network error for r/%s: %s" % (subreddit, e.reason))
    except Exception as e:  # noqa: BLE001 - surface anything as a fetch error
        raise FetchError("unexpected error for r/%s: %s" % (subreddit, e))

    try:
        payload = json.loads(body)
        children = payload["data"]["children"]
    except (ValueError, KeyError, TypeError):
        # Reddit sometimes returns an HTML block/login page on soft-blocks.
        raise FetchError("unparseable response for r/%s (possible block)" % subreddit)

    posts = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        post = _normalize(child, subreddit)
        if post["over_18"] and not config.SHOW_NSFW:
            continue
        posts.append(post)

    return {
        "subreddit": subreddit,
        "sort": config.SORT,
        "fetched_at": time.time(),
        "posts": posts[: config.POSTS_PER_SUB],
    }
