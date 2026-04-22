from __future__ import annotations

import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path

from hgr.debug.desktop_controller import DesktopAppEntry
from hgr.voice.command_processor import VoiceProfileStore
from hgr.voice.training_data import VoiceCommandDatasetBuilder


class _FakeDesktopController:
    def application_catalog_snapshot(self) -> list[DesktopAppEntry]:
        return [
            DesktopAppEntry(
                display_name="Discord",
                normalized_name="discord",
                target="discord",
                source="known",
                aliases=("discord",),
                category="chat",
            ),
            DesktopAppEntry(
                display_name="Visual Studio Code",
                normalized_name="visual studio code",
                target="code",
                source="known",
                aliases=("visual studio code", "visual studio", "vscode"),
                category="editor",
            ),
        ]


class VoiceTrainingDataTest(unittest.TestCase):
    @contextlib.contextmanager
    def _temp_dir(self):
        root = Path.home() / "Documents"
        tmp_dir = Path(tempfile.mkdtemp(dir=str(root)))
        try:
            yield tmp_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_build_bundle_generates_training_eval_and_corrections(self) -> None:
        with self._temp_dir() as tmp_dir:
            store = VoiceProfileStore(path=tmp_dir / "voice_profile.json")
            store.record_correction(utterance="open discord please", app_name="system", action="open_app", query="Discord")
            builder = VoiceCommandDatasetBuilder(desktop_controller=_FakeDesktopController(), profile_store=store)

            bundle = builder.build_bundle(max_generic_apps=2)

        self.assertGreater(len(bundle.training_examples), 200)
        self.assertGreaterEqual(len(bundle.evaluation_examples), 6)
        self.assertEqual(len(bundle.correction_examples), 1)

    def test_export_bundle_writes_jsonl_files(self) -> None:
        with self._temp_dir() as tmp_dir:
            builder = VoiceCommandDatasetBuilder(desktop_controller=_FakeDesktopController(), profile_store=VoiceProfileStore(path=tmp_dir / "voice_profile.json"))

            paths = builder.export_bundle(tmp_dir / "voice_bundle", max_generic_apps=2)
            self.assertTrue(paths["train"].exists())
            self.assertTrue(paths["eval"].exists())
            self.assertTrue(paths["corrections"].exists())
            self.assertTrue(paths["summary"].exists())
