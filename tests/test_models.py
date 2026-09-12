import unittest

from bot.models import MODELS, model_for_images, parse_model_key, resolve_model_key


class ModelAliasTests(unittest.TestCase):
    def test_flash_and_v41_aliases(self):
        for raw in ("flash", "v4.1", "v41flash", "deepseek-v4.1-flash", "deepseekv4.1flash"):
            self.assertEqual(parse_model_key(raw), "flash")
        self.assertEqual(MODELS["flash"].api_model, "deepseek-v4-flash")

    def test_vision_aliases(self):
        for raw in ("vision", "flash-vision", "deepseek-v4-flash-vision-exp", "看图"):
            self.assertEqual(parse_model_key(raw), "vision")
        spec = MODELS["vision"]
        self.assertTrue(spec.vision)
        self.assertEqual(spec.api_model, "deepseek-v4-flash-vision-exp")

    def test_image_requests_use_vision(self):
        self.assertEqual(model_for_images("flash"), "vision")
        self.assertEqual(model_for_images("vision"), "vision")
        self.assertEqual(model_for_images("codex"), "vision")

    def test_settings_default(self):
        self.assertEqual(resolve_model_key("deepseek-v4-flash"), "flash")
        self.assertEqual(resolve_model_key("deepseek-v4-flash-vision-exp"), "vision")
