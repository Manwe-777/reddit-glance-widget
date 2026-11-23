"""Fetches and normalizes posts by scraping old.reddit.com HTML listings.

Reddit's JSON endpoints (/r/<sub>/<sort>.json, and oauth.reddit.com) are
commonly blocked from datacenter / VPS egress IPs with a 403 "You've been
blocked by network security" page, while the classic old.reddit.com HTML
listing keeps working. So instead of the JSON API (with an Atom/RSS fallback),
we fetch the old.reddit listing page for each subreddit and read the
machine-readable data-* attributes Reddit puts on every `div.thing`.

The old.reddit markup carries everything we need without a parser dependency:
data-fullname, data-author, data-subreddit, data-score, data-comments-count,
data-timestamp, data-permalink, data-url, data-domain, data-nsfw. The title,
link flair and thumbnail are read from the post's inner elements.
"""
import gzip
import html
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import config

# One `<div class=" thing ...` per listing item, with the data-* attributes we
# need on the opening tag. Attributes never contain ">", so [^>]* is safe.
_THING_RE = re.compile(
    r'<div class=" thing[^"]*"[^>]*data-fullname="t3_([a-z0-9]+)"[^>]*>',
    re.S,
)
_ATTR_RE = re.compile(r'\bdata-([a-z-]+)="([^"]*)"')
_TITLE_RE = re.compile(r'<a[^>]*class="title[^"]*"[^>]*>(.*?)</a>', re.S)
_FLAIR_RE = re.compile(r'<span class="linkflairlabel[^"]*"[^>]*>(.*?)</span>', re.S)
_THUMB_ANCHOR_RE = re.compile(
    r'<a[^>]*class="[^"]*thumbnail[^"]*"[^>]*>(.*?)</a>', re.S
)
_IMG_SRC_RE = re.compile(r'<img[^>]*\bsrc="([^"]+)"', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


class FetchError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _build_url(subreddit):
    sort = config.SORT if config.SORT in ("hot", "new", "top", "rising") else "hot"
    url = "%s/r/%s/%s/" % (
        config.REDDIT_BASE.rstrip("/"),
        urllib.parse.quote(subreddit),
        sort,
    )
    if sort == "top":
        url += "?t=%s" % config.TOP_PERIOD
    return url


def _headers():
    """Headers that mirror a real browser navigation to old.reddit.com."""
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "%s/" % config.REDDIT_BASE.rstrip("/"),
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1",
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


def _get(url, subreddit):
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
            return _read_body(resp)
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %s for r/%s" % (e.code, subreddit), status=e.code)
    except urllib.error.URLError as e:
        raise FetchError("network error for r/%s: %s" % (subreddit, e.reason))
    except Exception as e:  # noqa: BLE001 - surface anything as a fetch error
        raise FetchError("unexpected error for r/%s: %s" % (subreddit, e))


def _clean_text(value):
    """Strip tags, collapse whitespace and unescape HTML entities."""
    text = _TAG_RE.sub("", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _attrs(tag):
    return {name: value for name, value in _ATTR_RE.findall(tag)}


def _thumbnail(block):
    anchor = _THUMB_ANCHOR_RE.search(block)
    scope = anchor.group(1) if anchor else block
    match = _IMG_SRC_RE.search(scope)
    if not match:
        return ""
    src = html.unescape(match.group(1)).strip()
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = config.REDDIT_BASE.rstrip("/") + src
    return src


def _absolute(path):
    if not path:
        return ""
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("/"):
        return config.REDDIT_BASE.rstrip("/") + path
    return path


def _normalize(block, tag, subreddit):
    attrs = _attrs(tag)
    domain = attrs.get("domain", "")
    if domain in ("old.reddit.com", "www.reddit.com", "reddit.com"):
        domain = "reddit.com"
    permalink = _absolute(attrs.get("permalink", ""))
    url = _absolute(attrs.get("url", ""))
    is_self = domain.startswith("self.") or " self " in tag
    if is_self or not url:
        url = permalink
    title_match = _TITLE_RE.search(block)
    flair_match = _FLAIR_RE.search(block)
    return {
        "id": attrs.get("fullname", "")[3:],
        "title": _clean_text(title_match.group(1)) if title_match else "",
        "subreddit": attrs.get("subreddit") or subreddit,
        "author": attrs.get("author", ""),
        "score": int(attrs.get("score", 0) or 0),
        "comments": int(attrs.get("comments-count", 0) or 0),
        "created_utc": float(attrs.get("timestamp", 0) or 0) / 1000.0,
        "permalink": permalink,
        "url": url,
        "domain": domain,
        "flair": _clean_text(flair_match.group(1)) if flair_match else "",
        "thumbnail": _thumbnail(block),
        "is_self": is_self,
        "over_18": attrs.get("nsfw", "").lower() == "true",
        "stickied": "stickied" in tag,
        "source": "html",
    }


def _result(subreddit, posts):
    return {
        "subreddit": subreddit,
        "sort": config.SORT,
        "fetched_at": time.time(),
        "posts": posts[: config.POSTS_PER_SUB],
    }


def fetch(subreddit):
    """Fetch and normalize posts for one subreddit. Raises FetchError on failure."""
    body = _get(_build_url(subreddit), subreddit)
    if "blocked by network security" in body or "reason=lor2" in body:
        raise FetchError("blocked for r/%s (network security / login wall)" % subreddit, status=403)

    # Drop <script> blocks first so report/dialog templates (which also contain
    # `thing` divs) don't get mistaken for posts.
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)

    matches = list(_THING_RE.finditer(body))
    posts = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[start:end]
        tag = match.group(0)
        if 'data-promoted="true"' in tag:
            continue
        post = _normalize(block, tag, subreddit)
        if not post["title"]:
            continue
        if post["over_18"] and not config.SHOW_NSFW:
            continue
        posts.append(post)

    if not posts:
        raise FetchError("empty listing for r/%s (possible block)" % subreddit)
    return _result(subreddit, posts)
