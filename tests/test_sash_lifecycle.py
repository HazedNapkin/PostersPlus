import unittest
from datetime import date, timedelta

from discovery import (
    DiscoveryMeta,
    extract_discovery_meta,
    is_recently_released_or_available,
    pick_sash,
)


def _iso(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


class SashLifecycleTests(unittest.TestCase):
    def _meta(self, tmdb_data, media_type="tv", **kwargs):
        return extract_discovery_meta(
            tmdb_data=tmdb_data,
            media_type=media_type,
            award_wins=[],
            award_noms=[],
            trending_rank=kwargs.pop("trending_rank", None),
            **kwargs,
        )

    def test_trending_top_40_and_broad_41_to_100_are_separate_slots(self):
        top = self._meta({}, media_type="movie", trending_rank=40)
        broad = self._meta({}, media_type="movie", trending_rank=41)

        self.assertEqual(pick_sash(top, ["trending"]), ("#40 Today", "trending"))
        self.assertIsNone(pick_sash(top, ["trending_broad"]))
        self.assertIsNone(pick_sash(broad, ["trending"]))
        self.assertEqual(pick_sash(broad, ["trending_broad"]), ("#41 Today", "trending"))

    def test_new_season_uses_s2_plus_episode_one(self):
        meta = self._meta({
            "number_of_seasons": 2,
            "number_of_episodes": 11,
            "next_episode": {"season_number": 2, "episode_number": 1, "air_date": _iso(5)},
        })

        self.assertEqual(pick_sash(meta, ["new_season"]), ("New Season", "alert"))
        self.assertIsNone(pick_sash(meta, ["returning"]))

    def test_returning_uses_fresh_non_premiere_episode(self):
        meta = self._meta({
            "number_of_seasons": 3,
            "number_of_episodes": 25,
            "next_episode": {"season_number": 3, "episode_number": 4, "air_date": _iso(3)},
        })

        self.assertEqual(pick_sash(meta, ["returning"]), ("Returning", "alert"))
        self.assertIsNone(pick_sash(meta, ["new_season"]))

    def test_premiere_and_just_added_are_distinct_freshness_slots(self):
        premiere = self._meta({"tmdb_release_date": _iso(-2)}, media_type="tv")
        just_added = self._meta(
            {"tmdb_release_date": _iso(-90)},
            media_type="movie",
            recent_digital_release_date=_iso(-1),
        )

        self.assertEqual(pick_sash(premiere, ["premiere"]), ("Premiere", "alert"))
        self.assertIsNone(pick_sash(premiere, ["just_added"]))
        self.assertEqual(pick_sash(just_added, ["just_added"]), ("Just Added", "alert"))

    def test_season_finale_is_conservative_for_recent_ended_final_season(self):
        meta = self._meta({
            "tmdb_status": "Ended",
            "number_of_seasons": 4,
            "number_of_episodes": 40,
            "last_episode": {"season_number": 4, "episode_number": 10, "air_date": _iso(-1)},
            "seasons": [{"season_number": 4, "episode_count": 10}],
        })

        self.assertEqual(pick_sash(meta, ["season_finale"]), ("Season Finale", "alert"))

    def test_is_recently_released_or_available_within_30_days(self):
        # 10 days ago -> True
        self.assertTrue(is_recently_released_or_available(release_date=_iso(-10)))
        # 30 days ago -> True
        self.assertTrue(is_recently_released_or_available(release_date=_iso(-30)))
        # 31 days ago -> False
        self.assertFalse(is_recently_released_or_available(release_date=_iso(-31)))

    def test_is_recently_released_or_available_via_digital_or_physical(self):
        # Movie released in theaters 100 days ago, but digital release 5 days ago
        self.assertTrue(
            is_recently_released_or_available(
                release_date=_iso(-100),
                digital_release_date=_iso(-5),
            )
        )
        # Via cached movie_release_info
        self.assertTrue(
            is_recently_released_or_available(
                movie_release_info={"digital_latest_date": _iso(-12)}
            )
        )
        self.assertTrue(
            is_recently_released_or_available(
                movie_release_info={"physical_date": _iso(-20)}
            )
        )

    def test_is_recently_released_or_available_via_discovery_meta_flags(self):
        meta_digital = DiscoveryMeta(is_digital_release=True)
        self.assertTrue(is_recently_released_or_available(meta_digital))

        meta_just_added = DiscoveryMeta(is_just_added=True)
        self.assertTrue(is_recently_released_or_available(meta_just_added))

        meta_old = DiscoveryMeta()
        self.assertFalse(is_recently_released_or_available(meta_old, release_date=_iso(-100)))


if __name__ == "__main__":
    unittest.main()

