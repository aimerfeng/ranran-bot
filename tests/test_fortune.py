import unittest
from datetime import UTC, date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from telegram.error import BadRequest

from bot.fortune import assets_ready, build_fortune, render_card, send_fortune, shanghai_day

ASSETS_HINT = "素材未下载：先运行 python scripts/fetch_fortune_assets.py"


class FortuneTests(unittest.TestCase):
    def test_daily_determinism(self):
        a = build_fortune(42, '测试用户', date(2026, 9, 5), theme='genshin')
        self.assertEqual(a, build_fortune(42, '测试用户', date(2026, 9, 5), theme='genshin'))
        self.assertNotEqual(a, build_fortune(42, '测试用户', date(2026, 9, 6)))
        self.assertNotEqual(a, build_fortune(43, '测试用户', date(2026, 9, 5)))
        self.assertEqual(len(a.metrics), 6)
        self.assertTrue(all(1 <= s <= 100 for _, s in a.metrics))
        self.assertTrue(set(a.good).isdisjoint(a.avoid))

    def test_all_bands_and_name_independence(self):
        bands = set()
        for uid in range(600):
            f = build_fortune(uid, '测试', date(2026, 9, 5))
            bands.add(f.band)
            self.assertTrue(f.advice)
            self.assertTrue(1 <= f.score <= 100)
            renamed = build_fortune(uid, '改名', date(2026, 9, 5))
            self.assertEqual((f.score, f.advice, f.metrics), (renamed.score, renamed.advice, renamed.metrics))
        self.assertEqual(bands, {'大吉', '中吉', '小吉', '末吉', '小凶', '凶', '大凶'})

    def test_midnight(self):
        self.assertEqual(shanghai_day(datetime(2026, 9, 5, 15, 59, tzinfo=UTC)), date(2026, 9, 5))
        self.assertEqual(shanghai_day(datetime(2026, 9, 5, 16, 0, tzinfo=UTC)), date(2026, 9, 6))

    @unittest.skipUnless(assets_ready(), ASSETS_HINT)
    def test_card(self):
        for name in ('星野', '很长的名字' * 30, 'Alice <b> & 🌙\n换行'):
            card = render_card(build_fortune(42, name, date(2026, 9, 5)))
            self.assertLess(len(card), 2_000_000)
            im = Image.open(BytesIO(card))
            self.assertEqual(im.size, (960, 960))
            self.assertEqual(im.format, 'PNG')
            im.verify()

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(assets_ready(), ASSETS_HINT)
    async def test_photo(self):
        msg = SimpleNamespace(reply_photo=AsyncMock(), reply_text=AsyncMock())
        await send_fortune(msg, 42, '星野')
        msg.reply_photo.assert_awaited_once()
        msg.reply_text.assert_not_awaited()

    async def test_photo_fallback(self):
        msg = SimpleNamespace(reply_photo=AsyncMock(side_effect=BadRequest('no photos')), reply_text=AsyncMock())
        await send_fortune(msg, 42, '星野')
        self.assertIn('幸运', msg.reply_text.call_args.args[0])
        self.assertIn('事业', msg.reply_text.call_args.args[0])

    async def test_render_fallback(self):
        msg = SimpleNamespace(reply_photo=AsyncMock(), reply_text=AsyncMock())
        with patch('bot.fortune.render_card', side_effect=OSError('font missing')):
            await send_fortune(msg, 42, '星野')
        msg.reply_photo.assert_not_awaited()
        msg.reply_text.assert_awaited_once()

    async def test_handler(self):
        from bot.main import yun_cmd
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42, full_name='星野', username=None), effective_message=object())
        with patch('bot.main._deny_if_unauthorized', AsyncMock(return_value=False)), patch('bot.main.send_fortune', AsyncMock()) as send, patch('bot.main._interpret_fortune', AsyncMock()):
            await yun_cmd(update, None)
            send.assert_awaited_once()
        with patch('bot.main._deny_if_unauthorized', AsyncMock(return_value=True)), patch('bot.main.send_fortune', AsyncMock()) as send, patch('bot.main._interpret_fortune', AsyncMock()):
            await yun_cmd(update, None)
            send.assert_not_awaited()

if __name__ == '__main__':
    unittest.main()
