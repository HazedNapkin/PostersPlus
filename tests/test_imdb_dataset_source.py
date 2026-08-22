"""The IMDb weight can be sourced from a local, MDBList-free dataset instead
of MDBList's aggregated response.

These tests cover: the dataset module's own enable/lookup/refresh behaviour
in isolation, and the /poster-side wiring that merges a dataset value into
whatever MDBList did or didn't return.
"""
import asyncio
import gzip
import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import imdb_dataset
import main


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        return _FakeResponse(self._content)


def _make_gz_tsv(rows: list[tuple[str, str, str]]) -> bytes:
    lines = ["tconst\taverageRating\tnumVotes"] + [
        "\t".join(r) for r in rows
    ]
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    return gzip.compress(raw)


class ImdbDatasetDisabledByDefaultTests(unittest.TestCase):
    def test_disabled_lookup_is_always_none(self):
        with patch.object(imdb_dataset, "IMDB_DATASET_ENABLED", False):
            self.assertIsNone(imdb_dataset.get_rating("tt0903747"))

    def test_disabled_status_reports_disabled(self):
        with patch.object(imdb_dataset, "IMDB_DATASET_ENABLED", False):
            self.assertFalse(imdb_dataset.is_enabled())
            self.assertEqual(imdb_dataset.row_count(), 0)


class ImdbDatasetLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "imdb_ratings.db")
        self._patches = [
            patch.object(imdb_dataset, "IMDB_DATASET_ENABLED", True),
            patch.object(imdb_dataset, "IMDB_DATASET_PATH", self.db_path),
            patch.object(imdb_dataset, "IMDB_DATASET_MIN_VOTES", 10),
        ]
        for p in self._patches:
            p.start()
        imdb_dataset._local.conn = None

    def tearDown(self):
        for p in self._patches:
            p.stop()
        imdb_dataset._local.conn = None

    def _seed(self, rows: list[tuple[str, float, int]]):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE imdb_ratings (tconst TEXT PRIMARY KEY, rating REAL NOT NULL, votes INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO imdb_ratings (tconst, rating, votes) VALUES (?, ?, ?)", rows
        )
        conn.commit()
        conn.close()

    def test_known_id_above_min_votes_returns_rating(self):
        self._seed([("tt0903747", 9.0, 2_000_000)])
        self.assertEqual(imdb_dataset.get_rating("tt0903747"), 9.0)

    def test_unknown_id_returns_none(self):
        self._seed([("tt0903747", 9.0, 2_000_000)])
        self.assertIsNone(imdb_dataset.get_rating("tt9999999"))

    def test_below_min_votes_is_filtered_out(self):
        self._seed([("tt0000001", 10.0, 3)])
        self.assertIsNone(imdb_dataset.get_rating("tt0000001"))

    def test_blank_id_returns_none_without_querying(self):
        self._seed([("tt0903747", 9.0, 2_000_000)])
        self.assertIsNone(imdb_dataset.get_rating(""))
        self.assertIsNone(imdb_dataset.get_rating(None))


class ImdbDatasetRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "imdb_ratings.db")
        self._patches = [
            patch.object(imdb_dataset, "IMDB_DATASET_ENABLED", True),
            patch.object(imdb_dataset, "IMDB_DATASET_PATH", self.db_path),
            patch.object(imdb_dataset, "IMDB_DATASET_MIN_VOTES", 10),
        ]
        for p in self._patches:
            p.start()
        imdb_dataset._local.conn = None

    def tearDown(self):
        for p in self._patches:
            p.stop()
        imdb_dataset._local.conn = None

    def test_refresh_parses_and_loads_rows(self):
        gz = _make_gz_tsv([
            ("tt0903747", "9.0", "2000000"),
            ("tt0000001", "5.5", "42"),
        ])
        client = _FakeClient(gz)
        count = asyncio.run(imdb_dataset.refresh_dataset(client))
        self.assertEqual(count, 2)
        self.assertEqual(imdb_dataset.get_rating("tt0903747"), 9.0)
        self.assertEqual(imdb_dataset.get_rating("tt0000001"), 5.5)
        self.assertEqual(imdb_dataset.row_count(), 2)

    def test_malformed_rows_are_skipped_not_fatal(self):
        raw = b"tconst\taverageRating\tnumVotes\ntt0903747\t9.0\t2000000\nbad-row-only-one-field\n"
        gz = gzip.compress(raw)
        client = _FakeClient(gz)
        count = asyncio.run(imdb_dataset.refresh_dataset(client))
        self.assertEqual(count, 1)

    def test_refresh_is_a_noop_when_disabled(self):
        with patch.object(imdb_dataset, "IMDB_DATASET_ENABLED", False):
            client = _FakeClient(_make_gz_tsv([("tt0903747", "9.0", "2000000")]))
            count = asyncio.run(imdb_dataset.refresh_dataset(client))
            self.assertEqual(count, 0)
            self.assertEqual(client.calls, 0)

    def test_download_failure_returns_zero_without_raising(self):
        class _FailingClient:
            async def get(self, url, **kwargs):
                raise ConnectionError("boom")

        count = asyncio.run(imdb_dataset.refresh_dataset(_FailingClient()))
        self.assertEqual(count, 0)
        self.assertIsNotNone(imdb_dataset._last_refresh_error)


class RequestConfigImdbSourceTests(unittest.TestCase):
    def test_default_source_is_mdblist(self):
        rcfg = main.build_request_config({})
        self.assertEqual(rcfg.imdb_rating_source, "mdblist")

    def test_dataset_can_be_selected_via_query_param(self):
        rcfg = main.build_request_config({"imdb_rating_source": "dataset"})
        self.assertEqual(rcfg.imdb_rating_source, "dataset")

    def test_invalid_value_falls_back_to_default(self):
        rcfg = main.build_request_config({"imdb_rating_source": "nonsense"})
        self.assertEqual(rcfg.imdb_rating_source, "mdblist")

    def test_value_is_case_insensitive(self):
        rcfg = main.build_request_config({"imdb_rating_source": "DATASET"})
        self.assertEqual(rcfg.imdb_rating_source, "dataset")


class MergeImdbDatasetRatingTests(unittest.TestCase):
    def _rcfg(self, source="dataset"):
        rcfg = main.build_request_config({})
        rcfg.imdb_rating_source = source
        return rcfg

    def test_noop_when_source_is_mdblist(self):
        with patch.object(imdb_dataset, "get_rating", return_value=9.0):
            out = main._merge_imdb_dataset_rating({"tomatoes": 80}, "tt0903747", self._rcfg("mdblist"))
        self.assertEqual(out, {"tomatoes": 80})

    def test_noop_when_no_imdb_id(self):
        with patch.object(imdb_dataset, "get_rating", return_value=9.0) as mock_get:
            out = main._merge_imdb_dataset_rating({"tomatoes": 80}, None, self._rcfg("dataset"))
        mock_get.assert_not_called()
        self.assertEqual(out, {"tomatoes": 80})

    def test_noop_when_dataset_has_no_value(self):
        with patch.object(imdb_dataset, "get_rating", return_value=None):
            out = main._merge_imdb_dataset_rating({"tomatoes": 80}, "tt0903747", self._rcfg("dataset"))
        self.assertEqual(out, {"tomatoes": 80})

    def test_dataset_value_is_added_when_absent_from_mdblist(self):
        with patch.object(imdb_dataset, "get_rating", return_value=9.0):
            out = main._merge_imdb_dataset_rating({"tomatoes": 80}, "tt0903747", self._rcfg("dataset"))
        self.assertEqual(out, {"tomatoes": 80, "imdb": 9.0})

    def test_dataset_value_overrides_an_mdblist_imdb_value(self):
        with patch.object(imdb_dataset, "get_rating", return_value=9.0):
            out = main._merge_imdb_dataset_rating({"imdb": 7.0}, "tt0903747", self._rcfg("dataset"))
        self.assertEqual(out, {"imdb": 9.0})

    def test_non_dict_ratings_passed_through_untouched(self):
        # The rating-fetch-failed / string-sentinel path (FETCH_FAILED etc).
        out = main._merge_imdb_dataset_rating("N/A", "tt0903747", self._rcfg("dataset"))
        self.assertEqual(out, "N/A")

    def test_works_end_to_end_with_calculate_weighted_score(self):
        from ratings import calculate_weighted_score
        with patch.object(imdb_dataset, "get_rating", return_value=9.0):
            merged = main._merge_imdb_dataset_rating({}, "tt0903747", self._rcfg("dataset"))
        score = calculate_weighted_score(merged, {"imdb": 1.0})
        self.assertEqual(score, 90)  # (9.0 / 10) * 100


class MergeDirectTmdbRatingTests(unittest.TestCase):
    def _rcfg(self, source="direct"):
        rcfg = main.build_request_config({})
        rcfg.tmdb_rating_source = source
        return rcfg

    def test_noop_when_source_is_mdblist(self):
        out = main._merge_direct_tmdb_rating(
            {"tomatoes": 80}, {"vote_average": 8.2}, self._rcfg("mdblist")
        )
        self.assertEqual(out, {"tomatoes": 80})

    def test_noop_when_tmdb_data_missing_vote_average(self):
        out = main._merge_direct_tmdb_rating({"tomatoes": 80}, {}, self._rcfg("direct"))
        self.assertEqual(out, {"tomatoes": 80})

    def test_noop_when_tmdb_data_is_none(self):
        out = main._merge_direct_tmdb_rating({"tomatoes": 80}, None, self._rcfg("direct"))
        self.assertEqual(out, {"tomatoes": 80})

    def test_vote_average_is_added_rescaled_to_0_100(self):
        out = main._merge_direct_tmdb_rating({}, {"vote_average": 8.2}, self._rcfg("direct"))
        self.assertEqual(out, {"tmdb": 82.0})

    def test_direct_value_overrides_an_mdblist_tmdb_value(self):
        out = main._merge_direct_tmdb_rating(
            {"tmdb": 55}, {"vote_average": 8.2}, self._rcfg("direct")
        )
        self.assertEqual(out, {"tmdb": 82.0})

    def test_zero_vote_average_is_treated_as_unrated_not_zero_score(self):
        # An unreleased or brand-new title reports vote_average: 0 with no
        # votes yet — showing a 0/100 score would be actively misleading.
        out = main._merge_direct_tmdb_rating({}, {"vote_average": 0}, self._rcfg("direct"))
        self.assertEqual(out, {})

    def test_non_dict_ratings_passed_through_untouched(self):
        out = main._merge_direct_tmdb_rating("N/A", {"vote_average": 8.2}, self._rcfg("direct"))
        self.assertEqual(out, "N/A")

    def test_works_end_to_end_with_calculate_weighted_score(self):
        from ratings import calculate_weighted_score
        merged = main._merge_direct_tmdb_rating({}, {"vote_average": 8.2}, self._rcfg("direct"))
        score = calculate_weighted_score(merged, {"tmdb": 1.0})
        self.assertEqual(score, 82)


class TmdbRatingSourceRequestConfigTests(unittest.TestCase):
    def test_default_source_is_mdblist(self):
        rcfg = main.build_request_config({})
        self.assertEqual(rcfg.tmdb_rating_source, "mdblist")

    def test_direct_can_be_selected_via_query_param(self):
        rcfg = main.build_request_config({"tmdb_rating_source": "direct"})
        self.assertEqual(rcfg.tmdb_rating_source, "direct")

    def test_invalid_value_falls_back_to_default(self):
        rcfg = main.build_request_config({"tmdb_rating_source": "nonsense"})
        self.assertEqual(rcfg.tmdb_rating_source, "mdblist")


if __name__ == "__main__":
    unittest.main()
