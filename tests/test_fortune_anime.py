import hashlib
import json
import unittest
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageChops

from bot.fortune import ASSETS, assets_ready, build_fortune, render_card

ASSETS_HINT = "素材未下载：先运行 python scripts/fetch_fortune_assets.py"


class AnimeTests(unittest.TestCase):
    @unittest.skipUnless(assets_ready(), ASSETS_HINT)
    def test_real_anime_background(self):
        f=build_fortune(42,'星野',date(2026,9,5))
        self.assertTrue(f.image_key.startswith('img/'))
        with Image.open(ASSETS/f.image_key) as base:
            base=base.convert('RGB').resize((960,960),Image.Resampling.LANCZOS)
        im=Image.open(BytesIO(render_card(f)))
        self.assertEqual(im.size,(960,960))
        self.assertIsNone(ImageChops.difference(base.crop((480,0,960,960)),im.crop((480,0,960,960))).getbbox())
        self.assertIsNotNone(ImageChops.difference(base,im).getbbox())

    @unittest.skipUnless(assets_ready(), ASSETS_HINT)
    def test_all_themes(self):
        for theme in ('genshin','arknights','pcr','touhou'):
            f=build_fortune(42,'星野',date(2026,9,5),theme=theme)
            self.assertEqual(f.theme,theme)
            self.assertTrue(f.image_key.startswith('img/'+theme+'/'))
            Image.open(BytesIO(render_card(f))).verify()

    def test_theme_does_not_reroll_fortune(self):
        rows=[build_fortune(42,'星野',date(2026,9,5),theme=t) for t in ('random','genshin','arknights','pcr','touhou')]
        self.assertEqual(len({(r.score,r.band,r.advice) for r in rows}),1)
        self.assertEqual(rows[1].image_key,build_fortune(42,'改名',date(2026,9,5),theme='genshin').image_key)

    def test_no_path_traversal(self):
        with self.assertRaises(ValueError):
            build_fortune(42,'星野',theme='../../anything')

    @unittest.skipUnless(assets_ready(), ASSETS_HINT)
    def test_pinned_assets(self):
        manifest=json.loads((ASSETS/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['files']),597)
        for row in manifest['files']:
            p=ASSETS/row['path']
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['sha256'])
            if row['path'].startswith('img/'):
                with Image.open(p) as image:
                    self.assertEqual(image.size,(480,480))
                    image.verify()

class ThemeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_theme_alias(self):
        from bot.main import yun_cmd
        msg=SimpleNamespace(reply_text=AsyncMock(),text='/yun 原神')
        update=SimpleNamespace(effective_message=msg,effective_user=SimpleNamespace(id=42,full_name='星野'))
        with patch('bot.main._deny_if_unauthorized',AsyncMock(return_value=False)), patch('bot.main.send_fortune',AsyncMock()) as send, patch('bot.main._interpret_fortune',AsyncMock()):
            await yun_cmd(update,SimpleNamespace(args=['原神']))
            self.assertEqual(send.call_args.kwargs['theme'],'genshin')

    async def test_theme_help_and_invalid(self):
        from bot.main import yun_cmd
        for arg in ('主题','help','invalid'):
            msg=SimpleNamespace(reply_text=AsyncMock(),text='/yun '+arg)
            update=SimpleNamespace(effective_message=msg,effective_user=SimpleNamespace(id=42,full_name='星野'))
            with patch('bot.main._deny_if_unauthorized',AsyncMock(return_value=False)), patch('bot.main.send_fortune',AsyncMock()) as send, patch('bot.main._interpret_fortune',AsyncMock()):
                await yun_cmd(update,SimpleNamespace(args=[arg]))
                self.assertIn('/yun 原神',msg.reply_text.call_args.args[0])
                send.assert_not_awaited()
