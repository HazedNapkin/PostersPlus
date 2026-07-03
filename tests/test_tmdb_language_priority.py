import unittest

from i18n import load_languages, translate_genre, translate_sash
from tmdb import _image_language_keys, _image_matches_language, image_language_order


class ImageLanguageOrderTests(unittest.TestCase):
    def test_native_content_keeps_native_language_first(self):
        self.assertEqual(
            image_language_order("fr", "fr", "native_if_original_english"),
            ["fr", "en"],
        )

    def test_foreign_content_prefers_english_then_original(self):
        for original_language in ("ko", "ja", "ru", "zh"):
            with self.subTest(original_language=original_language):
                self.assertEqual(
                    image_language_order(
                        "fr", original_language, "native_if_original_english"
                    ),
                    ["en", original_language],
                )

    def test_existing_priorities_are_unchanged(self):
        self.assertEqual(
            image_language_order("fr", "ja", "native_original"),
            ["fr", "ja"],
        )
        self.assertEqual(
            image_language_order("fr", "ja", "original_native"),
            ["ja", "fr"],
        )
        self.assertEqual(
            image_language_order("fr", "ja", "native_text"),
            ["fr"],
        )

    def test_duplicate_languages_are_only_tried_once(self):
        self.assertEqual(
            image_language_order("en", "en", "native_if_original_english"),
            ["en"],
        )

    def test_region_qualified_french_does_not_fall_back_to_bare_french_art(self):
        self.assertEqual(
            image_language_order("fr-fr", "en", "native_original"),
            ["fr-fr", "en"],
        )
        self.assertNotIn(
            "fr",
            image_language_order("fr-fr", "en", "native_original"),
        )

    def test_tmdb_language_region_images_match_locale_requests(self):
        france = {"iso_639_1": "fr", "iso_3166_1": "FR"}
        canada = {"iso_639_1": "fr", "iso_3166_1": "CA"}
        generic = {"iso_639_1": "fr", "iso_3166_1": None}

        self.assertEqual(_image_language_keys(france), ["fr-fr", "fr"])
        self.assertTrue(_image_matches_language(france, "fr-fr"))
        self.assertFalse(_image_matches_language(canada, "fr-fr"))
        self.assertFalse(_image_matches_language(generic, "fr-fr"))
        self.assertTrue(_image_matches_language(canada, "fr"))

    def test_region_qualified_language_uses_base_translation_table(self):
        load_languages()
        self.assertEqual(translate_genre("Drama", "fr-FR"), "Drame")
        self.assertEqual(translate_sash("Season Finale", "fr-FR"), "Finale saison")


if __name__ == "__main__":
    unittest.main()
