import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.chat_state import ChatMemory
from bot.files import FileError
from bot.websearch import SearchHit
from bot.core.runtime import RanranRuntime

def make_runtime(provider, tmp):
    """测试用核心运行时：注入假 provider，避免真实网络调用。"""
    settings = SimpleNamespace(
        secrets=(), data_dir=tmp, deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash", skills_dir=None, persona_output_guard=False,
    )
    return RanranRuntime(
        settings, persona_extra="", deepseek=provider,
        session_state_path=tmp / "sessions.json", session_memory_path=tmp / "memory.json",
    )



def make_ctx(tmp: Path, *, threshold: int = 500):
    memory = ChatMemory()
    thinking = SimpleNamespace(message_id=100, edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(
        message_id=9,
        reply_text=AsyncMock(return_value=thinking),
        reply_document=AsyncMock(return_value=SimpleNamespace(message_id=201)),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(full_name="甲", username=None, id=7, is_bot=False),
    )
    settings = SimpleNamespace(
        secrets=(),
        data_dir=tmp,
        reply_file_threshold=threshold,
        deepseek_api_key="sk-test",
        deepseek_model="deepseek-v4-flash",
        persona_extra_path=None,
        persona_output_guard=False,
    )
    ctx = SimpleNamespace(
        bot=SimpleNamespace(id=1, username="aimerranbot"),
        bot_data={
            "memory": memory,
            "locks": {},
            "settings": settings,
            "reply_models": {},
            "chat_state": SimpleNamespace(get_model=lambda chat_id: "flash", nsfw=lambda chat_id: False),
            "runtime": make_runtime(None, tmp),
        },
    )
    return update, ctx, message, thinking


class ReplyFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_reply_is_sent_as_markdown_file(self):
        from bot.main import _deliver_reply

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            update, ctx, message, thinking = make_ctx(tmp)
            long_text = "然然的长回复。" * 80  # 560 字
            self.assertGreater(len(long_text), 500)
            await _deliver_reply(
                update, ctx, thinking, "flash", "私聊", long_text, playful=True
            )

            files = list((tmp / "outbox").glob("*.md"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_text(encoding="utf-8"), long_text)
            sent_file = message.reply_document.await_args.kwargs["document"]
            self.assertTrue(sent_file.filename.startswith("然然的回复-"))
            self.assertTrue(sent_file.filename.endswith(".md"))
            notice = thinking.edit_text.await_args.args[0]
            self.assertIn("md 文件", notice)
            self.assertIn(str(len(long_text)), notice)
            self.assertNotIn(long_text, notice)
            self.assertIn(long_text, ctx.bot_data["memory"].render(1))

    async def test_short_reply_still_sent_as_text(self):
        from bot.main import _deliver_reply

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            update, ctx, message, thinking = make_ctx(tmp)
            await _deliver_reply(update, ctx, thinking, "flash", "私聊", "短回复。", playful=True)
            message.reply_document.assert_not_awaited()
            self.assertFalse((tmp / "outbox").exists())
            self.assertEqual(thinking.edit_text.await_args.args[0], "短回复。")

    async def test_threshold_zero_disables_file_mode(self):
        from bot.main import _deliver_reply

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            update, ctx, message, thinking = make_ctx(tmp, threshold=0)
            await _deliver_reply(update, ctx, thinking, "flash", "私聊", "长" * 800, playful=True)
            message.reply_document.assert_not_awaited()
            self.assertFalse((tmp / "outbox").exists())

    async def test_write_failure_falls_back_to_text(self):
        from bot.main import _deliver_reply

        with tempfile.TemporaryDirectory() as td:
            update, ctx, message, thinking = make_ctx(Path(td))
            long_text = "长" * 800
            with patch("bot.main.create_artifact", side_effect=FileError("磁盘满了")):
                await _deliver_reply(update, ctx, thinking, "flash", "私聊", long_text, playful=True)
            message.reply_document.assert_not_awaited()
            self.assertTrue(thinking.edit_text.await_args.args[0].startswith("长"))

    async def test_search_answer_over_threshold_still_lists_sources(self):
        from bot.main import _run_search_query

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            update, ctx, message, thinking = make_ctx(tmp)
            hit = SearchHit(url="https://a.example/1", title="示例", snippet="", page_age="")
            long_answer = "搜索结论。" * 120
            with patch(
                "bot.main._answer_with_tools", AsyncMock(return_value=(long_answer, [hit], []))
            ):
                await _run_search_query(update, ctx, "今天的新闻")

            files = list((tmp / "outbox").glob("*.md"))
            self.assertEqual(len(files), 1)
            message.reply_document.assert_awaited_once()
            texts = [call.args[0] for call in message.reply_text.await_args_list]
            self.assertTrue(any("来源" in t and "https://a.example/1" in t for t in texts))


if __name__ == "__main__":
    unittest.main()