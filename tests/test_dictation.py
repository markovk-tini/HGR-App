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

    def test_brand_names_are_cased(self) -> None:
        cases = (
            ("open youtube", "Open YouTube"),
            ("push to github", "Push to GitHub"),
            ("post on slack", "Post on Slack"),
            ("open figma", "Open Figma"),
            ("log a jira ticket", "Log a Jira ticket"),
            ("join the zoom call", "Join the Zoom call"),
            ("open ios notes", "Open iOS notes"),
            ("running on iphone", "Running on iPhone"),
            ("talk to openai", "Talk to OpenAI"),
            ("a claude response", "A Claude response"),
            ("push a docker image", "Push a Docker image"),
            ("my nvidia gpu", "My NVIDIA gpu"),
            ("call the api", "Call the API"),
            ("parse this json", "Parse this JSON"),
            ("write python code", "Write Python code"),
        )
        for spoken, expected in cases:
            with self.subTest(spoken=spoken):
                processor = DictationProcessor()
                result = processor.ingest(spoken)
                self.assertEqual(result.text_to_insert, expected)

    def test_symbol_dictation(self) -> None:
        cases = (
            ("contact at sign gmail", "Contact @ gmail"),
            ("price is dollar sign ten", "Price is $ ten"),
            ("a ampersand b", "A & b"),
            ("use forward slash path", "Use / path"),
            ("set x equals sign five", "Set x = five"),
            ("open bracket key close bracket", "[Key]"),
        )
        for spoken, expected in cases:
            with self.subTest(spoken=spoken):
                processor = DictationProcessor()
                result = processor.ingest(spoken)
                self.assertEqual(result.text_to_insert, expected)

    def test_new_line_vs_new_paragraph(self) -> None:
        processor = DictationProcessor()
        update = processor.ingest("first line new line second line new paragraph third")
        self.assertEqual(update.text_to_insert, "First line\nSecond line\n\nThird")

    def test_ellipsis_dictation(self) -> None:
        processor = DictationProcessor()
        update = processor.ingest("hmm ellipsis interesting")
        self.assertEqual(update.text_to_insert, "Hmm... Interesting")

    def test_i_pronoun_capitalization(self) -> None:
        processor = DictationProcessor()
        update = processor.ingest("tomorrow i will go")
        self.assertEqual(update.text_to_insert, "Tomorrow I will go")

    def test_i_contractions_expanded(self) -> None:
        processor = DictationProcessor()
        update = processor.ingest("im ready and ive tried and ill go")
        self.assertEqual(update.text_to_insert, "I'm ready and I've tried and I'll go")

    def test_question_and_exclamation_end_sentence_casing(self) -> None:
        processor = DictationProcessor()
        update = processor.ingest("ready question mark yes exclamation point lets go")
        self.assertEqual(update.text_to_insert, "Ready? Yes! Lets go")

    def test_continuation_capitalizes_after_sentence_end(self) -> None:
        processor = DictationProcessor()
        processor.ingest("hello period")
        update = processor.ingest("world")
        self.assertEqual(update.text_to_insert, " World")
        self.assertEqual(update.full_text, "Hello. World")
