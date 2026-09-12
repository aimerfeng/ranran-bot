import importlib.util
import sys
import unittest
from collections import Counter
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image

from bot.fortune import BANDS, HIDDEN_SIGNS, assets_ready, build_fortune, render_card
from bot.fortune_poetry import draw_verse, poetry_catalog, verse_columns

ASSETS_HINT = "素材未下载：先运行 python scripts/fetch_fortune_assets.py"
BASELINE_HINT = "缺少升级基线 artifacts/poetic-fortune/baseline/fortune.py（开发产物，未入库）"


class PoetryTests(unittest.TestCase):
    def test_four_hundred_unique_curated_verses(self):
        data=poetry_catalog();all_rows=[]
        self.assertEqual(set(data['ordinary']),{b for _,b in BANDS})
        for _band,rows in data['ordinary'].items():
            self.assertEqual(len(rows),48)
            self.assertEqual(Counter(r['imagery'] for r in rows),{'山':12,'水':12,'花':12,'月':12})
            all_rows.extend(rows)
        for sign in HIDDEN_SIGNS:
            rows=data['hidden'][sign.key];self.assertEqual(len(rows),8);all_rows.extend(rows)
        self.assertEqual(len(all_rows),400);self.assertEqual(len({r['verse'] for r in all_rows}),400)
        for row in all_rows:
            self.assertRegex(row['verse'],r'^[\u4e00-\u9fff]{7}，[\u4e00-\u9fff]{7}$')
            self.assertEqual(tuple(map(len,verse_columns(row['verse']))),(7,7))
    def test_daily_and_theme_stability(self):
        for uid in range(100):
            a=build_fortune(uid,'甲',date(2026,9,5));b=build_fortune(uid,'乙',date(2026,9,5),theme='genshin')
            self.assertEqual((a.advice,a.display_advice,a.display_imagery),(b.advice,b.display_advice,b.display_imagery))
    def test_same_band_variety(self):
        poems={draw_verse(uid,date(2026,9,5),band='大吉').verse for uid in range(200)}
        self.assertGreaterEqual(len(poems),40)
    def test_hidden_variants(self):
        for sign in HIDDEN_SIGNS:
            choices={draw_verse(uid,date(2026,9,5),hidden_key=sign.key).verse for uid in range(150)}
            self.assertEqual(len(choices),8)
    @unittest.skipUnless(assets_ready(), ASSETS_HINT)
    def test_all_seven_bands_render(self):
        base=build_fortune(42,'星野',date(2026,9,5))
        for band,rows in poetry_catalog()['ordinary'].items():
            for row in rows[::12]:
                f=replace(base,hidden=None,band=band,advice=row['verse'],poetry_imagery=row['imagery'])
                im=Image.open(BytesIO(render_card(f)));self.assertEqual(im.size,(960,960));im.verify()
    def test_prompt_identifies_original_poetry(self):
        from bot.fortune_reading import FORTUNE_READING_SYSTEM, build_reading_prompt
        f=build_fortune(42,'星野',date(2026,9,5));prompt=build_reading_prompt(f)
        self.assertIn('原创拟古',prompt);self.assertIn(f.display_advice,prompt)
        self.assertIn('诗人',FORTUNE_READING_SYSTEM)
    @unittest.skipUnless(Path('artifacts/poetic-fortune/baseline/fortune.py').is_file(), BASELINE_HINT)
    def test_upgrade_preserves_old_score_and_hidden_roll(self):
        path=Path('artifacts/poetic-fortune/baseline/fortune.py')
        spec=importlib.util.spec_from_file_location('old_fortune_poem_test',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
        m.ASSETS=Path('bot/assets/fortune')
        for uid in range(300):
            a=m.build_fortune(uid,'甲',date(2026,9,5));b=build_fortune(uid,'甲',date(2026,9,5))
            self.assertEqual((a.score,a.band,a.metrics,a.good,a.avoid,a.color,a.number),(b.score,b.band,b.metrics,b.good,b.avoid,b.color,b.number))
            self.assertEqual(a.hidden.key if a.hidden else None,b.hidden.key if b.hidden else None)
