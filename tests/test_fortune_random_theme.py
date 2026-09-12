import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from bot.fortune import THEMES, build_fortune


class RandomThemeTests(unittest.TestCase):
    def test_consecutive_random_themes_change_without_reroll(self):
        rows = [build_fortune(99881, '测试', date(2026,9,5)) for _ in range(30)]
        for a,b in zip(rows,rows[1:],strict=False):
            self.assertNotEqual(a.theme,b.theme)
            self.assertEqual(a,replace(b,theme=a.theme,image_key=a.image_key))
            self.assertTrue(b.image_key.startswith('img/'+b.theme+'/'))

    def test_explicit_theme_is_preserved(self):
        for theme in THEMES:
            a=build_fortune(99882,'测试',date(2026,9,5),theme=theme)
            self.assertEqual(a,build_fortune(99882,'测试',date(2026,9,5),theme=theme))

    def test_random_uses_unseeded_choice_excluding_previous(self):
        with patch('bot.fortune.random.choice',side_effect=lambda choices: choices[0]) as choice:
            a=build_fortune(99883,'测试',date(2026,9,5))
            b=build_fortune(99883,'测试',date(2026,9,5))
            self.assertEqual(choice.call_count,2)
            self.assertNotIn(a.theme,choice.call_args.args[0])
            self.assertNotEqual(a.theme,b.theme)
