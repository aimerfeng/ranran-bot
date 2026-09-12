import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.files import FileError, create_artifact, normalize_csv, sanitize_filename
from bot.harness.agent import BUDGET_NOTICE, Agent
from bot.harness.kit import build_tools
from bot.harness.skills import SkillRegistry
from bot.harness.tools import ToolRegistry, ToolOutcome, json_tool
from bot.harness.types import Artifact, ToolCall
from bot.providers.deepseek import ChatResult
from bot.websearch import parse_search_marker


def write_skill(root: Path, rel: str, meta: str, body: str) -> None:
    path = root / f"{rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{meta}\n---\n{body}\n", encoding="utf-8")


class ToolRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_tool_and_bad_arguments_do_not_raise(self):
        async def handler(query: str) -> ToolOutcome:
            return ToolOutcome(content=f"ok:{query}")

        registry = ToolRegistry([json_tool("search", "搜索", {"query": {"type": "string"}}, handler)])
        missing = await registry.call("nope", {})
        self.assertIn("没有名为 nope 的工具", missing.content)
        wrong = await registry.call("search", {"wrong": 1})
        self.assertIn("参数不对", wrong.content)
        good = await registry.call("search", {"query": "天气"})
        self.assertEqual(good.content, "ok:天气")

    async def test_handler_crash_becomes_text(self):
        async def boom() -> ToolOutcome:
            raise RuntimeError("炸了")

        registry = ToolRegistry([json_tool("boom", "炸", {}, boom, required=[])])
        result = await registry.call("boom", {})
        self.assertIn("炸了", result.content)

    async def test_schema_shape(self):
        async def handler(x: str = "") -> str:
            return x

        tool = json_tool("t", "说明", {"a": {"type": "string"}}, handler)
        self.assertEqual(tool.schema()["function"]["name"], "t")
        self.assertEqual(tool.schema()["type"], "function")
        self.assertFalse(tool.schema()["function"]["parameters"]["additionalProperties"])


class SkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nested_include_expands_once(self):
        write_skill(self.root, "conversation/human-voice", "name: human-voice\nwhen: 聊天\nincludes:\n  - conversation/anti-assistant", "根节点正文")
        write_skill(self.root, "conversation/anti-assistant", "name: anti-assistant", "子节点正文")
        registry = SkillRegistry(self.root).load() or SkillRegistry(self.root)
        registry = SkillRegistry(self.root)
        self.assertEqual(registry.load(), 2)
        body = registry.expand("conversation/human-voice")
        self.assertIn("根节点正文", body)
        self.assertIn("子节点正文", body)
        self.assertLess(body.index("根节点正文"), body.index("子节点正文"))
        self.assertTrue(body.startswith("根节点正文"))

    def test_cycle_and_depth_are_bounded(self):
        write_skill(self.root, "a", "name: a\nincludes:\n  - b", "A正文")
        write_skill(self.root, "b", "name: b\nincludes:\n  - a", "B正文")
        registry = SkillRegistry(self.root)
        registry.load()
        body = registry.expand("a")
        self.assertEqual(body.count("A正文"), 1)
        self.assertEqual(body.count("B正文"), 1)

    def test_auto_active_by_keyword_and_always(self):
        write_skill(self.root, "always-one", "name: always-one\nalways: true", "常驻正文")
        write_skill(self.root, "chatty", "name: chatty\nwhen: 聊天|闲聊", "闲聊正文")
        write_skill(self.root, "rare", "name: rare", "低频正文")
        registry = SkillRegistry(self.root)
        registry.load()
        section = registry.active_section("今天随便聊聊天")
        self.assertIn("常驻正文", section)
        self.assertIn("闲聊正文", section)
        self.assertNotIn("低频正文", section)
        self.assertNotIn("低频正文", registry.active_section("聊工作"))

    def test_budget_truncates(self):
        write_skill(self.root, "big", "name: big\nalways: true", "长" * 5000)
        registry = SkillRegistry(self.root, budget=600)
        registry.load()
        section = registry.active_section("随便")
        self.assertLessEqual(len(section), 700)
        self.assertIn("已截断", section)

    def test_missing_root_is_safe(self):
        registry = SkillRegistry(Path("no/such/dir"))
        self.assertEqual(registry.load(), 0)
        self.assertEqual(registry.expand("whatever"), "")
        self.assertEqual(registry.catalog(), "")


class FakeProvider:
    """按脚本返回一串 chat 结果，用来驱动 Agent 循环。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def chat(self, messages, *, model=None, tools=None, temperature=0.95):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.results.pop(0) if self.results else ChatResult(content="结束")


def tool_call_result(name, arguments, call_id="c1"):
    raw = [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments=json.loads(arguments),
                raw_arguments=arguments,
            )
        ],
        assistant_message={"role": "assistant", "content": None, "tool_calls": raw},
    )


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_is_fed_back_then_final_answer(self):
        seen = {}

        async def handler(query: str) -> ToolOutcome:
            seen["query"] = query
            return ToolOutcome(content="搜索结果：显卡涨价 [1]")

        registry = ToolRegistry([json_tool("web_search", "搜索", {"query": {"type": "string"}}, handler)])
        provider = FakeProvider([
            tool_call_result("web_search", '{"query": "显卡价格"}'),
            ChatResult(content="今天显卡涨价了 [1]", assistant_message={"role": "assistant", "content": "今天显卡涨价了 [1]"}),
        ])
        turn = await Agent(provider, registry, max_steps=3).run(system="S", user_content="U")

        self.assertEqual(seen["query"], "显卡价格")
        self.assertEqual(turn.text, "今天显卡涨价了 [1]")
        self.assertEqual(turn.steps, 1)
        self.assertEqual(turn.calls, ["web_search"])
        second_messages = provider.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second_messages], ["system", "user", "assistant", "tool"])
        self.assertIn("tool_calls", second_messages[2])
        self.assertEqual(second_messages[3]["tool_call_id"], "c1")
        self.assertIn("搜索结果：显卡涨价", second_messages[3]["content"])

    async def test_tools_are_cut_off_after_budget(self):
        async def handler(query: str) -> ToolOutcome:
            return ToolOutcome(content="还是不够")

        registry = ToolRegistry([json_tool("web_search", "搜索", {"query": {"type": "string"}}, handler)])
        provider = FakeProvider([
            tool_call_result("web_search", '{"query": "a"}', "c1"),
            tool_call_result("web_search", '{"query": "b"}', "c2"),
            ChatResult(content="只好用现有的回答"),
        ])
        turn = await Agent(provider, registry, max_steps=2).run(system="S", user_content="U")

        self.assertEqual(turn.steps, 2)
        self.assertEqual(turn.text, "只好用现有的回答")
        self.assertIsNone(provider.calls[-1]["tools"])
        contents = [m.get("content") for m in provider.calls[-1]["messages"]]
        self.assertIn(BUDGET_NOTICE, contents)

    async def test_provider_without_tool_support_falls_back(self):
        async def handler(query: str) -> ToolOutcome:
            return ToolOutcome(content="不该被调用")

        registry = ToolRegistry([json_tool("web_search", "搜索", {"query": {"type": "string"}}, handler)])
        provider = FakeProvider([ChatResult(content="[搜索: 显卡]", tools_unsupported=True)])
        turn = await Agent(provider, registry).run(system="S", user_content="U")
        self.assertTrue(turn.tools_disabled)
        self.assertEqual(turn.text, "[搜索: 显卡]")

    async def test_text_marker_is_promoted_to_a_real_tool_call(self):
        seen = {}

        async def handler(query: str) -> ToolOutcome:
            seen["query"] = query
            return ToolOutcome(content="结果：显卡涨价 [1]")

        registry = ToolRegistry(
            [json_tool("web_search", "搜索", {"query": {"type": "string"}}, handler)]
        )
        provider = FakeProvider([
            ChatResult(
                content="[搜索: 显卡价格]",
                assistant_message={"role": "assistant", "content": "[搜索: 显卡价格]"},
            ),
            ChatResult(content="显卡确实涨价了 [1]"),
        ])
        turn = await Agent(
            provider, registry, max_steps=3, marker_parser=parse_search_marker
        ).run(system="S", user_content="U")
        self.assertEqual(seen["query"], "显卡价格")
        self.assertEqual(turn.text, "显卡确实涨价了 [1]")
        self.assertEqual(turn.steps, 1)
        self.assertIn("web_search", turn.calls)
        roles = [m["role"] for m in provider.calls[1]["messages"]]
        self.assertEqual(roles[:3], ["system", "user", "assistant"])
        self.assertEqual(roles[3], "user")
        self.assertIn("系统已经替你搜过", provider.calls[1]["messages"][3]["content"])

    async def test_marker_loop_is_bounded(self):
        async def handler(query: str) -> ToolOutcome:
            return ToolOutcome(content="还是不够")

        registry = ToolRegistry(
            [json_tool("web_search", "搜索", {"query": {"type": "string"}}, handler)]
        )
        provider = FakeProvider([
            ChatResult(content="[搜索: a]", assistant_message={"role": "assistant", "content": "[搜索: a]"})
            for _ in range(6)
        ])
        turn = await Agent(
            provider, registry, max_steps=2, marker_parser=parse_search_marker
        ).run(system="S", user_content="U")
        self.assertEqual(turn.steps, 2)
        self.assertLessEqual(len(provider.calls), 3)

    async def test_bad_json_arguments_are_reported_to_model(self):
        async def handler(query: str) -> ToolOutcome:
            return ToolOutcome(content="不该被调用")

        registry = ToolRegistry([json_tool("web_search", "搜索", {"query": {"type": "string"}}, handler)])
        broken = ChatResult(
            content="",
            tool_calls=[ToolCall(id="c1", name="web_search", arguments={}, raw_arguments="{坏", parse_error="Expecting property name")],
            assistant_message={"role": "assistant", "content": None, "tool_calls": []},
        )
        provider = FakeProvider([broken, ChatResult(content="好的")])
        turn = await Agent(provider, registry).run(system="S", user_content="U")
        self.assertEqual(turn.text, "好的")
        tool_message = provider.calls[1]["messages"][3]
        self.assertIn("不是合法 JSON", tool_message["content"])


class KitTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_file_produces_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            kit = build_tools(api_key="sk-x", model="m", outbox_dir=Path(td))
            outcome = await kit.registry.call(
                "write_file",
                {"filename": "计划.csv", "content": "任务,负责\n写代码,然然", "format": "csv"},
            )
            self.assertEqual(len(outcome.artifacts), 1)
            artifact = outcome.artifacts[0]
            self.assertEqual(artifact.filename, "计划.csv")
            self.assertTrue(artifact.path.exists())
            self.assertEqual(artifact.path.read_bytes()[:3], b"\xef\xbb\xbf")
            self.assertIn("已生成文件", outcome.content)

    async def test_use_skill_returns_body(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skills"
            write_skill(root, "conversation/human-voice", "name: human-voice", "真人感正文")
            registry = SkillRegistry(root)
            registry.load()
            kit = build_tools(
                api_key="sk-x", model="m", outbox_dir=Path(td) / "out", skills=registry
            )
            ok = await kit.registry.call("use_skill", {"name": "conversation/human-voice"})
            self.assertIn("真人感正文", ok.content)
            self.assertEqual(kit.skills_used, ["conversation/human-voice"])
            missing = await kit.registry.call("use_skill", {"name": "no-such"})
            self.assertIn("没有名为", missing.content)

    async def test_search_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            kit = build_tools(
                api_key="", model="m", outbox_dir=Path(td), max_searches=1
            )
            first = await kit.registry.call("web_search", {"query": "天气"})
            self.assertIn("联网搜索失败", first.content)
            second = await kit.registry.call("web_search", {"query": "天气"})
            self.assertIn("已经用完了", second.content)


class FileTests(unittest.TestCase):
    def test_filename_is_sanitized(self):
        self.assertEqual(sanitize_filename("../../etc/passwd.md"), "etcpasswd.md")
        self.assertEqual(sanitize_filename("a/b\\c:d*e?.md"), "abcde.md")
        self.assertEqual(sanitize_filename("report", "csv"), "report.csv")
        self.assertEqual(sanitize_filename("", "md"), "然然的文件.md")

    def test_unknown_extension_falls_back_to_txt(self):
        self.assertEqual(sanitize_filename("x.exe", ""), "x.txt")
        self.assertEqual(sanitize_filename("x.pdf", ""), "x.txt")

    def test_csv_is_normalized_with_quotes(self):
        out = normalize_csv('a,b\n"含,逗号",2')
        self.assertIn('"含,逗号",2', out)
        self.assertTrue(out.endswith("\r\n"))

    def test_empty_and_oversized_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileError):
                create_artifact(Path(td), filename="a.md", content="   ")
            with self.assertRaises(FileError):
                create_artifact(Path(td), filename="a.md", content="x" * 200_001)


if __name__ == "__main__":
    unittest.main()