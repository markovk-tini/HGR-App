import unittest

from hgr.debug.text_input_controller import _compute_replace_edit


class ComputeReplaceEditTest(unittest.TestCase):
    def test_no_change_returns_empty_edit(self) -> None:
        self.assertEqual(_compute_replace_edit("hello", "hello"), (0, 0, "", 0))

    def test_append_only_types_new_suffix(self) -> None:
        self.assertEqual(_compute_replace_edit("hello", "hello world"), (0, 0, " world", 0))

    def test_tail_typo_only_inserts_missing_character(self) -> None:
        self.assertEqual(_compute_replace_edit("dictate worl", "dictate world"), (0, 0, "d", 0))

    def test_middle_replacement_preserves_unchanged_suffix(self) -> None:
        left_moves, backspaces, insert_text, right_moves = _compute_replace_edit(
            "open teh file",
            "open the file",
        )
        self.assertEqual(backspaces, 2)
        self.assertEqual(insert_text, "he")
        self.assertEqual(left_moves, len(" file"))
        self.assertEqual(right_moves, len(" file"))

    def test_grammar_style_correction_keeps_tail_in_place(self) -> None:
        left_moves, backspaces, insert_text, right_moves = _compute_replace_edit(
            "their report final",
            "there report final",
        )
        self.assertEqual(backspaces, 2)
        self.assertEqual(insert_text, "re")
        self.assertEqual(left_moves, len(" report final"))
        self.assertEqual(right_moves, len(" report final"))


if __name__ == "__main__":
    unittest.main()
