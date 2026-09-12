import unittest
from collections import Counter
from datetime import date
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from PIL import Image
from bot.fortune import build_fortune,render_card,send_fortune,HIDDEN_SIGNS,hidden_from_ticket

class HiddenTests(unittest.TestCase):
    def test_exact_odds(self):
        counts=Counter(hidden_from_ticket(i).key if hidden_from_ticket(i) else 'normal' for i in range(10000))
        self.assertEqual(counts['normal'],9500)
        self.assertEqual(len(HIDDEN_SIGNS),8)
        for sign in HIDDEN_SIGNS:self.assertEqual(counts[sign.key],sign.weight)
        for invalid in (-1,10000):
            with self.assertRaises(ValueError):hidden_from_ticket(invalid)
    def test_stable_across_name_theme_and_restart(self):
        for uid in range(120):
            a=build_fortune(uid,'甲',date(2026,9,6))
            b=build_fortune(uid,'乙',date(2026,9,6),theme='genshin')
            self.assertEqual(a.hidden,b.hidden)
            self.assertEqual(a.advice,b.advice)
            self.assertEqual(a.hidden,build_fortune(uid,'甲',date(2026,9,6)).hidden)
    def test_hidden_all_render_and_text(self):
        base=build_fortune(42,'星野',date(2026,9,6))
        for sign in HIDDEN_SIGNS:
            f=replace(base,hidden=sign)
            image=Image.open(BytesIO(render_card(f)))
            self.assertEqual(image.size,(960,960));image.verify()
            self.assertIn(sign.title,f.text())
            self.assertIn(sign.advice,f.text())
            self.assertIn('基础运势',f.text())
    def test_hidden_not_command(self):
        from bot.fortune import THEME_ALIASES
        for sign in HIDDEN_SIGNS:self.assertNotIn(sign.title,THEME_ALIASES)

class HiddenDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_and_fallback(self):
        f=replace(build_fortune(42,'星野',date(2026,9,6)),hidden=HIDDEN_SIGNS[-1])
        msg=SimpleNamespace(reply_photo=AsyncMock(),reply_text=AsyncMock())
        with patch('bot.fortune.build_fortune',return_value=f):
            await send_fortune(msg,42,'星野')
        self.assertIn(f.hidden.title,msg.reply_photo.call_args.kwargs['caption'])
        with patch('bot.fortune.build_fortune',return_value=f),patch('bot.fortune.render_card',side_effect=OSError('missing')):
            await send_fortune(msg,42,'星野')
        self.assertIn(f.hidden.title,msg.reply_text.call_args.args[0])
