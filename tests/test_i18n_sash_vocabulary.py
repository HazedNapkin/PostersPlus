import json
import unittest
from pathlib import Path

from discovery import FESTIVAL_KEYWORDS
from i18n import load_languages, translate_sash


LANGUAGE_DIR = Path(__file__).resolve().parents[1] / "languages"

RELEASE_STATUS_LABELS = {
    "Physical",
    "Streaming",
    "Cinema",
    "Production",
    "Airing",
    "Ended",
    "Cancelled",
}

FESTIVAL_LABELS = set(FESTIVAL_KEYWORDS.values())

FIXED_SASH_LABELS = RELEASE_STATUS_LABELS | FESTIVAL_LABELS


def _load_language(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


class FixedSashVocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_languages()

    def test_canonical_locale_documents_all_fixed_sash_labels(self):
        english = _load_language(LANGUAGE_DIR / "en.json")
        self.assertTrue(FIXED_SASH_LABELS <= english["sashLabels"].keys())

    def test_every_shipped_translation_includes_all_fixed_sash_labels(self):
        for path in LANGUAGE_DIR.glob("*.json"):
            if path.name == "en.json":
                continue
            with self.subTest(language=path.stem):
                language = _load_language(path)
                self.assertTrue(
                    FIXED_SASH_LABELS <= language["sashLabels"].keys(),
                    f"{path.name} is missing fixed sash translations",
                )

    def test_release_status_translation_uses_the_new_locale_entries(self):
        self.assertEqual(translate_sash("Cinema", "fr-FR"), "Au cinéma")
        self.assertEqual(translate_sash("Airing", "es-MX"), "En emisión")
        self.assertEqual(translate_sash("Physical", "pt-BR"), "Mídia Física")

    def test_festival_translation_uses_the_new_locale_entries(self):
        self.assertEqual(translate_sash("Golden Lion", "it-IT"), "Leone d'oro")
        self.assertEqual(translate_sash("Golden Bear", "es-ES"), "Oso de Oro")
        self.assertEqual(translate_sash("Tiger Award", "pt-PT"), "Prémio Tigre")


if __name__ == "__main__":
    unittest.main()
