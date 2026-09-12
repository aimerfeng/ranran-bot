import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.images import ImagePart, collect_images, message_image_media, sniff_image_mime
from bot.providers.deepseek import VISION_MODEL, user_content
from bot.util import mentioned_bot


JPEG = b"\xff\xd8\xff" + b"jpeg-body"
PNG = b"\x89PNG\r\n\x1a\n" + b"png-body"


class ImageHelperTests(unittest.TestCase):
    def test_sniff_mime(self):
        self.assertEqual(sniff_image_mime(JPEG), "image/jpeg")
        self.assertEqual(sniff_image_mime(PNG), "image/png")
        self.assertIsNone(sniff_image_mime(b"not-an-image"))

    def test_message_photo_and_document(self):
        photo = SimpleNamespace(file_id="p1")
        message = SimpleNamespace(photo=[SimpleNamespace(file_id="small"), photo], document=None, sticker=None)
        self.assertEqual(message_image_media(message), photo)
        document = SimpleNamespace(file_id="d1", mime_type="image/png")
        self.assertEqual(message_image_media(SimpleNamespace(photo=(), document=document, sticker=None)), document)
        self.assertIsNone(message_image_media(SimpleNamespace(photo=(), document=SimpleNamespace(mime_type="application/pdf"), sticker=None)))

    def test_user_content_with_images(self):
        image = ImagePart(data=JPEG, mime="image/jpeg")
        content = user_content("看图", [image])
        self.assertEqual(content[0], {"type": "text", "text": "看图"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertIn(base64.b64encode(JPEG).decode("ascii"), content[1]["image_url"]["url"])
        self.assertEqual(user_content("你好"), "你好")
        self.assertEqual(VISION_MODEL, "deepseek-v4-flash-vision-exp")

    def test_caption_mention(self):
        message = SimpleNamespace(
            text=None,
            caption="@ranranbot 这是什么",
            entities=None,
            caption_entities=[],
        )
        self.assertTrue(mentioned_bot(message, 1, "ranranbot"))


class CollectImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_photo_and_skips_duplicates(self):
        media = SimpleNamespace(file_id="p1")
        message = SimpleNamespace(photo=[media], document=None, sticker=None)
        reply = SimpleNamespace(photo=[media], document=None, sticker=None)
        with patch("bot.images.download_source", AsyncMock(return_value=JPEG)) as download:
            images = await collect_images(SimpleNamespace(), message, reply)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime, "image/jpeg")
        download.assert_awaited_once()
