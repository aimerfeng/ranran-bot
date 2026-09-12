import os
import unittest

from dotenv import dotenv_values

from bot.settings import ROOT, load_settings, secret_fingerprint


class SettingsLoadTests(unittest.TestCase):
    def test_dotenv_overrides_stale_process_env(self):
        vals = dotenv_values(ROOT / ".env")
        expected_key = (vals.get("DEEPSEEK_API_KEY") or "").strip()
        expected_model = (vals.get("DEEPSEEK_MODEL") or "deepseek-v4-flash").strip()
        if not expected_key:
            self.skipTest(".env has no DEEPSEEK_API_KEY")
        old = {name: os.environ.get(name) for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL")}
        try:
            os.environ["DEEPSEEK_API_KEY"] = "sk-STALEKEYSTALEKEYSTALE"
            os.environ["DEEPSEEK_MODEL"] = "deepseek-v4-pro"
            settings = load_settings()
            self.assertEqual(settings.deepseek_api_key, expected_key)
            self.assertEqual(settings.deepseek_model, expected_model)
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_fingerprint_hides_secret(self):
        self.assertEqual(secret_fingerprint(""), "missing")
        self.assertEqual(secret_fingerprint("sk-dummykeydummykey7243"), "****7243")
