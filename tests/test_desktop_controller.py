from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hgr.debug.desktop_controller import DesktopController


class DesktopControllerTest(unittest.TestCase):
    def _temp_dir(self) -> Path:
        return Path(tempfile.mkdtemp())

    def test_can_resolve_known_app_aliases(self) -> None:
        controller = DesktopController(outlook_paths=())

        self.assertTrue(controller.can_resolve_application("steam"))
        self.assertTrue(controller.can_resolve_application("visual studios"))

    def test_rank_applications_in_text_prefers_known_alias(self) -> None:
        controller = DesktopController(outlook_paths=())

        ranked = controller.rank_applications_in_text("open a visual stdios window please")

        self.assertTrue(ranked)
        top_entry, score, matched_alias = ranked[0]
        self.assertEqual(top_entry.display_name, "visual studio code")
        self.assertGreaterEqual(score, 0.82)
        self.assertIn(matched_alias, {"visual studio", "visual studio code"})

    def test_rank_applications_in_text_ignores_generic_apps_alias_noise(self) -> None:
        controller = DesktopController(outlook_paths=())

        ranked = controller.rank_applications_in_text("open kkad app")

        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0].display_name, "kicad")

    def test_open_named_application_prefers_known_display_name(self) -> None:
        controller = DesktopController(outlook_paths=())

        with patch.object(controller, "_launch_path_or_command", return_value=True) as launch_mock:
            self.assertTrue(controller.open_named_application("visual studios"))

        launch_mock.assert_called_once()
        self.assertEqual(controller.message, "opened app: visual studio code")

    def test_open_outlook_folder_uses_classic_select_when_available(self) -> None:
        classic_path = Path("C:/Program Files/Microsoft Office/root/Office16/OUTLOOK.EXE")
        controller = DesktopController(outlook_paths=())

        with patch.object(controller, "_classic_outlook_path", return_value=classic_path):
            with patch("subprocess.Popen") as popen_mock:
                self.assertTrue(controller.open_outlook_folder("scent"))

        popen_mock.assert_called_once_with([str(classic_path), "/select", "outlook:Sent Items"], shell=False)
        self.assertEqual(controller.message, "opened outlook folder: Sent Items")

    def test_open_outlook_folder_reports_partial_fallback_when_only_opening_outlook(self) -> None:
        controller = DesktopController(outlook_paths=())

        with patch.object(controller, "open_outlook", return_value=True):
            self.assertFalse(controller.open_outlook_folder("scent"))

        self.assertEqual(controller.message, "opened outlook, but could not select Sent Items")

    def test_open_named_file_can_resolve_plain_filename_without_folder_hint(self) -> None:
        root = self._temp_dir()
        try:
            target = root / "Budget Report.pdf"
            target.write_text("budget", encoding="utf-8")
            controller = DesktopController(outlook_paths=())

            with patch.object(controller, "_file_search_roots", return_value=[root]):
                with patch.object(controller, "_launch_target", return_value=True) as launch_mock:
                    self.assertTrue(controller.open_named_file("budget report pdf"))

            launch_mock.assert_called_once_with(str(target))
            self.assertEqual(controller.message, "opened file: Budget Report.pdf")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_open_named_file_prefers_exact_extension_match(self) -> None:
        root = self._temp_dir()
        try:
            pdf_target = root / "Final Presentation.pdf"
            ppt_target = root / "Final Presentation.pptx"
            pdf_target.write_text("pdf", encoding="utf-8")
            ppt_target.write_text("ppt", encoding="utf-8")
            controller = DesktopController(outlook_paths=())

            with patch.object(controller, "_file_search_roots", return_value=[root]):
                with patch.object(controller, "_launch_target", return_value=True) as launch_mock:
                    self.assertTrue(controller.open_named_file("final presentation pdf"))

            launch_mock.assert_called_once_with(str(pdf_target))
            self.assertEqual(controller.message, "opened file: Final Presentation.pdf")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_open_named_file_prefers_indexed_results_before_fallback_scan(self) -> None:
        root = self._temp_dir()
        try:
            target = root / "Test Cases" / "notes.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("notes", encoding="utf-8")
            controller = DesktopController(outlook_paths=())

            with patch.object(controller, "_file_search_roots", return_value=[root]):
                with patch.object(controller, "_query_indexed_paths", return_value=[target]):
                    with patch.object(controller, "_scan_file_candidates", return_value=[]) as scan_mock:
                        with patch.object(controller, "_launch_target", return_value=True) as launch_mock:
                            self.assertTrue(controller.open_named_file("notes txt"))

            launch_mock.assert_called_once_with(str(target))
            scan_mock.assert_not_called()
            self.assertEqual(controller.message, "opened file: notes.txt")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_resolve_named_folder_returns_known_documents_path(self) -> None:
        controller = DesktopController(outlook_paths=())

        resolved, ambiguous = controller.resolve_named_folder("documents folder")

        self.assertFalse(ambiguous)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name.lower(), "documents")
