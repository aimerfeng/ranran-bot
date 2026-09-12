"""DeepSeek 提供方的重试与错误映射（不打真实网络）。"""
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bot.providers.deepseek import DeepSeekError, DeepSeekProvider

URL = "https://api.deepseek.com/chat/completions"


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code=status, json=payload or {}, request=httpx.Request("POST", URL))


class FakeClient:
    """按脚本依次返回响应，并记录调用次数。"""

    calls: list[int] = []

    def __init__(self, script, *_args, **_kwargs):
        self.script = script

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, *_args, **_kwargs):
        FakeClient.calls.append(1)
        index = min(len(FakeClient.calls) - 1, len(self.script) - 1)
        item = self.script[index]
        if isinstance(item, Exception):
            raise item
        return item


OK_PAYLOAD = {"choices": [{"message": {"content": "好的"}}]}


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeClient.calls = []
        self.provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash")

    def _fake_client(self, script):
        """只替换 AsyncClient：httpx 的异常类型必须保持真实，否则测不出超时分支。"""
        return patch(
            "bot.providers.deepseek.httpx.AsyncClient",
            lambda *args, **kwargs: FakeClient(script, *args, **kwargs),
        )

    @staticmethod
    def _no_sleep():
        return patch("bot.providers.deepseek.asyncio.sleep", AsyncMock())

    async def test_retries_then_succeeds(self):
        with self._fake_client([_response(429), _response(503), _response(200, OK_PAYLOAD)]), self._no_sleep():
            text = await self.provider.ask("在吗")
        self.assertEqual(text, "好的")
        self.assertEqual(len(FakeClient.calls), 3)

    async def test_timeout_is_retried_then_reported(self):
        with self._fake_client([httpx.TimeoutException("超时")] * 3), self._no_sleep():
            with self.assertRaises(DeepSeekError) as ctx:
                await self.provider.ask("在吗")
        self.assertIn("超时", str(ctx.exception))
        self.assertEqual(len(FakeClient.calls), 3)

    async def test_network_error_is_retried(self):
        with self._fake_client([httpx.ConnectError("断了")] * 3), self._no_sleep():
            with self.assertRaises(DeepSeekError) as ctx:
                await self.provider.ask("在吗")
        self.assertIn("无法连接", str(ctx.exception))
        self.assertEqual(len(FakeClient.calls), 3)

    async def test_bad_key_is_not_retried(self):
        with self._fake_client([_response(401)]), self._no_sleep():
            with self.assertRaises(DeepSeekError) as ctx:
                await self.provider.ask("在吗")
        self.assertIn("API Key 无效", str(ctx.exception))
        self.assertEqual(len(FakeClient.calls), 1)


if __name__ == "__main__":
    unittest.main()
