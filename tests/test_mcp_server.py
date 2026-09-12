import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.core.runtime import RanranRuntime

# MCP 依赖是可选的（只需要 Telegram bot 的人不必安装），没装就跳过这些用例。
try:
    from adapters.mcp.server import TOOL_NAMES, build_server
except ImportError:  # pragma: no cover - 取决于环境
    TOOL_NAMES, build_server = (), None

MCP_HINT = "未安装 mcp 依赖：pip install -r requirements-mcp.txt"


def make_settings(tmp):
    return SimpleNamespace(
        secrets=(), data_dir=tmp, deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash", skills_dir=None, persona_output_guard=False,
    )


def make_runtime(tmp, provider):
    return RanranRuntime(
        make_settings(tmp), persona_extra="", deepseek=provider,
        session_state_path=tmp / "sessions.json", session_memory_path=tmp / "memory.json",
    )


def tool_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


@unittest.skipUnless(build_server is not None, MCP_HINT)
class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.provider = SimpleNamespace(ask=AsyncMock(return_value="嗨，我在。"))
        self.runtime = make_runtime(self.tmp, self.provider)
        self.server = build_server(self.runtime)

    async def test_all_tools_are_registered(self):
        names = {tool.name for tool in await self.server.list_tools()}
        self.assertEqual(names, set(TOOL_NAMES))

    async def test_ranran_chat_returns_reply_and_remembers(self):
        result = await self.server.call_tool(
            "ranran_chat", {"message": "在吗", "session_id": "u1", "speaker": "小林"}
        )
        self.assertIn("嗨，我在。", tool_text(result))
        history = self.runtime.history("u1")
        self.assertIn("在吗", history)
        self.assertIn("小林", history)

    async def test_write_file_tool_creates_csv(self):
        result = await self.server.call_tool(
            "write_file",
            {"filename": "名单", "content": "名字,备注\n小林,美式", "format": "csv"},
        )
        text = tool_text(result)
        self.assertIn("已写入", text)
        path = Path(text.split("已写入 ")[1].split("（")[0].strip())
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf")

    async def test_list_and_use_skill(self):
        listed = tool_text(await self.server.call_tool("list_skills", {}))
        self.assertIn("skill", listed)
        missing = tool_text(await self.server.call_tool("use_skill", {"name": "no-such-skill"}))
        self.assertIn("没有名为", missing)

    async def test_search_history_reads_session_memory(self):
        await self.server.call_tool("ranran_chat", {"message": "我下周要去杭州出差", "session_id": "u2"})
        found = tool_text(await self.server.call_tool("search_history", {"keyword": "杭州", "session_id": "u2"}))
        self.assertIn("杭州", found)
        empty = tool_text(await self.server.call_tool("search_history", {"keyword": "火星", "session_id": "u2"}))
        self.assertIn("没有包含", empty)

    async def test_session_tool_reads_and_writes(self):
        before = tool_text(await self.server.call_tool("ranran_session", {"session_id": "u3"}))
        self.assertIn("外部人设 关", before)
        after = tool_text(
            await self.server.call_tool("ranran_session", {"session_id": "u3", "model": "pro", "nsfw": "on"})
        )
        self.assertIn("模型 pro", after)
        self.assertIn("外部人设 开", after)
        self.assertTrue(self.runtime.session("u3").nsfw)

    async def test_web_search_reports_failure_without_raising(self):
        from unittest.mock import patch

        from bot.websearch import WebSearchError

        with patch("bot.core.runtime.deepseek_web_search", AsyncMock(side_effect=WebSearchError("服务超时"))):
            text = tool_text(await self.server.call_tool("web_search", {"query": "今天新闻"}))
        self.assertIn("联网搜索失败", text)
        self.assertIn("服务超时", text)


if __name__ == "__main__":
    unittest.main()
