# Reddit → Glance widget

A tiny, dependency-free HTTP server that fetches Reddit posts **once every couple
of hours** using realistic browser-like requests (your session cookie + matching
User-Agent), caches them, and serves them to [Glance](https://github.com/glanceapp/glance)
instantly on every dashboard refresh.

It exists because Glance's built-in Reddit widget gets `403`-blocked from
datacenter / VPS IPs. This server sidesteps that by:

- **Scraping old.reddit.com HTML listings.** Reddit commonly 403s the `.json`
  endpoints (and `oauth.reddit.com`) from datacenter / VPS egress IPs, while the
  classic `old.reddit.com/r/<sub>/<sort>/` HTML pages keep working. Each post's
  `data-*` attributes carry the score, comment count, author, timestamp, etc.
- **Looking like a real logged-in browser.** You supply your Reddit session
  cookie and a matching User-Agent, so requests look ordinary.
- **Almost never hitting Reddit.** A background scheduler fetches each subreddit
  ~once every 2h (configurable), with jitter and enforced spacing between
  requests so it never bursts. Glance reads from the local cache, so you can set
  Glance to refresh as often as you like without touching Reddit.
- **Serving stale-while-revalidate.** If a fetch fails (e.g. a transient 403),
  the last good posts keep showing and the subreddit is retried after a backoff.
- **Surviving restarts.** The cache is persisted to disk, so redeploys don't
  trigger a fresh burst of requests.
- **Matching your dashboard.** The widget is served as an iframe web app that
  uses Glance's own CSS-variable color system and syncs to the active Glance
  theme (light/dark/custom presets) over `postMessage` — same mechanism as the
  homelab-bookmarks widget.

Stdlib-only Python — no `pip install`, no framework. The whole thing is a handful
of files under `app/` and a small Docker image, so it drops onto any host cleanly.

---

## Setup — the 5-minute path

Everything you need to get this running and into Glance. Ports assume Glance is
already on `8080`; the widget publishes on **`8091`** by default.

**1. Get the files onto your server** and enter the folder:

```bash
git clone <this-repo-url> reddit-glance-widget
cd reddit-glance-widget
cp .env.example .env
```

**2. Choose your subreddits** — edit `SUBREDDITS` in `.env`:

```bash
SUBREDDITS=homelab,selfhosted,DataHoarder
```

**3. Add your Reddit cookie** so requests look like a normal logged-in browser
(this is what avoids the `403`s). In a browser logged into Reddit:
DevTools (F12) → **Network** → reload reddit.com → click the top request →
**Request Headers** → copy the whole **`Cookie:`** value into `.env` as
`REDDIT_COOKIE=<value>` (one line, no `Cookie:` prefix). Then copy that request's
**`User-Agent`** into `USER_AGENT` in `.env`. See
[Cookies](#cookies-the-anti-throttle-bit) for detail.

> Use **your own** cookie + the matching User-Agent from the *same* browser.

**4. Start it:**

```bash
docker compose up -d --build
curl http://localhost:8091/healthz     # -> ok
```

Open `http://<your-server>:8091` in a browser to see the widget. The first fetch
lands within ~15s; until then it shows a "fetching in the background" note.

**5. Add it to Glance** — in your `glance.yml`:

```yaml
- type: iframe
  title: Reddit
  source: http://<your-server>:8091/   # or the docker service name, see below
  height: 460
```

**6. Enable theme sync** (once) so the widget follows your Glance theme. If you
already added this for the homelab-bookmarks widget, **skip it** — it covers all
iframes:

```yaml
document:
  head: |
    <script src="https://cdn.jsdelivr.net/gh/Manwe-777/homelab-bookmarks@main/glance-theme-broadcast.js"></script>
```

That's it. The rest of this README is reference detail.

> **Networking note.** If Glance and this widget run in the **same Docker
> network**, use the service name and the container's internal port in the
> iframe: `source: http://reddit-glance-widget:8080/`. From the host / LAN /
> Tailscale, use `http://<server>:8091/`. Change the published port with
> `WIDGET_PORT` in `.env`.

---

## Quick start (TL;DR)

```bash
cp .env.example .env          # edit SUBREDDITS, SORT, etc.
# put your cookie in reddit_cookie.txt (see "Cookies" below)
docker compose up -d --build
curl http://localhost:8091/healthz          # -> ok
curl http://localhost:8091/api/status       # per-subreddit fetch state
```

### Run without Docker (for quick testing)

```bash
SUBREDDITS="homelab,selfhosted" python3 app/server.py   # serves on :8080
```

---

## Wiring it into Glance

### Option A — `iframe` widget (recommended)

The server hosts a self-contained web app that uses Glance's own CSS variable
color system, so it inherits the active Glance theme — light/dark and any custom
preset — exactly like the [homelab-bookmarks](https://github.com/Manwe-777/homelab-bookmarks)
widget. Add it as an iframe:

```yaml
- type: iframe
  title: Reddit
  source: http://reddit-glance-widget:8080/
  height: 460
```

Use `http://reddit-glance-widget:8080` if Glance shares a Docker network with
this container (the service name), or `http://<host-ip>:8080` otherwise.

**Theme sync (one-time Glance setup).** So the iframe knows which theme Glance is
using, add the broadcast script to your `glance.yml` `head`. If you already added
it for the bookmarks widget, you're done — it broadcasts to *all* iframes,
including this one:

```yaml
document:
  head: |
    <script src="https://cdn.jsdelivr.net/gh/Manwe-777/homelab-bookmarks@main/glance-theme-broadcast.js"></script>
```

The widget caches the last theme in `localStorage`, so it renders in the right
scheme immediately on load (no flash) and updates live when you switch themes.

The gear (top-right on hover) offers display options — group by subreddit, max
posts, and which of the configured subreddits to show. These are display-only
and stored per-browser; they never change what the server fetches.

### Option B — `extension` widget (native Glance list, no iframe)

The server also renders a plain HTML fragment using Glance's own list classes and
sends the `Widget-Content-Type: html` header, at `/extension`:

```yaml
- type: extension
  title: Reddit
  url: http://reddit-glance-widget:8080/extension
  allow-potentially-dangerous-html: true
  cache: 10m        # how often Glance re-reads the LOCAL cache — safe to make short
  parameters:
    subreddits: homelab           # optional; subset of configured SUBREDDITS
    limit: 10
    collapse: 5                   # collapse list after N items (0 = off)
```

### Option C — `custom-api` widget (style it yourself in Glance)

The server also exposes clean JSON, so you can use Glance's templating:

```yaml
- type: custom-api
  title: r/homelab
  url: http://reddit-glance-widget:8080/api/posts/homelab
  cache: 10m
  template: |
    <ul class="list list-gap-10 collapsible-container" data-collapse-after="5">
      {{ range .JSON.Array "posts" }}
      <li>
        <a class="size-h4 color-primary-if-not-visited" target="_blank" href="{{ .String "permalink" }}">{{ .String "title" }}</a>
        <ul class="list-horizontal-text">
          <li>{{ .Int "score" }}</li>
          <li>{{ .Int "comments" }} comments</li>
        </ul>
      </li>
      {{ end }}
    </ul>
```

---

## Cookies (the anti-throttle bit)

To make requests that Reddit treats as an ordinary browsing session:

1. Log into Reddit in your browser (old.reddit or www).
2. Open DevTools → **Network** tab → refresh reddit.com → click the document
   request → **Request Headers** → copy the entire **`Cookie:`** value.
3. Put it in `.env` as `REDDIT_COOKIE=<value>` (one line, no `Cookie:` prefix).
4. Copy the **`User-Agent`** from the same request headers into `USER_AGENT` in
   `.env` — it should match the browser the cookie came from.

> Keep the cookie and UA from the **same** browser/session. A mismatched UA is a
> common reason sessions get flagged.

`.env` is git-ignored, so the cookie never lands in the repo. If you'd rather
keep it out of `.env` entirely, use the optional file mount documented in
`docker-compose.yml` (`REDDIT_COOKIE_FILE`) — it isn't baked into the image or
visible in `docker inspect`.

Cookies expire eventually; if you start seeing `403`s in `/api/status`, refresh
the cookie. Because requests are so infrequent (once per sub / couple hours), a
session cookie tends to last a long time.

> **`403` — "You've been blocked by network security".** This is a network-level
> block, not an expired cookie: Reddit refuses the `.json` endpoints (and
> `oauth.reddit.com`) for this egress IP. This is why the widget scrapes the
> `old.reddit.com` HTML listings instead — they are still served from blocked
> IPs. If old.reddit also starts returning the block page, a fresh cookie +
> matching User-Agent from a logged-in browser is the first thing to try. The
> default request gap of 75–100s keeps the request rate low; widen
> `MIN_REQUEST_GAP`/`MAX_REQUEST_GAP` if you see `429`s.

---

## Configuration reference

All via environment variables (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `SUBREDDITS` | `homelab,selfhosted` | Comma-separated list |
| `SORT` | `hot` | `hot` / `new` / `top` / `rising` |
| `TOP_PERIOD` | `day` | Used when `SORT=top` |
| `POSTS_PER_SUB` | `15` | Posts kept per subreddit |
| `SHOW_NSFW` | `false` | Include NSFW posts |
| `REFRESH_INTERVAL` | `7200` | Seconds between refetches of one sub (2h) |
| `REFRESH_JITTER` | `900` | ± random jitter on each sub's schedule |
| `MIN_REQUEST_GAP` / `MAX_REQUEST_GAP` | `75` / `100` | Enforced spacing between any two Reddit requests |
| `ERROR_BACKOFF` | `900` | Backoff after a failed fetch |
| `USER_AGENT` | Chrome UA | Should match your cookie's browser |
| `REDDIT_BASE` | `https://old.reddit.com` | Site whose HTML listings are scraped |
| `REDDIT_COOKIE` / `REDDIT_COOKIE_FILE` | — | Session cookie (inline or file) |
| `PORT` | `8080` | Listen port |
| `CACHE_FILE` | `/data/cache.json` | Persistent cache path |

### How the throttle-avoidance actually works

- Each subreddit gets a `next_fetch` timestamp = `now + REFRESH_INTERVAL ± jitter`.
- The scheduler wakes every ~15s and fetches **at most one** overdue subreddit,
  then refuses to make another request for `MIN..MAX_REQUEST_GAP` seconds.
- Net effect: with N subreddits you make ~N requests every ~2h, each separated
  by 75–100s — never a burst, even right after boot or a config change.

---

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | The iframe widget web app (HTML/CSS/JS) |
| `GET /extension` (or `/widget`) | Glance `extension` HTML fragment (all configured subs) |
| `GET /extension?standalone=1` | Full dark HTML page for quick browser preview |
| `GET /api/posts` | Aggregated JSON for all subs (+ per-sub `meta`) |
| `GET /api/posts/<sub>` | JSON for one sub |
| `GET /api/status` | Health + per-sub fetch state, errors, next-fetch timers |
| `GET /healthz` | Liveness probe |

Query params on `/extension`: `subreddits`, `limit`, `collapse`, `title`, `standalone`.

---

## Files

```
reddit-glance-widget/
├── app/
│   ├── server.py      # HTTP server + routing + static file serving
│   ├── scheduler.py   # background refresh loop (throttle logic)
│   ├── reddit.py      # scrape + normalize old.reddit.com HTML listings
│   ├── cache.py       # thread-safe cache w/ disk persistence
│   ├── render.py      # Glance `extension` HTML fragment rendering
│   ├── config.py      # env-based config
│   └── public/        # the iframe web app (Glance-themed)
│       ├── index.html #   theme-sync head script + markup
│       ├── styles.css #   Glance CSS-variable color system
│       └── script.js  #   fetch /api/posts, render, settings
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── reddit_cookie.txt  # you create this (git-ignored)
```
