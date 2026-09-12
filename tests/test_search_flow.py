import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.chat_state import ChatMemory
from bot.core.runtime import RanranRuntime
from bot.websearch import MAX_SEARCH_ROUNDS, SearchHit, SearchReport, WebSearchError


def _hit(url: str = "https://news.example/1") -> SearchHit:
    return SearchHit(url=url, title="示例新闻标题", snippet="摘要", page_age="")


def make_fixture(
    answers, *, text="今天有什么新闻", username="aimerranbot", persona_extra="", nsfw=False
):
    memory = ChatMemory()
    message = SimpleNamespace(
        message_id=9,
        text=text,
        caption=None,
        entities=None,
        caption_entities=None,
        from_user=SimpleNamespace(id=7, full_name="甲", username=None, is_bot=False),
        reply_to_message=None,
        reply_text=AsyncMock(
            return_value=SimpleNamespace(
                message_id=123, edit_text=AsyncMock(), delete=AsyncMock()
            )
        ),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(full_name="甲", username=None, id=7, is_bot=False),
    )
    provider = SimpleNamespace(ask=AsyncMock(side_effect=list(answers)))
    settings = SimpleNamespace(
        secrets=(),
        deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash",
        persona_extra_path=None,
        persona_output_guard=False,
    )
    ctx = SimpleNamespace(
        bot=SimpleNamespace(id=1, username=username),
        bot_data={
            "memory": memory,
            "locks": {},
            "settings": settings,
            "persona_extra": persona_extra,
            "deepseek": provider,
            "reply_models": {},
            "chat_state": SimpleNamespace(
                get_model=lambda chat_id: "flash", nsfw=lambda chat_id: nsfw
            ),
            "runtime": make_runtime(provider, Path(tempfile.mkdtemp()), persona_extra=persona_extra),
        },
    )
    return update, ctx, provider




def make_runtime(provider, tmp, persona_extra=""):
    """测试用核心运行时：注入假 provider，避免真实网络调用。"""
    settings = SimpleNamespace(
        secrets=(), data_dir=tmp, deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash", skills_dir=None, persona_output_guard=False,
    )
    return RanranRuntime(
        settings, persona_extra=persona_extra, deepseek=provider,
        session_state_path=tmp / "sessions.json", session_memory_path=tmp / "memory.json",
    )


class SearchFlowTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, answers, **kwargs):
        return make_fixture(answers, **kwargs)

    async def test_model_marker_triggers_search_then_answers_with_sources(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(["我先查一下。\n[搜索: 今日热点新闻]", "今天的热点是……[1]"])
        report = SearchReport(hits=[_hit()], summary="搜索汇总正文")
        with patch("bot.core.runtime.deepseek_web_search", AsyncMock(return_value=report)) as search, patch(
            "bot.main._deliver_reply", AsyncMock()
        ) as deliver:
            await _run_query(update, ctx, "flash", "今天有什么新闻", trigger="私聊")

        search.assert_awaited_once()
        self.assertEqual(search.await_args.args[1], "今日热点新闻")
        self.assertEqual(provider.ask.await_count, 2)
        second_prompt = provider.ask.await_args_list[1].args[0]
        self.assertIn("搜索汇总正文", second_prompt)
        self.assertIn("https://news.example/1", second_prompt)
        self.assertIn("今天的热点是", deliver.call_args.args[5])
        replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
        self.assertTrue(any("来源" in r and "https://news.example/1" in r for r in replies))

    async def test_search_failure_is_reported_not_silent(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(["[搜索: 今日热点新闻]"])
        with patch(
            "bot.core.runtime.deepseek_web_search", AsyncMock(side_effect=WebSearchError("搜索服务超时"))
        ), patch("bot.main._deliver_reply", AsyncMock()) as deliver:
            await _run_query(update, ctx, "flash", "今天有什么新闻", trigger="私聊")
        self.assertIn("联网搜索没成功", deliver.call_args.args[5])
        self.assertIn("搜索服务超时", deliver.call_args.args[5])

    async def test_explicit_request_routes_to_search_instead_of_chat(self):
        from bot.main import _maybe_reply

        update, ctx, _ = self.fixture([], text="@aimerranbot 联网帮我搜索热点新闻")
        with patch("bot.main._run_search_query", AsyncMock()) as search, patch(
            "bot.main._run_query", AsyncMock()
        ) as chat:
            await _maybe_reply(update, ctx, "@aimerranbot 联网帮我搜索热点新闻")
        search.assert_awaited_once()
        self.assertEqual(search.await_args.args[2], "热点新闻")
        chat.assert_not_awaited()

    async def test_plain_chat_does_not_search(self):
        from bot.main import _maybe_reply

        update, ctx, _ = self.fixture([], text="@aimerranbot 年假一般是多少天")
        with patch("bot.main._run_search_query", AsyncMock()) as search, patch(
            "bot.main._run_query", AsyncMock()
        ) as chat:
            await _maybe_reply(update, ctx, "@aimerranbot 年假一般是多少天")
        search.assert_not_awaited()
        chat.assert_awaited_once()


class MultiRoundSearchTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, answers, **kwargs):
        return make_fixture(answers, **kwargs)

    async def test_model_can_ask_for_more_search_rounds(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(
            ["[搜索: 第一轮]", "[搜索: 第二轮]", "综合两轮结果，结论是……[1][2]"]
        )
        first = SearchReport(hits=[_hit("https://a.example/1")], summary="A 汇总")
        second = SearchReport(hits=[_hit("https://b.example/2")], summary="B 汇总")
        with patch(
            "bot.core.runtime.deepseek_web_search", AsyncMock(side_effect=[first, second])
        ) as search, patch("bot.main._deliver_reply", AsyncMock()) as deliver:
            await _run_query(update, ctx, "flash", "今天有什么新闻", trigger="私聊")

        self.assertEqual(search.await_count, 2)
        self.assertEqual(search.await_args_list[0].args[1], "第一轮")
        self.assertEqual(search.await_args_list[1].args[1], "第二轮")
        self.assertEqual(provider.ask.await_count, 3)
        third_prompt = provider.ask.await_args_list[2].args[0]
        self.assertIn("A 汇总", third_prompt)
        self.assertIn("B 汇总", third_prompt)
        self.assertIn("综合两轮结果", deliver.call_args.args[5])
        replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
        sources = [r for r in replies if "来源" in r]
        self.assertEqual(len(sources), 1)
        self.assertIn("https://a.example/1", sources[0])
        self.assertIn("https://b.example/2", sources[0])

    async def test_rounds_are_capped_and_marker_never_leaks(self):
        from bot.main import _run_query

        report = SearchReport(hits=[_hit()], summary="汇总")
        update, ctx, provider = self.fixture(["[搜索: 一次又一次]"] * (MAX_SEARCH_ROUNDS + 2))
        with patch("bot.core.runtime.deepseek_web_search", AsyncMock(return_value=report)) as search, patch(
            "bot.main._deliver_reply", AsyncMock()
        ) as deliver:
            await _run_query(update, ctx, "flash", "今天有什么新闻", trigger="私聊")

        self.assertEqual(search.await_count, MAX_SEARCH_ROUNDS)
        self.assertEqual(provider.ask.await_count, MAX_SEARCH_ROUNDS + 1)
        last_system = provider.ask.await_args_list[-1].kwargs["system"]
        self.assertIn("用满", last_system)
        self.assertNotIn("[搜索:", deliver.call_args.args[5])

    async def test_later_round_failure_still_answers(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(
            ["[搜索: 第一轮]", "[搜索: 第二轮]", "用第一轮的材料回答 [1]"]
        )
        report = SearchReport(hits=[_hit("https://a.example/1")], summary="A 汇总")
        with patch(
            "bot.core.runtime.deepseek_web_search",
            AsyncMock(side_effect=[report, WebSearchError("服务超时")]),
        ) as search, patch("bot.main._deliver_reply", AsyncMock()) as deliver:
            await _run_query(update, ctx, "flash", "今天有什么新闻", trigger="私聊")

        self.assertEqual(search.await_count, 2)
        self.assertIn("用第一轮的材料回答", deliver.call_args.args[5])
        replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
        self.assertTrue(any("https://a.example/1" in r for r in replies))

    async def test_first_round_failure_is_reported(self):
        from bot.main import _run_query

        update, ctx, _ = self.fixture(["[搜索: 第一轮]"])
        with patch(
            "bot.core.runtime.deepseek_web_search", AsyncMock(side_effect=WebSearchError("服务超时"))
        ), patch("bot.main._deliver_reply", AsyncMock()) as deliver:
            await _run_query(update, ctx, "flash", "今天有什么新闻", trigger="私聊")
        self.assertIn("联网搜索没成功", deliver.call_args.args[5])

    async def test_automatic_reply_never_searches(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(["[搜索: 群里的八卦]"])
        with patch("bot.core.runtime.deepseek_web_search", AsyncMock()) as search, patch(
            "bot.main._deliver_reply", AsyncMock()
        ) as deliver:
            await _run_query(update, ctx, "flash", "他们在聊天", trigger="主动接话")
        search.assert_not_awaited()
        deliver.assert_not_awaited()

    async def test_external_persona_is_off_unless_switched_on(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(["普通回答"], persona_extra="外部人设：测试标记 XYZ")
        with patch("bot.main._deliver_reply", AsyncMock()), patch("bot.main._store_model"):
            await _run_query(update, ctx, "flash", "你好", trigger="私聊")
        system = provider.ask.await_args.kwargs["system"]
        self.assertNotIn("测试标记 XYZ", system)

    async def test_external_persona_injected_when_switched_on(self):
        from bot.main import _run_query

        update, ctx, provider = self.fixture(
            ["普通回答"], persona_extra="外部人设：测试标记 XYZ", nsfw=True
        )
        with patch("bot.main._deliver_reply", AsyncMock()), patch("bot.main._store_model"):
            await _run_query(update, ctx, "flash", "你好", trigger="私聊")
        system = provider.ask.await_args.kwargs["system"]
        # 角色扮演模式：外部人设独立成栈，内置然然人设与输出纪律都不再注入
        self.assertIn("测试标记 XYZ", system)
        self.assertNotIn("沉浸式聊天风格", system)
        self.assertNotIn("输出纪律（覆盖上面任何冲突的要求）", system)
        self.assertIn("运行环境说明", system)


if __name__ == "__main__":
    unittest.main()
