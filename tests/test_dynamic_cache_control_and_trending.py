"""Tests for dynamic "countdown" max-age Cache-Control headers, 304 ETag handling,
and the refactored trending fetch background job.
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi import Response

import main
import cache


class _FakeRequest:
    def __init__(self, if_none_match=None):
        self.headers = {} if if_none_match is None else {"if-none-match": if_none_match}


class PosterResponse304AndAutoTTLTests(unittest.TestCase):
    KEY = "tt0087332:620:movie:abc123"
    COLON_KEY = "tmdb:12345:620:movie:abc123"
    BODY = b"rendered-poster-jpeg-bytes"

    def setUp(self):
        self.orig_auto_cache = main._cfg.AUTO_CACHE_TTL
        self.orig_cdn_ttl = main._cfg.CDN_CACHE_TTL
        self.orig_disable_composite = main._cfg.DISABLE_COMPOSITE_CACHE
        main._cfg.DISABLE_COMPOSITE_CACHE = False
        main._cfg.CDN_CACHE_TTL = 0
        main._cfg.AUTO_CACHE_TTL = False
        cache.init_db()

    def tearDown(self):
        main._cfg.AUTO_CACHE_TTL = self.orig_auto_cache
        main._cfg.CDN_CACHE_TTL = self.orig_cdn_ttl
        main._cfg.DISABLE_COMPOSITE_CACHE = self.orig_disable_composite
        cache.delete_cached_final_poster(self.KEY)
        cache.delete_cached_final_poster(self.COLON_KEY)

    # -----------------------------------------------------------------------
    # Part 1: 304 Handling & ETag Validation
    # -----------------------------------------------------------------------

    def test_304_matching_standard_quoted_etag(self):
        etag = main._poster_etag(self.BODY)  # e.g. '"<hash>"'
        req = _FakeRequest(if_none_match=etag)
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 304)

    def test_304_matching_stripped_quotes_etag(self):
        etag_raw = main._poster_etag(self.BODY).strip('"')
        req = _FakeRequest(if_none_match=etag_raw)
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 304)

    def test_304_matching_cloudflare_weak_prefix_with_quotes(self):
        etag_raw = main._poster_etag(self.BODY).strip('"')
        req = _FakeRequest(if_none_match=f'W/"{etag_raw}"')
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 304)

    def test_304_matching_lowercase_weak_prefix(self):
        etag_raw = main._poster_etag(self.BODY).strip('"')
        req = _FakeRequest(if_none_match=f'w/"{etag_raw}"')
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 304)

    def test_304_matching_weak_prefix_without_quotes(self):
        etag_raw = main._poster_etag(self.BODY).strip('"')
        req = _FakeRequest(if_none_match=f'W/{etag_raw}')
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 304)

    def test_304_matching_in_comma_separated_etags(self):
        etag_raw = main._poster_etag(self.BODY).strip('"')
        inm = f'W/"someothertag", W/"{etag_raw}", "thirdtag"'
        req = _FakeRequest(if_none_match=inm)
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 304)

    def test_mismatched_etag_returns_200(self):
        req = _FakeRequest(if_none_match='W/"completely_different_hash"')
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, self.BODY)

    def test_304_includes_newly_calculated_cache_control(self):
        """CRITICAL: The 304 response MUST include the newly calculated Cache-Control
        headers so the client's internal timer resets correctly."""
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 0  # defaults to 6 hours (21600s)

        # Seed the poster in cache with an internal TTL of 7200s (2 hours remaining)
        cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=7200)

        etag = main._poster_etag(self.BODY)
        req = _FakeRequest(if_none_match=f'W/{etag}')
        resp = main._poster_response(req, self.BODY, self.KEY, False)

        self.assertEqual(resp.status_code, 304)
        self.assertIn("cache-control", resp.headers)
        # 7200 is less than the 6-hour cap (21600), so it should pass internal TTL ~7200
        cc = resp.headers["cache-control"]
        self.assertTrue(cc.startswith("public, max-age="))
        max_age = int(cc.split("max-age=")[1].split(",")[0])
        self.assertGreaterEqual(max_age, 7190)
        self.assertLessEqual(max_age, 7200)

    # -----------------------------------------------------------------------
    # Part 2: Strict Cache-Control & Countdown Logic
    # -----------------------------------------------------------------------

    def test_auto_cache_ttl_disabled_preserves_cdn_cache_ttl(self):
        main._cfg.AUTO_CACHE_TTL = False
        main._cfg.CDN_CACHE_TTL = 3600
        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.headers["cache-control"], "public, max-age=3600")

    def test_auto_cache_ttl_disabled_and_cdn_cache_ttl_zero_gives_no_cache_control(self):
        main._cfg.AUTO_CACHE_TTL = False
        main._cfg.CDN_CACHE_TTL = 0
        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertNotIn("cache-control", resp.headers)

    def test_auto_cache_ttl_default_cap_is_6_hours_when_cdn_cache_ttl_is_zero(self):
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 0

        # Item with standard 7-day TTL (604800s) > 6-hour cap (21600s)
        cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=604800)

        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.headers["cache-control"], "public, max-age=21600")

    def test_auto_cache_ttl_respects_custom_cdn_cache_ttl_cap(self):
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 3600  # 1 hour cap

        # Item with 7200s internal TTL > 3600s cap
        cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=7200)

        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertEqual(resp.headers["cache-control"], "public, max-age=3600")

    def test_auto_cache_ttl_passes_remaining_internal_ttl_when_less_than_cap(self):
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 21600  # 6 hours

        # Item with 3000s remaining internal TTL (< 21600s cap)
        cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=3000)

        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        cc = resp.headers["cache-control"]
        self.assertTrue(cc.startswith("public, max-age="))
        max_age = int(cc.split("max-age=")[1].split(",")[0])
        self.assertGreaterEqual(max_age, 2990)
        self.assertLessEqual(max_age, 3000)

    def test_no_background_stale_reads_never_includes_stale_while_revalidate(self):
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 3600
        cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=7200)

        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, False)
        self.assertNotIn("stale-while-revalidate", resp.headers["cache-control"])

    def test_trending_posters_time_until_next_scheduled_run(self):
        """For trending posters generated on first run of container, the time
        until next scheduled run should be used, respecting the cap."""
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 0  # 6 hour cap (21600)

        # Mock _seconds_until_next_trending_fetch to return 10800 seconds (3 hours)
        with patch.object(main, "_seconds_until_next_trending_fetch", return_value=10800.0):
            # When rendered, trending poster sets ttl_override to time until next scheduled run
            ttl = main._seconds_until_next_trending_fetch()
            cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=int(ttl))

            req = _FakeRequest()
            resp = main._poster_response(req, self.BODY, self.KEY, False)
            cc = resp.headers["cache-control"]
            max_age = int(cc.split("max-age=")[1].split(",")[0])
            self.assertGreaterEqual(max_age, 10790)
            self.assertLessEqual(max_age, 10800)

    def test_trending_posters_capped_when_next_scheduled_run_exceeds_cap(self):
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 0  # 6 hour cap (21600)

        # Next run is in 18 hours (64800s)
        with patch.object(main, "_seconds_until_next_trending_fetch", return_value=64800.0):
            ttl = main._seconds_until_next_trending_fetch()
            cache.set_cached_final_poster(self.KEY, self.BODY, ttl_override=int(ttl))

            req = _FakeRequest()
            resp = main._poster_response(req, self.BODY, self.KEY, False)
            # Must respect the 6-hour cap (21600)
            self.assertEqual(resp.headers["cache-control"], "public, max-age=21600")


class TrendingFetchCycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server_tmdb_key = main._cfg.SERVER_TMDB_KEY
        main._cfg.SERVER_TMDB_KEY = "dummy-tmdb-key"
        cache.init_db()
        self.db = cache.get_db()

    def tearDown(self):
        main._cfg.SERVER_TMDB_KEY = self.server_tmdb_key
        with cache._db_lock:
            self.db.execute("DELETE FROM final_poster_cache")
            self.db.commit()

    async def test_safe_parsing_and_bulk_delete_and_sequential_regeneration(self):
        """Verify:
        1. Keys with colons in prefixes (like tmdb:12345:movie:...) are parsed safely via parts[-3] and parts[-2].
        2. Categorize: still trending -> items_to_regenerate, dropped -> items_to_delete_only.
        3. Instant bulk delete: delete_cached_final_poster executed for both before any HTTP requests.
        4. Sequential regeneration: local_client.get called only for items_to_regenerate.
        """
        # Seed final_poster_cache with:
        # 1. Normal trending movie (tmdb_id=100)
        # 2. Colon prefix trending series (tmdb_id=200, type=tv, key has 'tmdb:200')
        # 3. Dropped item (tmdb_id=300, not trending anymore)
        # 4. Colon prefix dropped item (tmdb_id=400, not trending anymore, key has 'tmdb:400')
        key_trending_movie = "tt100:100:movie:hash1"
        key_trending_tv = "tmdb:200:200:tv:hash2"
        key_dropped_movie = "tt300:300:movie:hash3"
        key_dropped_tv = "tmdb:400:400:series:hash4"

        cache.set_cached_final_poster(key_trending_movie, b"img1", request_params="tmdb_id=100&type=movie")
        cache.set_cached_final_poster(key_trending_tv, b"img2", request_params="tmdb_id=200&type=tv")
        cache.set_cached_final_poster(key_dropped_movie, b"img3", request_params="tmdb_id=300&type=movie")
        cache.set_cached_final_poster(key_dropped_tv, b"img4", request_params="tmdb_id=400&type=series")

        # Mock candidates returned from TMDB: only 100 (movie) and 200 (tv)
        mock_candidates = [
            {"tmdb_id": "100", "media_type": "movie"},
            {"tmdb_id": "200", "media_type": "tv"},
        ]

        deleted_keys = []
        http_requests = []

        orig_delete = cache.delete_cached_final_poster

        def tracking_delete(cache_key):
            # Assert that no HTTP calls have occurred before or during deletion
            self.assertEqual(len(http_requests), 0, "HTTP request made before bulk delete completed!")
            deleted_keys.append(cache_key)
            orig_delete(cache_key)

        async def fake_get(url):
            http_requests.append(url)
            return httpx.Response(200, content=b"new-poster")

        with patch("main.fetch_trending_candidates", AsyncMock(return_value=mock_candidates)), \
             patch("main.delete_cached_final_poster", side_effect=tracking_delete), \
             patch("httpx.AsyncClient.get", side_effect=fake_get):

            dummy_client = AsyncMock()
            await main._run_trending_fetch_cycle(dummy_client)

        # 1. Bulk delete should have deleted ALL 4 keys (both dropped and trending)
        self.assertIn(key_trending_movie, deleted_keys)
        self.assertIn(key_trending_tv, deleted_keys)
        self.assertIn(key_dropped_movie, deleted_keys)
        self.assertIn(key_dropped_tv, deleted_keys)

        # 2. Sequential regeneration should ONLY request the 2 trending items
        self.assertEqual(len(http_requests), 2)
        self.assertIn("/poster?tmdb_id=100&type=movie", http_requests)
        self.assertIn("/poster?tmdb_id=200&type=tv", http_requests)

        # Dropped items should NOT have been regenerated
        self.assertNotIn("/poster?tmdb_id=300&type=movie", http_requests)
        self.assertNotIn("/poster?tmdb_id=400&type=series", http_requests)

    async def test_items_with_empty_params_are_deleted_not_regenerated(self):
        key_empty_params = "tt500:500:movie:hash5"
        cache.set_cached_final_poster(key_empty_params, b"img5", request_params="")

        mock_candidates = [{"tmdb_id": "500", "media_type": "movie"}]
        http_requests = []
        deleted_keys = []

        with patch("main.fetch_trending_candidates", AsyncMock(return_value=mock_candidates)), \
             patch("main.delete_cached_final_poster", side_effect=lambda k: deleted_keys.append(k)), \
             patch("httpx.AsyncClient.get", side_effect=lambda url: (http_requests.append(url), httpx.Response(200))[1]):
            await main._run_trending_fetch_cycle(AsyncMock())

        self.assertIn(key_empty_params, deleted_keys)
        self.assertEqual(len(http_requests), 0)

    async def test_regeneration_errors_do_not_halt_cycle(self):
        key1 = "tt601:601:movie:h1"
        key2 = "tt602:602:movie:h2"
        cache.set_cached_final_poster(key1, b"i1", request_params="tmdb_id=601&type=movie")
        cache.set_cached_final_poster(key2, b"i2", request_params="tmdb_id=602&type=movie")

        mock_candidates = [
            {"tmdb_id": "601", "media_type": "movie"},
            {"tmdb_id": "602", "media_type": "movie"},
        ]
        http_requests = []

        async def failing_get(url):
            http_requests.append(url)
            if "601" in url:
                raise httpx.ConnectError("network failure")
            return httpx.Response(200, content=b"new")

        with patch("main.fetch_trending_candidates", AsyncMock(return_value=mock_candidates)), \
             patch("main.delete_cached_final_poster"), \
             patch("httpx.AsyncClient.get", side_effect=failing_get):
            await main._run_trending_fetch_cycle(AsyncMock())

        # Both items should have been attempted despite first one failing
        self.assertEqual(len(http_requests), 2)
        self.assertIn("/poster?tmdb_id=601&type=movie", http_requests)
        self.assertIn("/poster?tmdb_id=602&type=movie", http_requests)

    async def test_malformed_cache_keys_are_safely_ignored(self):
        malformed_key = "short:key"
        cache.set_cached_final_poster(malformed_key, b"img", request_params="foo=bar")
        mock_candidates = [{"tmdb_id": "100", "media_type": "movie"}]
        deleted_keys = []

        with patch("main.fetch_trending_candidates", AsyncMock(return_value=mock_candidates)), \
             patch("main.delete_cached_final_poster", side_effect=lambda k: deleted_keys.append(k)):
            await main._run_trending_fetch_cycle(AsyncMock())

        self.assertNotIn(malformed_key, deleted_keys)

    async def test_items_invalidated_by_snapshot_update_are_still_regenerated(self):
        """If set_cached_trending_snapshot runs during candidates fetch and invalidates
        (deletes) an item from final_poster_cache, pre-snapshotted rows ensure it is still
        regenerated with its original request_params."""
        key = "tt28014327:1137844:movie:abc123"
        cache.set_cached_final_poster(key, b"old-poster", request_params="tmdb_id=1137844&type=movie")

        # Simulate fetch_trending_candidates side effect: invalidating the item
        async def fake_candidates(*args, **kwargs):
            cache.invalidate_final_posters("1137844", "movie")
            return [{"tmdb_id": "1137844", "media_type": "movie"}]

        http_requests = []
        async def fake_get(url):
            http_requests.append(url)
            return httpx.Response(200, content=b"new-poster")

        with patch("main.fetch_trending_candidates", side_effect=fake_candidates), \
             patch("httpx.AsyncClient.get", side_effect=fake_get):
            await main._run_trending_fetch_cycle(AsyncMock())

        self.assertEqual(len(http_requests), 1)
        self.assertIn("/poster?tmdb_id=1137844&type=movie", http_requests)

    async def test_fetch_trending_candidates_with_max_pages_per_list(self):
        """Verify fetch_trending_candidates respects max_pages_per_list and does not truncate."""
        import tmdb
        mock_resp = {"results": [{"id": i} for i in range(1, 31)]}
        mock_client = AsyncMock()
        mock_client.get.return_value = httpx.Response(200, json=mock_resp, request=httpx.Request("GET", "http://test"))

        with patch("tmdb.fetch_trending_source_ids", AsyncMock(return_value=None)):
            candidates = await tmdb.fetch_trending_candidates(
                mock_client, "fake_key", max_items=200, max_pages_per_list=2
            )
            # 30 items per page * 2 pages = 60 items per type (movie, tv)
            self.assertEqual(len(candidates), 60)
            movie_ids = [c["tmdb_id"] for c in candidates if c["media_type"] == "movie"]
            self.assertEqual(len(movie_ids), 30)


class ETagCleanHelperTests(unittest.TestCase):
    def test_clean_etag_edge_cases(self):
        self.assertIsNone(main._clean_etag(None))
        self.assertIsNone(main._clean_etag(""))
        self.assertEqual(main._clean_etag('""'), "")
        self.assertEqual(main._clean_etag('  "abc"  '), "abc")
        self.assertEqual(main._clean_etag('W/"abc"'), "abc")
        self.assertEqual(main._clean_etag('w/"abc"'), "abc")
        self.assertEqual(main._clean_etag('W/abc'), "abc")
        self.assertEqual(main._clean_etag('""abc""'), "abc")
        self.assertEqual(main._clean_etag('abc'), "abc")
        self.assertEqual(main._clean_etag('"W/abc"'), "abc")
        self.assertEqual(main._clean_etag('"W/\\"abc\\""'), "abc")
        self.assertEqual(main._clean_etag('W/ "abc"'), "abc")
        self.assertEqual(main._clean_etag('*'), "*")
        self.assertEqual(main._clean_etag('W/"*"'), "*")

    def test_304_matching_outer_quoted_weak_prefix(self):
        etag_raw = main._poster_etag(b"poster-bytes").strip('"')
        req = _FakeRequest(if_none_match=f'"W/{etag_raw}"')
        resp = main._poster_response(req, b"poster-bytes", "tt1:1:movie:h", False)
        self.assertEqual(resp.status_code, 304)

    def test_304_matching_wildcard(self):
        req = _FakeRequest(if_none_match='*')
        resp = main._poster_response(req, b"poster-bytes", "tt1:1:movie:h", False)
        self.assertEqual(resp.status_code, 304)

    def test_304_ignores_empty_or_whitespace_tokens(self):
        etag_raw = main._poster_etag(b"poster-bytes").strip('"')
        req = _FakeRequest(if_none_match=f', , "different", W/"{etag_raw}"')
        resp = main._poster_response(req, b"poster-bytes", "tt1:1:movie:h", False)
        self.assertEqual(resp.status_code, 304)


class AutoCacheTTLEdgeCasesTests(unittest.TestCase):
    KEY = "tt0087332:620:movie:abc123"
    BODY = b"poster-bytes"

    def setUp(self):
        self.orig_auto = main._cfg.AUTO_CACHE_TTL
        self.orig_cdn = main._cfg.CDN_CACHE_TTL
        self.orig_disable = main._cfg.DISABLE_COMPOSITE_CACHE
        main._cfg.AUTO_CACHE_TTL = True
        main._cfg.CDN_CACHE_TTL = 0
        main._cfg.DISABLE_COMPOSITE_CACHE = False
        cache.init_db()

    def tearDown(self):
        main._cfg.AUTO_CACHE_TTL = self.orig_auto
        main._cfg.CDN_CACHE_TTL = self.orig_cdn
        main._cfg.DISABLE_COMPOSITE_CACHE = self.orig_disable
        cache.delete_cached_final_poster(self.KEY)

    def test_provisional_render_with_auto_cache_ttl_still_no_store(self):
        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, provisional=True)
        self.assertEqual(resp.headers["cache-control"], "no-store, no-cache, must-revalidate")
        self.assertEqual(resp.headers["pragma"], "no-cache")
        self.assertNotIn("etag", resp.headers)

    def test_disable_composite_cache_with_auto_cache_ttl_still_no_store(self):
        main._cfg.DISABLE_COMPOSITE_CACHE = True
        req = _FakeRequest()
        resp = main._poster_response(req, self.BODY, self.KEY, provisional=False)
        self.assertEqual(resp.headers["cache-control"], "no-store, no-cache, must-revalidate")

    def test_zero_remaining_ttl_yields_max_age_zero(self):
        # Remaining TTL of 0
        resp = Response(content=self.BODY)
        main._apply_poster_cache_headers(resp, provisional=False, internal_ttl=0)
        self.assertEqual(resp.headers["cache-control"], "public, max-age=0")


if __name__ == "__main__":
    unittest.main()
