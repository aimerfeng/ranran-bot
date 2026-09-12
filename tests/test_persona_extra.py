import tempfile
import unittest
from pathlib import Path

from bot.persona import (
    DEEPSEEK_PERSONA,
    EXTRA_PERSONA_LIMIT,
    compose_system,
    load_extra_persona,
)


class ExtraPersonaTests(unittest.TestCase):
    def test_missing_file_and_none_are_silent(self):
        self.assertEqual(load_extra_persona(None), "")
        self.assertEqual(load_extra_persona(Path("no/such/persona.txt")), "")

    def test_reads_utf8_strips_bom_and_blank(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "persona.txt"
            path.write_text("\ufeff\n  设定内容 A\n设定内容 B\n\n", encoding="utf-8")
            text = load_extra_persona(path)
        self.assertEqual(text, "设定内容 A\n设定内容 B")

    def test_blank_file_is_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "persona.txt"
            path.write_text("   \n\n", encoding="utf-8")
            self.assertEqual(load_extra_persona(path), "")

    def test_overlong_file_is_truncated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "persona.txt"
            path.write_text("长" * (EXTRA_PERSONA_LIMIT + 500), encoding="utf-8")
            text = load_extra_persona(path)
        self.assertLessEqual(len(text), EXTRA_PERSONA_LIMIT + 20)
        self.assertTrue(text.endswith("（外部人设过长，已截断）"))

    def test_compose_puts_extra_last(self):
        self.assertEqual(compose_system("基础", ""), "基础")
        composed = compose_system("基础人设\n规则", "外部人设")
        self.assertTrue(composed.startswith("基础人设"))
        self.assertTrue(composed.endswith("外部人设"))
        self.assertIn("\n\n", composed)

    def test_builtin_persona_still_mentions_search(self):
        self.assertIn("联网搜索", DEEPSEEK_PERSONA)
        self.assertIn("[搜索:", DEEPSEEK_PERSONA)


if __name__ == "__main__":
    unittest.main()