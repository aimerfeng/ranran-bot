import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.core.runtime import RanranRuntime, Reply, session_key
from bot.harness.skills import SkillRegistry
from bot.harness.types import ToolCall
from bot.models import MODELS
from bot.persona import LENGTH_BUDGETS
from bot.providers.deepseek import ChatResult
from bot.websearch import SearchHit, SearchReport


def make_settings(tmp, *, guard=False, skills_dir=None):
    return SimpleNamespace(
        secrets=(), data_dir=tmp, deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash", skills_dir=skills_dir,
        persona_output_guard=guard, persona_extra_path=None,
    )


def make_runtime(tmp, provider, *, skills=None, persona_extra="", guard=False, settings=None,
                 length_planner=None):
    return RanranRuntime(
        settings or make_settings(tmp, guard=guard),
        skills=skills, persona_extra=persona_extra, deepseek=provider,
        session_state_path=tmp / "sessions.json", session_memory_path=tmp / "memory.json",
        guard_enabled=guard, length_planner=length_planner,
    )


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_external_persona_only_when_nsfw(self):
        runtime = make_runtime(self.tmp, SimpleNamespace(), persona_extra="外部人设标记XYZ")
        spec = SimpleNamespace(provider="deepseek", api_key=None, api_model="m")
        self.assertNotIn("外部人设标记XYZ", runtime.compose_prompt(spec, user_text="你好"))
        self.assertIn("外部人设标记XYZ", runtime.compose_prompt(spec, user_text="你好", nsfw=True))

    def test_output_guard_can_be_disabled(self):
        on = make_runtime(self.tmp, SimpleNamespace(), guard=True)
        off = make_runtime(self.tmp, SimpleNamespace(), guard=False)
        spec = SimpleNamespace(provider="deepseek", api_key=None, api_model="m")
        self.assertIn("输出纪律", on.compose_prompt(spec))
        self.assertNotIn("输出纪律", off.compose_prompt(spec))

    def test_skills_catalog_and_auto_activation(self):
        skills_dir = self.tmp / "skills"
        sub = skills_dir / "conversation"
        sub.mkdir(parents=True)
        (sub / "human-voice.md").write_text(
            "---\nname: human-voice\nwhen: 聊天\nalways: true\n---\n真人感规则正文\n", encoding="utf-8"
        )
        registry = SkillRegistry(skills_dir)
        registry.load()
        runtime = make_runtime(self.tmp, SimpleNamespace(), skills=registry)
        spec = SimpleNamespace(provider="deepseek", api_key=None, api_model="m")
        prompt = runtime.compose_prompt(spec, user_text="随便聊聊")
        self.assertIn("可用 skill", prompt)
        self.assertIn("真人感规则正文", prompt)

    def test_sessions_are_isolated_and_persisted(self):
        runtime = make_runtime(self.tmp, SimpleNamespace())
        runtime.set_session_model("alice", "pro")
        runtime.set_session_nsfw("alice", True)
        self.assertEqual(runtime.session("alice").model_key, "pro")
        self.assertTrue(runtime.session("alice").nsfw)
        self.assertFalse(runtime.session("bob").nsfw)
        # 新实例读同一份文件，配置仍在
        again = make_runtime(self.tmp, SimpleNamespace())
        self.assertEqual(again.session("alice").model_key, "pro")
        self.assertNotEqual(session_key("alice"), session_key("bob"))


class ChatResultProvider:
    """带原生工具能力的假 provider。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def chat(self, messages, *, model=None, tools=None, temperature=0.95):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.results.pop(0) if self.results else ChatResult(content="结束")


class AnswerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spec = SimpleNamespace(provider="deepseek", api_key="m", api_model="m")

    async def test_tool_loop_is_used_when_provider_supports_tools(self):
        seen = {}

        async def on_tool(name, outcome):
            seen["tool"] = name

        provider = ChatResultProvider([
            ChatResult(
                content="",
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"filename": "a.md", "content": "正文"})],
                assistant_message={"role": "assistant", "content": None, "tool_calls": []},
            ),
            ChatResult(content="写好了", assistant_message={"role": "assistant", "content": "写好了"}),
        ])
        runtime = make_runtime(self.tmp, provider)
        text, hits, artifacts = await runtime.answer(
            self.spec, "帮我写个文件", system="S", on_tool=on_tool
        )
        self.assertEqual(text, "写好了")
        self.assertEqual(seen["tool"], "write_file")
        self.assertEqual(len(artifacts), 1)
        self.assertTrue(artifacts[0].path.exists())

    async def test_provider_without_tools_falls_back_to_marker_search(self):
        report = SearchReport(hits=[SearchHit(url="https://a/1", title="标题", snippet="摘要", page_age="")], summary="汇总")
        provider = SimpleNamespace(ask=AsyncMock(side_effect=["[搜索: 今日热点]", "给结论 [1]"]))
        runtime = make_runtime(self.tmp, provider)
        with patch("bot.core.runtime.deepseek_web_search", AsyncMock(return_value=report)) as search:
            text, hits, artifacts = await runtime.answer(self.spec, "今天有什么新闻", system="S")
        search.assert_awaited_once()
        self.assertEqual(text, "给结论 [1]")
        self.assertEqual(hits[0].url, "https://a/1")
        self.assertEqual(provider.ask.await_count, 2)

    async def test_chat_remembers_and_strips_prefix(self):
        provider = SimpleNamespace(ask=AsyncMock(return_value="然然：我在呢"))
        runtime = make_runtime(self.tmp, provider)
        reply = await runtime.chat("s1", "在吗", speaker="甲")
        self.assertIsInstance(reply, Reply)
        self.assertEqual(reply.text, "我在呢")
        history = runtime.history("s1")
        self.assertIn("在吗", history)
        self.assertIn("我在呢", history)
        self.assertNotIn("然然：", history)

    async def test_chat_uses_session_model(self):
        provider = SimpleNamespace(ask=AsyncMock(return_value="好"))
        runtime = make_runtime(self.tmp, provider)
        runtime.set_session_model("s2", "pro")
        await runtime.chat("s2", "你好")
        self.assertEqual(provider.ask.await_args.kwargs["model"], MODELS["pro"].api_model)



class RoleplayStackTests(unittest.TestCase):
    """角色扮演模式必须与普通模式彻底分开（回归保护）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spec = SimpleNamespace(provider="deepseek", api_key=None, api_model="m")

    def test_roleplay_stack_drops_builtin_persona_and_guard(self):
        skills_dir = self.tmp / "skills"
        (skills_dir / "conversation").mkdir(parents=True)
        (skills_dir / "conversation" / "human-voice.md").write_text(
            "---\nname: human-voice\nalways: true\n---\n真人感规则正文\n", encoding="utf-8"
        )
        registry = SkillRegistry(skills_dir)
        registry.load()
        runtime = make_runtime(
            self.tmp, SimpleNamespace(), skills=registry,
            persona_extra="外部人设：测试标记 XYZ", guard=True,
        )
        system = runtime.compose_prompt(self.spec, user_text="继续", nsfw=True)
        self.assertIn("测试标记 XYZ", system)          # 人设本身在
        self.assertIn("运行环境说明", system)           # 运行约束在
        self.assertNotIn("沉浸式聊天风格", system)      # 内置然然人设不注入
        self.assertNotIn("输出纪律（覆盖上面任何冲突的要求）", system)  # 输出纪律不注入
        self.assertNotIn("真人感规则正文", system)      # skill 不注入
        self.assertNotIn("联网搜索规则", system)

    def test_normal_stack_unchanged(self):
        runtime = make_runtime(self.tmp, SimpleNamespace(), persona_extra="外部人设：测试标记 XYZ", guard=True)
        system = runtime.compose_prompt(self.spec, user_text="你好", nsfw=False)
        self.assertIn("沉浸式聊天风格", system)
        self.assertIn("输出纪律（覆盖上面任何冲突的要求）", system)
        self.assertNotIn("测试标记 XYZ", system)

    def test_roleplay_without_persona_falls_back_to_normal(self):
        runtime = make_runtime(self.tmp, SimpleNamespace(), persona_extra="")
        system = runtime.compose_prompt(self.spec, user_text="你好", nsfw=True)
        self.assertIn("沉浸式聊天风格", system)

    def test_roleplay_user_prompt_has_no_assistant_meta(self):
        from bot.persona import build_roleplay_prompt

        prompt = build_roleplay_prompt(speaker="小林", text="今晚别走。", history="小林: 在吗")
        self.assertIn("今晚别走", prompt)
        self.assertIn("前情提要", prompt)
        self.assertNotIn("先结合上下文理解意图", prompt)
        self.assertNotIn("触发：", prompt)


class RoleplayAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_drops_caller_extra_and_guard_in_roleplay(self):
        tmp = Path(tempfile.mkdtemp())
        spec = SimpleNamespace(provider="deepseek", api_key="m", api_model="m")
        seen = {}

        class Provider:
            async def chat(self, messages, *, model=None, tools=None, temperature=0.95):
                seen["system"] = messages[0]["content"]
                return ChatResult(content="（回应）", assistant_message={"role": "assistant", "content": "（回应）"})

        runtime = make_runtime(tmp, Provider(), persona_extra="外部人设：测试标记 XYZ")
        await runtime.answer(
            spec, "（对方的话）", system="旧的内置人设", extra="外部人设：测试标记 XYZ",
            guard="输出纪律（覆盖上面任何冲突的要求）", nsfw=True,
        )
        system = seen["system"]
        self.assertIn("测试标记 XYZ", system)
        self.assertNotIn("输出纪律（覆盖上面任何冲突的要求）", system)
        self.assertNotIn("旧的内置人设", system)



class LengthPlannerTests(unittest.IsolatedAsyncioTestCase):
    """篇幅规划：该长写还是短接（角色扮演模式下人设会不分场合写长）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spec = SimpleNamespace(provider="deepseek", api_key="sk-test", api_model="deepseek-v4-flash")

    def _runtime(self, provider, *, planner=True, persona="外部人设：测试标记 XYZ"):
        return make_runtime(self.tmp, provider, persona_extra=persona, length_planner=planner)

    async def test_obvious_short_message_skips_model_call(self):
        provider = SimpleNamespace(ask=AsyncMock(return_value="long"))
        runtime = self._runtime(provider)
        mode, source = await runtime.plan_length(self.spec, text="嗯")
        self.assertEqual((mode, source), ("short", "heuristic"))
        provider.ask.assert_not_awaited()

    async def test_obvious_long_request_skips_model_call(self):
        provider = SimpleNamespace(ask=AsyncMock(return_value="short"))
        runtime = self._runtime(provider)
        mode, source = await runtime.plan_length(self.spec, text="继续写下去，别停")
        self.assertEqual((mode, source), ("long", "heuristic"))
        provider.ask.assert_not_awaited()

    async def test_ambiguous_message_asks_the_model(self):
        provider = SimpleNamespace(ask=AsyncMock(return_value="normal"))
        runtime = self._runtime(provider)
        mode, source = await runtime.plan_length(self.spec, text="今天在办公室遇到点事")
        self.assertEqual((mode, source), ("normal", "model"))
        provider.ask.assert_awaited_once()

    async def test_planner_failure_falls_back_to_normal(self):
        provider = SimpleNamespace(ask=AsyncMock(side_effect=RuntimeError("炸了")))
        runtime = self._runtime(provider)
        mode, source = await runtime.plan_length(self.spec, text="今天在办公室遇到点事")
        self.assertEqual((mode, source), ("normal", "fallback"))

    async def test_budget_is_injected_last_in_roleplay_prompt(self):
        runtime = self._runtime(SimpleNamespace())
        system = runtime.compose_prompt(self.spec, user_text="嗯", nsfw=True, budget=LENGTH_BUDGETS["short"])
        self.assertTrue(system.rstrip().endswith(LENGTH_BUDGETS["short"]))
        self.assertIn("测试标记 XYZ", system)

    async def test_chat_injects_short_budget_for_greeting(self):
        seen = {}

        class Provider:
            async def chat(self, messages, *, model=None, tools=None, temperature=0.95):
                seen["system"] = messages[0]["content"]
                return ChatResult(content="（短句回应）", assistant_message={"role": "assistant", "content": "（短句回应）"})

        runtime = self._runtime(Provider())
        runtime.set_session_nsfw("s1", True)
        await runtime.chat("s1", "嗯", speaker="小林")
        self.assertIn(LENGTH_BUDGETS["short"], seen["system"])

    async def test_chat_skips_budget_when_planner_disabled(self):
        seen = {}

        class Provider:
            async def chat(self, messages, *, model=None, tools=None, temperature=0.95):
                seen["system"] = messages[0]["content"]
                return ChatResult(content="（回应）", assistant_message={"role": "assistant", "content": "（回应）"})

        runtime = self._runtime(Provider(), planner=False)
        runtime.set_session_nsfw("s1", True)
        await runtime.chat("s1", "嗯", speaker="小林")
        self.assertNotIn("【本轮篇幅", seen["system"])

    async def test_normal_mode_has_no_budget(self):
        seen = {}

        class Provider:
            async def chat(self, messages, *, model=None, tools=None, temperature=0.95):
                seen["system"] = messages[0]["content"]
                return ChatResult(content="（回应）", assistant_message={"role": "assistant", "content": "（回应）"})

        runtime = self._runtime(Provider())
        await runtime.chat("s1", "嗯", speaker="小林")     # nsfw 关
        self.assertNotIn("【本轮篇幅", seen["system"])


class LengthLabelTests(unittest.TestCase):
    def test_hint_classification(self):
        from bot.persona import classify_length_hint

        for text in ("嗯", "？", "好的", "在吗", "哈哈"):
            self.assertEqual(classify_length_hint(text), "short", text)
        for text in ("继续", "展开写", "细写这一段"):
            self.assertEqual(classify_length_hint(text), "long", text)
        self.assertIsNone(classify_length_hint("今天在办公室遇到点事"))

    def test_label_parsing_is_forgiving(self):
        from bot.persona import parse_length_label

        self.assertEqual(parse_length_label("short"), "short")
        self.assertEqual(parse_length_label("LONG."), "long")
        self.assertEqual(parse_length_label("这个该写长一点"), "long")
        self.assertEqual(parse_length_label("应该短一些"), "short")
        self.assertEqual(parse_length_label("??"), "normal")
        self.assertEqual(parse_length_label(""), "normal")

if __name__ == "__main__":
    unittest.main()
