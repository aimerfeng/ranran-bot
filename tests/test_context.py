import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.chat_state import ChatMemory
from bot.core.runtime import RanranRuntime
from bot.persona import build_user_prompt


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


class MemoryTests(unittest.TestCase):
    def test_persist_isolate_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'memory.json';m=ChatMemory(path=p)
            m.add(1,'甲','项目叫星河',message_id=1)
            m.add(1,'甲','项目叫星河',message_id=1)
            m.add(2,'乙','另一个群',message_id=1)
            restored=ChatMemory(path=p)
            self.assertEqual(restored.render(1).count('项目叫星河'),1)
            self.assertNotIn('另一个群',restored.render(1))
            self.assertNotIn('项目叫星河',restored.render(1,exclude_message_id=1))
    def test_bounds(self):
        m=ChatMemory(limit=3,max_chars=180)
        for n in range(10):m.add(1,'甲','x'*100,message_id=n)
        self.assertLessEqual(len(m.render(1)),180)
        self.assertNotIn('消息0]',m.render(1))
    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'memory.json';p.write_text('{broken')
            self.assertEqual(ChatMemory(path=p).render(1),'')
    def test_quote_and_private_prompt(self):
        p=build_user_prompt(speaker='甲',text='选第二个',history='甲: 想选原神或东方',trigger='私聊',reply_context='然然: 原神和东方，你选哪个？',chat_type='private')
        self.assertIn('私聊',p);self.assertIn('原神和东方，你选哪个',p)
        self.assertIn('选第二个',p)

class QueryTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self,answer):
        memory=ChatMemory();memory.add(1,'甲','项目叫星河')
        message=SimpleNamespace(message_id=9,text='它叫什么',reply_to_message=SimpleNamespace(text='原神和东方选一个',caption=None,from_user=SimpleNamespace(full_name='然然',username=None),message_id=2),reply_text=AsyncMock())
        update=SimpleNamespace(effective_message=message,effective_chat=SimpleNamespace(id=1,type='private'),effective_user=SimpleNamespace(full_name='甲',username=None))
        provider=SimpleNamespace(ask=AsyncMock(return_value=answer))
        ctx=SimpleNamespace(bot=SimpleNamespace(),bot_data={'memory':memory,'locks':{},'settings':SimpleNamespace(secrets=()),'deepseek':provider,'reply_models':{},'runtime':make_runtime(provider,Path(tempfile.mkdtemp()))})
        return update,ctx,provider
    async def test_actual_history_and_quote_reach_provider(self):
        from bot.main import _run_query
        u,c,p=self.fixture('叫星河呀')
        with patch('bot.main._deliver_reply',AsyncMock()),patch('bot.main._store_model'):
            await _run_query(u,c,'flash','它叫什么',trigger='私聊')
        prompt=p.ask.call_args.args[0]
        self.assertIn('项目叫星河',prompt);self.assertIn('原神和东方选一个',prompt)
        self.assertEqual(prompt.count('它叫什么'),1)
    async def test_auto_skip_is_silent(self):
        from bot.main import _run_query
        u,c,p=self.fixture('[SKIP_REPLY]')
        with patch('bot.main._deliver_reply',AsyncMock()) as deliver,patch('bot.main._store_model'):
            await _run_query(u,c,'flash','他们在聊天',trigger='主动接话')
            deliver.assert_not_awaited()
            u.effective_message.reply_text.assert_not_awaited()
    async def test_explicit_request_not_silently_skipped(self):
        from bot.main import _run_query
        u,c,p=self.fixture('[SKIP_REPLY]')
        with patch('bot.main._deliver_reply',AsyncMock()) as deliver,patch('bot.main._store_model'):
            await _run_query(u,c,'flash','你好',trigger='私聊')
            self.assertNotIn('[SKIP_REPLY]',deliver.call_args.args[5])
    async def test_history_built_after_chat_lock(self):
        from bot.main import _run_query
        u,c,p=self.fixture('明白')
        lock=asyncio.Lock();await lock.acquire();c.bot_data['locks'][1]=lock
        with patch('bot.main._deliver_reply',AsyncMock()),patch('bot.main._store_model'):
            task=asyncio.create_task(_run_query(u,c,'flash','接着说',trigger='私聊'))
            await asyncio.sleep(0)
            c.bot_data['memory'].add(1,'然然','上一轮刚完成')
            lock.release();await task
        self.assertIn('上一轮刚完成',p.ask.call_args.args[0])
