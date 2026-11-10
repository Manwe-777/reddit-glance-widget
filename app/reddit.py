"""Fetches and normalizes posts from Reddit's public web endpoints.

We use the plain web endpoints (www.reddit.com/r/<sub>/<sort>.json) with
browser-like headers + an optional session cookie rather than the OAuth API,
because the goal is to look like an ordinary logged-in browser making the
occasional request. Combined with the low request rate in the scheduler, this
keeps us under Reddit's throttle radar.

Some networks get a hard "You've been blocked by network security" 403 on the
.json endpoints (and on oauth.reddit.com) while the Atom/RSS feeds keep working.
When that happens we transparently fall back to <sub>/<sort>.rss, which has no
score/comment counts but carries titles, links, authors, timestamps and
thumbnails.
"""
import gzip
import html
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

import config

_ATOM_NS = "http://www.w3.org/2005/Atom"
_MEDIA_NS = "http://search.yahoo.com/mrss/"

# Reddit blocks the .json endpoints from some networks ("You've been blocked by
# network security") while still serving the Atom/RSS feeds. Once we see that
# block, stop trying JSON for the rest of this process and go straight to RSS.
_json_blocked = False


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


def _rss_url(subreddit):
    sort = config.SORT if config.SORT in ("hot", "new", "top", "rising") else "hot"
    url = "%s/r/%s/%s.rss?limit=%d" % (
        config.REDDIT_BASE.rstrip("/"),
        urllib.parse.quote(subreddit),
        sort,
        max(1, config.POSTS_PER_SUB),
    )
    if sort == "top":
        url += "&t=%s" % config.TOP_PERIOD
    return url


def _headers(accept="application/json, text/plain, */*"):
    """Headers that mirror a real browser navigation to reddit."""
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": accept,
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
        "source": "json",
    }


def _parse_iso(value):
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _extract_link(content):
    """Pull the external target out of an Atom entry's HTML content.

    Reddit's RSS always links to the comments page in <link>, and for link
    posts the actual target appears as an <a href="...">[link]</a> in the
    content HTML. Self posts point that anchor back at the permalink.
    """
    if not content:
        return ""
    match = re.search(r'<a href="([^"]+)">\[link\]', html.unescape(content))
    return match.group(1) if match else ""


def _normalize_rss(entry, subreddit, ns):
    entry_id = entry.findtext("a:id", default="", namespaces=ns) or ""
    post_id = entry_id[3:] if entry_id.startswith("t3_") else entry_id
    link = entry.find("a:link", ns)
    permalink = link.get("href", "") if link is not None else ""
    author = (entry.findtext("a:author/a:name", default="", namespaces=ns) or "").strip()
    if author.startswith("/u/"):
        author = author[3:]
    content = entry.findtext("a:content", default="", namespaces=ns) or ""
    external = _extract_link(content)
    host = urllib.parse.urlparse(external).netloc.lower() if external else ""
    is_self = not external or external == permalink or host == "reddit.com" or host.endswith(".reddit.com")
    thumb = entry.find("media:thumbnail", ns)
    category = entry.find("a:category", ns)
    sub = (category.get("term") if category is not None else "") or subreddit
    if sub == "multi":
        sub = subreddit
    return {
        "id": post_id,
        "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
        "subreddit": sub,
        "author": author,
        # Atom feeds don't carry score or comment counts.
        "score": 0,
        "comments": 0,
        "created_utc": _parse_iso(entry.findtext("a:updated", default="", namespaces=ns)),
        "permalink": permalink,
        "url": permalink if is_self else external,
        "domain": "" if is_self else urllib.parse.urlparse(external).netloc,
        "flair": "",
        "thumbnail": thumb.get("url", "") if thumb is not None else "",
        "is_self": is_self,
        "over_18": False,
        "stickied": False,
        "source": "rss",
    }


def _get(url, subreddit, accept):
    req = urllib.request.Request(url, headers=_headers(accept), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
            return _read_body(resp)
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %s for r/%s" % (e.code, subreddit), status=e.code)
    except urllib.error.URLError as e:
        raise FetchError("network error for r/%s: %s" % (subreddit, e.reason))
    except Exception as e:  # noqa: BLE001 - surface anything as a fetch error
        raise FetchError("unexpected error for r/%s: %s" % (subreddit, e))


def _result(subreddit, posts):
    return {
        "subreddit": subreddit,
        "sort": config.SORT,
        "fetched_at": time.time(),
        "posts": posts[: config.POSTS_PER_SUB],
    }


def _fetch_json(subreddit):
    body = _get(_build_url(subreddit), subreddit, "application/json, text/plain, */*")
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
    return _result(subreddit, posts)


def _fetch_rss(subreddit):
    body = _get(
        _rss_url(subreddit),
        subreddit,
        "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    )
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FetchError("unparseable RSS for r/%s (possible block)" % subreddit)
    ns = {"a": _ATOM_NS, "media": _MEDIA_NS}
    posts = []
    for entry in root.findall("a:entry", ns):
        post = _normalize_rss(entry, subreddit, ns)
        if post["title"]:
            posts.append(post)
    if not posts:
        raise FetchError("empty RSS for r/%s (possible block)" % subreddit)
    return _result(subreddit, posts)


def fetch(subreddit):
    """Fetch and normalize posts for one subreddit. Raises FetchError on failure.

    Tries the JSON endpoint first, but falls back to the Atom feed when Reddit
    blocks JSON from this network. After the first 403 we skip JSON for the
    lifetime of the process so we don't waste a request per subreddit.
    """
    global _json_blocked
    if not _json_blocked:
        try:
            return _fetch_json(subreddit)
        except FetchError as e:
            if e.status == 403 or "unparseable" in str(e):
                _json_blocked = True
                print("[reddit] JSON endpoint blocked; using RSS from now on", flush=True)
            else:
                raise
    return _fetch_rss(subreddit)
