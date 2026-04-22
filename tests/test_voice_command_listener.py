from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hgr.debug.voice_command_listener import VoiceCommandListener


class VoiceCommandListenerTest(unittest.TestCase):
    def _temp_dir(self) -> Path:
        return Path(tempfile.mkdtemp(dir="C:\\HGR App v1.0.0"))

    def test_select_phrase_prefers_more_complete_phrase(self) -> None:
        listener = VoiceCommandListener()
        phrase = listener._select_phrase(
            [
                {"text": "play", "confidence": 0.82},
                {"text": "play numb by linkin park on spotify", "confidence": 0.62},
            ]
        )

        self.assertEqual(phrase, "play numb by linkin park on spotify")

    def test_select_phrase_prefers_longer_file_path_command(self) -> None:
        listener = VoiceCommandListener()
        phrase = listener._select_phrase(
            [
                {"text": "open report", "confidence": 0.84},
                {"text": "open report pdf in cs 579 folder in documents", "confidence": 0.66},
            ]
        )

        self.assertEqual(phrase, "open report pdf in cs 579 folder in documents")

    def test_parse_payload_reads_last_json_line(self) -> None:
        listener = VoiceCommandListener()
        payload = listener._parse_payload("debug line\n{\"phrases\":[{\"text\":\"play numb\",\"confidence\":0.7}],\"error\":null}\n")

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn("phrases", payload)

    def test_normalize_text_applies_domain_corrections(self) -> None:
        listener = VoiceCommandListener()

        normalized = listener._normalize_text("Play Back In Black by AC DC on Google Chrome")

        self.assertEqual(normalized, "play back in black by ac/dc on chrome")

    def test_build_initial_prompt_includes_app_hints(self) -> None:
        listener = VoiceCommandListener()
        listener.set_app_hints(["KiCad", "Visual Studio Code"])

        prompt = listener._build_initial_prompt()

        self.assertIn("kicad", prompt)
        self.assertIn("visual studio code", prompt)
        self.assertIn("nested folders", prompt)

    def test_build_initial_prompt_dictation_mentions_long_form_writing(self) -> None:
        listener = VoiceCommandListener()

        prompt = listener._build_initial_prompt(transcript_mode="dictation")

        self.assertIn("emails", prompt.lower())
        self.assertIn("new paragraph", prompt.lower())

    def test_build_initial_prompt_save_prompt_mentions_default_and_folders(self) -> None:
        listener = VoiceCommandListener()

        prompt = listener._build_initial_prompt(transcript_mode="save_prompt")

        self.assertIn("default", prompt.lower())
        self.assertIn("documents", prompt.lower())
        self.assertIn("absolute windows path", prompt.lower())

    def test_normalize_text_preserves_case_for_dictation_mode(self) -> None:
        listener = VoiceCommandListener()

        normalized = listener._normalize_text("ChatGPT in KiCad", transcript_mode="dictation")

        self.assertEqual(normalized, "ChatGPT in KiCad")

    def test_resolve_whisper_cpp_command_prefers_local_build(self) -> None:
        listener = VoiceCommandListener()
        listener._whisper_cpp_root = Path("C:/fake/whisper.cpp")
        command_path = listener._whisper_cpp_root / "build" / "bin" / "Release" / "whisper-cli.exe"
        with patch("shutil.which", return_value=None):
            with patch.object(Path, "exists", autospec=True, side_effect=lambda path: str(path) == str(command_path)):
                resolved = listener._resolve_whisper_cpp_command()

        self.assertEqual(resolved, (str(command_path),))

    def test_resolve_whisper_cpp_model_prefers_medium_en_and_skips_test_files(self) -> None:
        listener = VoiceCommandListener()
        listener._model_root = Path("C:/fake/models")
        listener._whisper_cpp_root = Path("C:/fake/whisper.cpp")
        medium_path = listener._whisper_cpp_root / "models" / "ggml-medium.en.bin"
        with patch.object(Path, "exists", autospec=True, side_effect=lambda path: str(path) == str(medium_path)):
            resolved = listener._resolve_whisper_cpp_model_path()

        self.assertEqual(resolved, medium_path)

    def test_resolve_whisper_cpp_vad_model_finds_downloaded_vad(self) -> None:
        listener = VoiceCommandListener()
        listener._model_root = Path("C:/fake/models")
        listener._whisper_cpp_root = Path("C:/fake/whisper.cpp")
        vad_path = listener._model_root / "ggml-silero-v5.1.2.bin"
        with patch.object(Path, "exists", autospec=True, side_effect=lambda path: str(path) == str(vad_path)):
            resolved = listener._resolve_whisper_cpp_vad_model_path()

        self.assertEqual(resolved, vad_path)
