# Advanced configuration

Tuning and debugging variables that are **not** in `.env.example`. These all have
working defaults and a running instance needs none of them.

This covers the poster service. The Plex and Jellyfin sync scripts have their own
variables (`PLEX_*`, `JELLYFIN_*`, `POSTERSPLUS_*`) documented in the README,
as do `TMDB_LANGUAGE`, `TVDB_CONCURRENCY` and `DISCOVERY_OVERRIDES_PATH`.

Set them the same way as anything in `.env.example`: as environment variables on
the container, or as lines in your `.env`.

> Several of these change how posters are selected or how burned-in text is
> detected. Those results are cached under a signature that includes the setting,
> so changing one invalidates the affected cache and re-renders on next request.

---

## Art sources (TVDB, AniList, Kitsu)

### `TVDB_ARTWORK_CACHE_DURATION`

Days to cache a title's resolved TVDB id and artwork listing.

Default: `14`

### `TVDB_NEG_CACHE_DURATION`

Days to cache a "no TVDB match / no art" result — kept shorter than `TVDB_ARTWORK_CACHE_DURATION` so newly-added TVDB art is picked up sooner.

Default: `3`

### `TVDB_TYPES_CACHE_DURATION`

Days to cache the TVDB artwork-type catalogue, which rarely changes.

Default: `30`

### `ANILIST_CONCURRENCY`

Caps concurrent calls, per provider — their limits differ by an order of magnitude. AniList advertises 90 req/min per IP but has served a degraded 30 for a long while, so keep it low. Kitsu publishes no hard limit and answers in ~0.2s. Art and metadata are cached after first fetch, so these only matter while the cache is cold.

Default: `3`

### `KITSU_CONCURRENCY`

Same cap for Kitsu, which publishes no hard limit and answers in ~0.2s, so it
tolerates more concurrency than AniList.

Default: `8`

### `ANIME_METADATA_CACHE_DURATION`

Days to cache anime metadata. Shorter than the TVDB artwork window because the community score drifts, and the score is part of this payload.

Default: `7`

### `ANIME_NEG_CACHE_DURATION`

Days to cache a "no such id on this provider" result. Throttles and network errors are never cached — only definitive 404s.

Default: `3`

### `ANILIST_API_URL`

Override the provider endpoints (useful for a proxy or a mirror).

Default: `https://graphql.anilist.co`

### `KITSU_API_BASE`

Override the Kitsu endpoint (useful for a proxy or a mirror).

Default: `https://kitsu.io/api/edge`

---

## Caching and output

### `CINEMA_MAX_AGE_YEARS`

Movies whose only known release is a theatrical date older than this many years are treated as "Streaming" rather than "Cinema" — guards against stale TMDB data that never got a physical/digital release date added. Set to 0 to disable the gate entirely. Default: 3.

Default: `3`

### `TMDB_IMAGE_CACHE_JITTER_DAYS`

+/- half this many days of per-title jitter applied to the TMDB poster/logo cache durations, so a large batch cached at once (e.g. cache warming) doesn't all expire on the same day. Default: 10.

Default: `10`

### `COMPOSITE_CACHE_TTL_JITTER`

+/- half this many seconds of per-title jitter applied to COMPOSITE_CACHE_TTL, so a large batch of composites rendered around the same time (e.g. cache warming) don't all expire and re-render at once. Default: 172800 (2 days).

Default: `172800`

### `TRENDING_SOURCE_MAX_ITEMS`

Cap on entries taken from a custom trending source (default 500).

Default: `500`

### `COMPOSITE_MEM_ENTRIES`

Fully-rendered composites kept in an in-memory LRU (L1) cache, served without a SQLite read. ~100-300 KB each; 500 entries is roughly 50-150 MB. Set to 0 to disable the in-memory cache entirely. Default: 500.

Default: `500`

### `DISABLE_COMPOSITE_CACHE`

Disable composite poster caching entirely. Every request re-renders from scratch. Only use this during development — never in production. Set to `true` to disable; caching is on unless you do.

Default: `false`

---

## Cache warming

### `CACHE_WARM_AT_HOUR`

Optionally align steady-state warm cycles to a fixed local hour of day (e.g. "4" or "4:30"), instead of running exactly CACHE_WARM_INTERVAL_HOURS after the previous cycle. Useful for scheduling the OCR-heavy cycle off-peak. Uses the container's TZ (UTC if unset). Empty = old behaviour.

Default: unset (interval-based scheduling)

### `CACHE_WARM_QUALITY_ENABLED`

Also pre-fetch quality badge data (resolution/source/HDR tokens) for every warmed title via your configured quality source. WARNING: against a public Stremio scraper addon (rather than your own self-hosted instance) this volume of traffic can get your server's IP rate-limited or blocked. Only enable this against your own AIOStreams/scraper instance. Off by default.

Default: `false`

### `CACHE_WARM_CATALOG_URLS`

Comma-separated Stremio addon manifest URLs to pre-warm in addition to TMDB trending/popular/supplemental — useful for a custom catalog you want fast on first load. Each catalog is fetched and warmed first, within the `CACHE_WARM_TMDB_BUDGET` / `CACHE_WARM_MDBLIST_BUDGET` ceilings.

### `CACHE_WARM_CATALOG_MAX_ITEMS`

Max items pre-warmed per catalog (across pagination), so one large catalog can't consume the whole cycle's budget. Default: 100.

Default: `100`

---

## Rendering, poster selection and text detection

### `LOGO_CONTRAST_RESCUE`

When a flat (single-colour) logo's average colour is too close to the poster background, recolour it (white / black / complementary accent) so it stays legible. Multi-colour and outline logos are never touched. Experimental and off by default while it's tested — it can mis-handle some logos. Set to true to enable. Default: false.

Default: `false`

### `LOGO_STRETCH_FACTOR`

Logo fill-stretch: a slim logo whose clamped size leaves it looking lost is enlarged toward its size cap by up to this factor (one axis only). 1.0 = no enlargement. Only applies when stretching is enabled (see `LOGO_STRETCH_DISABLED`). Default: 1.2.

Default: `1.2`

### `LOGO_STRETCH_DISABLED`

Fill-stretch is OFF by default (every logo kept at its true clamped size). Set to false to enable the stretch described in `LOGO_STRETCH_FACTOR`. Default: true.

Default: `true`

### `DEBUG_LOGO_SIZING`

Emit per-logo sizing telemetry (source dims, aspect, final dims) at INFO level. Handy when tuning logo size; noisy in normal operation. Default: false.

Default: `false`

### `TMDB_POSTER_MIN_VOTES`

Minimum votes preferred when selecting among TMDB textless posters. Candidates cannot be more than MAX_SCORE_DROP below the highest-rated textless option, so several negative votes cannot force selection of very poorly rated art. Defaults: 3 votes and a maximum 1.0-point downgrade.

Default: `3`

### `TMDB_POSTER_MAX_SCORE_DROP`

How far below the highest-rated textless option a candidate may fall before it is
rejected, in TMDB rating points. See `TMDB_POSTER_MIN_VOTES`.

Default: `1.0`

### `RATING_MIN_VOTES`

Ignore provider ratings with fewer votes than this. Roger Ebert is exempt because MDBList reports each review as one rating. Default: 10.

Default: `10`

### `TEXTLESS_TEXT_DETECTION`

Detect burned-in title text on posters TMDB mislabelled as "textless" and skip compositing our own logo so the title isn't doubled. Uses PP-OCRv5 Mobile; its ~4.6MB model is downloaded once into the cache volume. On by default; set to false to opt out. Default: true.

Default: `true`

### `TEXTLESS_FAKE_REPORT`

Append OCR-rejected TMDB posters to a deduplicated human-review file at /app/cache/fake_textless_posters.txt. Default: true.

Default: `true`

### `TEXTLESS_FAKE_REPORT_PATH`

Optional custom path inside the container. Keep it under /app/cache to retain the report in the existing cache volume.

Default: `/app/cache/fake_textless_posters.txt`

### `PPOCR_BOX_THRESHOLD`

Minimum PP-OCR text-box confidence. Higher is stricter. Default: 0.70.

Default: `0.70`

### `PPOCR_WIDE_BOX_THRESHOLD`

Lower-confidence fallback for wide, title-shaped regions. A box qualifies when it meets `PPOCR_WIDE_MIN_ASPECT`, `PPOCR_WIDE_MIN_AREA` and `PPOCR_WIDE_MIN_Y`. This catches stylised titles that PP-OCR scores around 0.30-0.69 without accepting the compact shapes in the Alien artwork.

Default: `0.30`

### `PPOCR_WIDE_MIN_ASPECT`

Minimum width-to-height ratio for the wide-box fallback. See
`PPOCR_WIDE_BOX_THRESHOLD`.

Default: `3.0`

### `PPOCR_WIDE_MIN_AREA`

Minimum share of the poster's area a box must cover for the wide-box fallback.
See `PPOCR_WIDE_BOX_THRESHOLD`.

Default: `0.01`

### `PPOCR_WIDE_MIN_Y`

Minimum vertical centre for the poster-only geometric fallback used when OCR cannot read a centred, title-shaped box. Backdrop scans require title confirmation.

Default: `0.55`

### `TEXTLESS_DETECTION_CONCURRENCY`

Independent PP-OCR sessions for parallel cold-cache scans. Sessions SPLIT the ONNX thread budget rather than adding to it, so raising this makes each single scan slower: measured on 4 cores, ~88ms at 1 session and ~139ms at 2, against bulk throughput of 8.9 vs 11.0 scans/sec. The background scan queue drains one item at a time, so a second session only earns its ~50MB during a cold-cache sweep of a large library. Default 1. Thread sizing follows the container's real CPU budget (cgroup quota), not the host's core count.

Default: `1`

### `TEXTLESS_SCAN_TOP`

Fraction of poster height skipped from the TOP before counting title text. Titles can sit top/middle/bottom, so this scans almost the whole poster; the small default margin ignores studio/network bugs at the very edge. Raise it if top-edge logos cause false positives; 0 scans the entire poster. Default: 0.08.

Default: `0.08`

### `BAKE_PPOCR_MODEL`

BUILD-TIME ONLY (read by compose at `docker compose build`): bake the ~4.6MB PP-OCRv5 Mobile model into the image. Set false to download it on first use.

Default: `true`

---

## Model files

Overrides for the bundled detection models. Useful for an air-gapped host, a
shared read-only model volume, or testing a different PP-OCR revision — not
needed otherwise.

### `PPOCR_MODEL_PATH`

Where the PP-OCRv5 detection model is read from. Defaults to the copy baked into
the image at `/app/models/ch_PP-OCRv5_det_mobile.onnx`, falling back to
`/app/cache/` when the image was built with `BAKE_PPOCR_MODEL=false`.

### `PPOCR_MODEL_URL`

Where the model is downloaded from when it is not already present.

### `PPOCR_MODEL_SHA256`

Expected checksum of that download. A mismatch fails the load rather than
running an unverified model, so change this together with `PPOCR_MODEL_URL`.

### `PPOCR_SKIP_MODEL_HASH`

Set to `1`/`true`/`yes` to skip checksum verification. Only sensible when you are
deliberately supplying your own model via `PPOCR_MODEL_PATH`.

Default: unset

### `YUNET_MODEL_PATH`

Where the YuNet face-detection model is read from. Face detection soft-disables
if it is missing, falling back to the saliency crop.

Default: `models/face_detection_yunet.onnx` alongside the source
