"""Render cached posts as HTML for Glance's `extension` widget.

The markup uses Glance's own CSS classes (list, list-horizontal-text,
collapsible-container, size-h4, color-*) so it looks native inside the
dashboard. A `?standalone=1` mode wraps it in a minimal dark page for direct
viewing / testing in a browser.
"""
import html
import time

import config


def _escape(s):
    return html.escape(s or "", quote=True)


def _time_ago(created_utc):
    if not created_utc:
        return ""
    delta = max(0, int(time.time() - created_utc))
    mins = delta // 60
    if mins < 1:
        return "just now"
    if mins < 60:
        return "%dm ago" % mins
    hours = mins // 60
    if hours < 24:
        return "%dh ago" % hours
    days = hours // 24
    if days < 30:
        return "%dd ago" % days
    months = days // 30
    if months < 12:
        return "%dmo ago" % months
    return "%dy ago" % (days // 365)


def _fmt_score(n):
    if n >= 10000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


def _post_li(post, show_subreddit):
    title = _escape(post["title"])
    link = _escape(post["permalink"])
    meta = []
    if post.get("source") != "rss":
        meta.append('<li>%s</li>' % _fmt_score(post["score"]))
        meta.append('<li>%d comments</li>' % post["comments"])
    if show_subreddit:
        meta.append('<li><a href="%s/r/%s" target="_blank" rel="noreferrer">r/%s</a></li>'
                    % (_escape(config.REDDIT_BASE.rstrip("/")), _escape(post["subreddit"]), _escape(post["subreddit"])))
    if post.get("flair"):
        meta.append('<li>%s</li>' % _escape(post["flair"]))
    when = _time_ago(post["created_utc"])
    if when:
        meta.append('<li>%s</li>' % when)

    domain = ""
    if not post.get("is_self") and post.get("domain"):
        domain = ' <span class="color-subdue size-h6">(%s)</span>' % _escape(post["domain"])

    return (
        '<li>'
        '<a class="size-h4 color-primary-if-not-visited" href="%s" target="_blank" rel="noreferrer">%s</a>%s'
        '<ul class="list-horizontal-text flex-nowrap text-truncate">%s</ul>'
        '</li>'
    ) % (link, title, domain, "".join(meta))


def render_widget_html(posts, show_subreddit=True, collapse_after=5, empty_msg=None):
    if not posts:
        msg = empty_msg or "No posts cached yet — fetching in the background."
        return '<p class="color-subdue">%s</p>' % _escape(msg)
    items = "".join(_post_li(p, show_subreddit) for p in posts)
    collapse_attr = ' data-collapse-after="%d"' % collapse_after if collapse_after else ""
    return (
        '<ul class="list list-gap-14 collapsible-container"%s>%s</ul>'
    ) % (collapse_attr, items)


_STANDALONE_CSS = """
:root{--bg:#151519;--card:#1c1c22;--text:#d6d6da;--sub:#87878f;--accent:#f26f4c;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;}
h1{font-size:15px;color:var(--sub);text-transform:uppercase;letter-spacing:.05em;margin:0 0 16px;}
.list{list-style:none;margin:0;padding:0;}
.list-gap-14>li{margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid #26262c;}
.list-gap-14>li:last-child{border-bottom:none;}
a{color:var(--text);text-decoration:none;}
a:hover{color:var(--accent);}
a:visited{color:var(--sub);}
.size-h4{font-size:14px;font-weight:500;display:block;}
.color-primary-if-not-visited{color:var(--text);}
.color-primary-if-not-visited:visited{color:var(--sub);}
.list-horizontal-text{list-style:none;display:flex;gap:6px;padding:4px 0 0;margin:0;flex-wrap:wrap;}
.list-horizontal-text>li{color:var(--sub);font-size:12px;}
.list-horizontal-text>li:not(:last-child)::after{content:'·';margin-left:6px;color:#3a3a42;}
.color-subdue,.color-subdue a{color:var(--sub);}
.size-h6{font-size:11px;}
"""


def render_standalone(posts, title, show_subreddit=True):
    body = render_widget_html(posts, show_subreddit=show_subreddit, collapse_after=0)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s</title><style>%s</style></head>"
        "<body><h1>%s</h1>%s</body></html>"
    ) % (_escape(title), _STANDALONE_CSS, _escape(title), body)
