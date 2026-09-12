import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.providers.codex import (
    CodexError,
    CodexProvider,
    _exec_args,
    _friendly_codex_error,
    _subprocess_env,
)


class CodexHelperTests(unittest.TestCase):
    def test_exec_args_override_reasoning(self):
        args = _exec_args(["codex"], Path("work"), Path("out.txt"), "gpt-6-astra")
        self.assertIn("-m", args)
        self.assertIn("gpt-6-astra", args)
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertIn("approval_policy=never", args)

    def test_friendly_auth_and_model_errors(self):
        self.assertIn("鉴权", _friendly_codex_error("", "401 Unauthorized", 1))
        self.assertIn("模型", _friendly_codex_error("", "unknown model gpt-6-astra", 1))
        self.assertIn("没有返回内容", _friendly_codex_error("", "", 0))

    def test_subprocess_env_drops_bot_secrets_keeps_path(self):
        old = os.environ.get("DEEPSEEK_API_KEY")
        try:
            os.environ["DEEPSEEK_API_KEY"] = "sk-secret-for-test"
            env = _subprocess_env(("sk-secret-for-test",))
            self.assertNotIn("DEEPSEEK_API_KEY", env)
            self.assertIn("PATH", env)
            self.assertNotIn("sk-secret-for-test", env.values())
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old


class CodexAskTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_exec_surfaces_stderr(self):
        provider = CodexProvider(timeout=10, workdir=Path("data/codex_workspace"))
        provider._cmd = ["codex"]
        provider._probed = True
        with patch.object(provider, "_run", AsyncMock(return_value=("", "unknown model gpt-6-astra", 1))):
            with self.assertRaises(CodexError) as ctx:
                await provider.ask("hi", model="gpt-6-astra")
        self.assertIn("模型", str(ctx.exception))
