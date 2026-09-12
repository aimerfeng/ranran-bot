import io
import unittest
from PIL import Image


def image_bytes(size=(900, 400), mode='RGBA', color=(70, 110, 180, 160), fmt='PNG'):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, fmt)
    return out.getvalue()


class MakerTests(unittest.TestCase):
    def maker(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('bot.sticker_maker'), '图片制作模块尚未实现')
        from bot import sticker_maker
        return sticker_maker

    def test_landscape_portrait_square_and_small(self):
        m = self.maker()
        for size in ((900,400),(300,900),(600,600),(12,5),(1,2000)):
            with self.subTest(size=size):
                payload = m.make_sticker(image_bytes(size))
                out = Image.open(io.BytesIO(payload))
                self.assertEqual(out.format, 'WEBP')
                self.assertEqual(max(out.size), 512)
                self.assertGreaterEqual(min(out.size), 1)
                self.assertLessEqual(len(payload), 512 * 1024)
                self.assertIn('A', out.getbands())

    def test_caption_changes_output_and_fits(self):
        m = self.maker()
        raw = image_bytes()
        plain = m.make_sticker(raw)
        for text in ('我真的谢', '这是一段比较长的中文贴纸文案，需要自动换行但不应该挡住图片。', 'A' * 80):
            out = m.make_sticker(raw, text)
            self.assertNotEqual(out, plain)
            self.assertEqual(Image.open(io.BytesIO(out)).size, (512,512))
        with self.assertRaises(ValueError):
            m.make_sticker(raw, '字' * 81)

    def test_invalid_oversized_and_animation(self):
        m = self.maker()
        with self.assertRaises(ValueError): m.make_sticker(b'not an image')
        with self.assertRaises(ValueError): m.make_sticker(b'x' * (m.MAX_INPUT_BYTES + 1))
        with self.assertRaises(ValueError): m.make_sticker(image_bytes((5000,4100)))
        out = io.BytesIO()
        Image.new('RGB',(20,20),'red').save(out,'GIF',save_all=True,append_images=[Image.new('RGB',(20,20),'blue')])
        with self.assertRaises(ValueError): m.make_sticker(out.getvalue())

    def test_exif_orientation(self):
        m = self.maker()
        exif = Image.Exif(); exif[274] = 6
        out = io.BytesIO(); Image.new('RGB',(800,400),'red').save(out,'JPEG',exif=exif)
        self.assertEqual(Image.open(io.BytesIO(m.make_sticker(out.getvalue()))).size,(256,512))

    def test_quote_short_long_and_newlines(self):
        m = self.maker()
        for text in ('明天一定早睡。', '第一行\n\n第三行', '今天我们聊了一些有意思的事情。' * 30):
            raw = m.make_quote(text,'小七',123)
            im = Image.open(io.BytesIO(raw))
            self.assertEqual(im.format,'PNG')
            self.assertEqual(im.width,1000)
            self.assertLessEqual(im.height,1500)
        with self.assertRaises(ValueError): m.make_quote('字'*501,'小七',123)
        with self.assertRaises(ValueError): m.make_quote('   ','小七',123)

    def test_wrap_preserves_text(self):
        m = self.maker()
        font = m.load_font(32)
        for text in ('中文 English words 混排','A'*120):
            lines = m.wrap_text(text,font,250)
            self.assertEqual(''.join(lines),text)
            self.assertTrue(all(font.getlength(line)<=250 for line in lines))
