"""What a poster response promises the client, and whether it can keep it.

Two bugs live here.  The first: a poster the server refused to cache must not be
cached by the client either.  Quality is fetched off the request path unless
wait_for_quality is set, so the first request for a cold title renders without
badges; the pipeline knows that and skips the composite cache.  With QualiCache
the window is wider still — "pending" means several requests in a row can render
provisionally before any tokens exist.

The second: the validator itself.  It used to be the composite cache key, which
is a pure function of the ids, the render params and the server signature.  A
re-render under the same key — a trending rank that turned over, a release
status that moved on — inherited the same ETag, so a client revalidated, matched
it, took a 304 and kept the superseded poster forever.  The validator is now the
hash of the bytes being served, which is the only thing it can honestly claim to
identify.
"""

import asyncio
import time
import unittest

from fastapi import Response
from fastapi.testclient import TestClient

import main


class _FakeRequest:
    """`_poster_response` reads exactly one thing off the request."""

    def __init__(self, if_none_match=None):
        self.headers = {} if if_none_match is None else {"if-none-match": if_none_match}


class PosterResponseTests(unittest.TestCase):
    KEY = "tt0087332:620:movie:abc123"
    BODY = b"finished-poster-bytes"

    def setUp(self):
        self.disable_composite = main._cfg.DISABLE_COMPOSITE_CACHE
        self.cdn_ttl = main._cfg.CDN_CACHE_TTL
        self.auto_cache_ttl = main._cfg.AUTO_CACHE_TTL
        main._cfg.DISABLE_COMPOSITE_CACHE = False
        main._cfg.CDN_CACHE_TTL = 0
        main._cfg.AUTO_CACHE_TTL = False

    def tearDown(self):
        main._cfg.DISABLE_COMPOSITE_CACHE = self.disable_composite
        main._cfg.CDN_CACHE_TTL = self.cdn_ttl
        main._cfg.AUTO_CACHE_TTL = self.auto_cache_ttl

    def _response(self, provisional, key=KEY, body=BODY, if_none_match=None):
        return main._poster_response(
            _FakeRequest(if_none_match), body, key, provisional
        )

    # ---- provisional renders ----

    def test_a_provisional_render_ships_no_validator(self):
        # The original bug: an ETag here lets the client revalidate its
        # badge-less copy and keep whatever it decides is still current.
        self.assertNotIn("etag", self._response(True).headers)

    def test_a_provisional_render_asks_not_to_be_stored(self):
        headers = self._response(True).headers
        self.assertEqual(headers["cache-control"], "no-store, no-cache, must-revalidate")
        self.assertEqual(headers["pragma"], "no-cache")

    def test_a_provisional_render_is_never_given_a_cdn_ttl(self):
        # Worse than the ETag: this tells a CDN to serve the badge-less poster
        # to everyone for the full TTL.
        main._cfg.CDN_CACHE_TTL = 86400
        self.assertNotIn("max-age", self._response(True).headers["cache-control"])

    def test_a_provisional_render_never_answers_a_conditional_request(self):
        # Even a client holding the exact bytes gets the full response back,
        # because a 304 would refresh a copy the server never blessed.
        resp = self._response(True, if_none_match=main._poster_etag(self.BODY))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, self.BODY)

    # ---- finished renders ----

    def test_a_finished_render_still_gets_a_validator(self):
        self.assertEqual(
            self._response(False).headers["etag"], main._poster_etag(self.BODY)
        )

    def test_a_finished_render_still_gets_the_cdn_ttl(self):
        main._cfg.CDN_CACHE_TTL = 86400
        self.assertEqual(
            self._response(False).headers["cache-control"], "public, max-age=86400, stale-if-error=14400"
        )

    def test_a_quality_override_has_no_key_to_validate_against(self):
        # quality= renders are one-offs and never enter the composite cache.
        self.assertNotIn("etag", self._response(False, key=None).headers)

    def test_composite_caching_off_still_means_no_store(self):
        main._cfg.DISABLE_COMPOSITE_CACHE = True
        headers = self._response(False, key=None).headers
        self.assertEqual(headers["cache-control"], "no-store, no-cache, must-revalidate")

    # ---- the validator tracks the content, not the key ----

    def test_a_re_render_under_the_same_key_mints_a_new_validator(self):
        """The whole point.  Trending rank turns over daily and the sash it
        drives changes the rendered bytes, but not the ids, the render params or
        the server signature — so the composite key, and the ETag that used to
        be a copy of it, stayed put.  Every client that revalidated took a 304
        and kept last week's sash.
        """
        yesterday = self._response(False, body=b"poster-with-trending-sash")
        today = self._response(False, body=b"poster-with-no-sash")

        self.assertNotEqual(yesterday.headers["etag"], today.headers["etag"])

    def test_identical_bytes_validate_identically(self):
        # The other half: a re-render that changed nothing must not churn the
        # validator, or every composite expiry costs a full payload.
        first = self._response(False, body=b"same-bytes")
        second = self._response(False, body=b"same-bytes")

        self.assertEqual(first.headers["etag"], second.headers["etag"])

    # ---- conditional requests ----

    def test_a_matching_validator_gets_a_304(self):
        resp = self._response(False, if_none_match=main._poster_etag(self.BODY))
        self.assertEqual(resp.status_code, 304)

    def test_a_stale_validator_gets_the_new_poster(self):
        resp = self._response(
            False, body=b"todays-render", if_none_match=main._poster_etag(b"last-weeks-render")
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, b"todays-render")

    def test_a_304_carries_the_headers_it_stands_in_for(self):
        """RFC 9111 §4.3.4 updates the stored response from whatever the 304
        carries.  A bare one leaves the client on the max-age it was first
        served with, so a poster whose TTL later shortened would never learn it.
        """
        main._cfg.CDN_CACHE_TTL = 86400
        resp = self._response(False, if_none_match=main._poster_etag(self.BODY))

        self.assertEqual(resp.status_code, 304)
        self.assertEqual(resp.headers["etag"], main._poster_etag(self.BODY))
        self.assertEqual(resp.headers["cache-control"], "public, max-age=86400, stale-if-error=14400")

    # ---- CORS headers ----

    def test_cors_headers_on_provisional_render(self):
        headers = self._response(True).headers
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(headers["access-control-allow-headers"], "*")

    def test_cors_headers_on_finished_render(self):
        headers = self._response(False).headers
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(headers["access-control-allow-headers"], "*")

    # ---- Dynamic cache_ttl ----

    def test_dynamic_cache_ttl_overrides_cdn_ttl(self):
        """A status-derived TTL (e.g. Cinema = 1 day) takes precedence when AUTO_CACHE_TTL is on."""
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 86400
        resp = Response(content=b"")
        main._apply_poster_cache_headers(resp, False, cache_ttl=3600)
        self.assertEqual(
            resp.headers["cache-control"],
            "public, max-age=3600, stale-if-error=14400",
        )

    def test_dynamic_cache_ttl_ignored_when_auto_disabled(self):
        """When AUTO_CACHE_TTL is off, cache_ttl is ignored and CDN_CACHE_TTL is used."""
        main._cfg.AUTO_CACHE_TTL = False
        main._cfg.CDN_CACHE_TTL = 86400
        resp = Response(content=b"")
        main._apply_poster_cache_headers(resp, False, cache_ttl=3600)
        self.assertEqual(
            resp.headers["cache-control"],
            "public, max-age=86400, stale-if-error=14400",
        )

    def test_dynamic_cache_ttl_none_falls_back_to_cdn_ttl(self):
        """When no status-derived TTL is available, CDN_CACHE_TTL is used."""
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 7200
        resp = Response(content=b"")
        main._apply_poster_cache_headers(resp, False, cache_ttl=None)
        self.assertEqual(
            resp.headers["cache-control"],
            "public, max-age=7200, stale-if-error=14400",
        )

    def test_no_cache_control_when_both_ttls_are_zero(self):
        """No Cache-Control is emitted when CDN_CACHE_TTL=0 and no override."""
        main._cfg.CDN_CACHE_TTL = 0
        resp = Response(content=b"")
        main._apply_poster_cache_headers(resp, False, cache_ttl=None)
        self.assertNotIn("cache-control", resp.headers)

    def test_dynamic_cache_ttl_recency_override_1_day(self):
        """1-day recency override (86400s) sets max-age=86400 with CORS headers."""
        main._cfg.AUTO_CACHE_TTL = True
        resp = Response(content=b"")
        main._apply_poster_cache_headers(resp, "key", False, cache_ttl=86400)
        self.assertEqual(
            resp.headers["cache-control"],
            "public, max-age=86400, stale-while-revalidate=3600, stale-if-error=14400",
        )
        self.assertEqual(resp.headers["access-control-allow-origin"], "*")
        self.assertEqual(resp.headers["access-control-allow-headers"], "*")

class CoalescedRenderTests(unittest.TestCase):
    """A request coalesced onto a provisional render inherits its status.

    The follower never runs the pipeline, so it has no view of its own on
    whether quality made it in — without the flag riding along on the future it
    would stamp an ETag on bytes the leader deliberately withheld, which is the
    burst case: a library load fans out dozens of concurrent requests per title.
    """

    PARAMS = {"tmdb_id": "620", "imdb_id": "tt0087332", "type": "movie"}

    def setUp(self):
        self.access_key = main._cfg.ACCESS_KEY
        self.tmdb_key = main._cfg.SERVER_TMDB_KEY
        self.disable_composite = main._cfg.DISABLE_COMPOSITE_CACHE
        self.real_get_cached = main.get_cached_final_poster
        main._cfg.ACCESS_KEY = ""
        main._cfg.SERVER_TMDB_KEY = "test-key"
        main._cfg.DISABLE_COMPOSITE_CACHE = False

    def tearDown(self):
        main._cfg.ACCESS_KEY = self.access_key
        main._cfg.SERVER_TMDB_KEY = self.tmdb_key
        main._cfg.DISABLE_COMPOSITE_CACHE = self.disable_composite
        main.get_cached_final_poster = self.real_get_cached
        main._render_inflight.clear()

    def _coalesced_response(self, payload, provisional):
        """Serve one request off a seeded in-flight render and hand back both
        the response and the key it coalesced on.

        The composite key is read off a served cache hit rather than recomputed
        here — duplicating get_poster's hashing would only drift.  It has to be
        read under the same lifespan as the coalesced request: startup settles
        the render-assets signature that feeds the hash, so a key captured
        outside the context manager names a different poster.
        """
        seen = []
        with TestClient(main.app) as client:
            _far_future = int(time.time()) + 86400
            main.get_cached_final_poster = lambda key: (seen.append(key), (b"jpeg", _far_future))[1]
            self.assertEqual(client.get("/poster", params=self.PARAMS).status_code, 200)
            key = seen[0]

            main.get_cached_final_poster = lambda _key: None

            async def _seed():
                fut = asyncio.get_running_loop().create_future()
                fut.set_result((payload, provisional))
                main._render_inflight[key] = fut

            # The future is awaited on the app's loop, so seed it from there.
            client.portal.call(_seed)
            return client.get("/poster", params=self.PARAMS), key

    def test_coalescing_onto_a_provisional_render_inherits_no_store(self):
        resp, _key = self._coalesced_response(b"provisional-jpeg", True)

        self.assertEqual(resp.content, b"provisional-jpeg")
        self.assertNotIn("etag", resp.headers)
        self.assertEqual(
            resp.headers["cache-control"], "no-store, no-cache, must-revalidate"
        )

    def test_coalescing_onto_a_finished_render_still_validates(self):
        resp, _key = self._coalesced_response(b"finished-jpeg", False)

        self.assertEqual(resp.content, b"finished-jpeg")
        self.assertEqual(resp.headers["etag"], main._poster_etag(b"finished-jpeg"))


if __name__ == "__main__":
    unittest.main()
