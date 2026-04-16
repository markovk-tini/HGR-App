from __future__ import annotations

import unittest

from hgr.voice.dictation import DictationProcessor


class DictationProcessorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = DictationProcessor()

    def test_ingest_applies_punctuation_and_sentence_casing(self) -> None:
        update = self.processor.ingest("hello comma world period i am here")

        self.assertEqual(update.text_to_insert, "Hello, world. I am here")
        self.assertEqual(update.full_text, "Hello, world. I am here")

    def test_ingest_formats_new_paragraphs_and_product_names(self) -> None:
        update = self.processor.ingest("open chat gpt new paragraph kicad and vscode")

        self.assertEqual(update.text_to_insert, "Open ChatGPT\n\nKiCad and VS Code")
        self.assertEqual(update.display_text, "Open ChatGPT\n\nKiCad and VS Code")

    def test_ingest_appends_spacing_between_chunks(self) -> None:
        self.processor.ingest("hello")
        update = self.processor.ingest("world period")

        self.assertEqual(update.text_to_insert, " world.")
        self.assertEqual(update.full_text, "Hello world.")

    def test_preview_respects_existing_text_context(self) -> None:
        self.processor.ingest("hello period")

        preview = self.processor.preview("chat gpt")

        self.assertEqual(preview, " ChatGPT")
