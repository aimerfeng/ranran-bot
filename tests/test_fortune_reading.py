import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.chat_state import ChatMemory
from bot.core.runtime import RanranRuntime
from bot.fortune import HIDDEN_SIGNS, THEME_ALIASES, THEME_HELP, THEMES, build_fortune, send_fortune
from bot.fortune_reading import FORTUNE_READING_SYSTEM, build_reading_prompt, fallback_reading
from bot.providers import DeepSeekError


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


class ReadingTests(unittest.TestCase):
    def test_expanded_themes(self):
        self.assertEqual(len(THEMES),12)
        for alias in ('碧蓝航线','赛马娘','战双','碧蓝幻想','东方归言录','holo','阴阳师','舰r'):
            self.assertIn(THEME_ALIASES[alias],THEMES)
        self.assertIn('/yun 赛马娘',THEME_HELP)
    def test_prompt_contains_real_hidden_and_base_facts(self):
        f=replace(build_fortune(42,'星野',date(2026,9,5)),hidden=HIDDEN_SIGNS[-1])
        prompt=build_reading_prompt(f)
        self.assertIn('空白',prompt);self.assertIn(f.band,prompt)
        self.assertIn(f.hidden.advice,prompt);self.assertIn('SP',prompt)
        self.assertIn(THEMES[f.theme],prompt)
        self.assertNotIn('image_key',prompt)
        self.assertGreater(len(fallback_reading(f)),110)
        self.assertIn('空白',fallback_reading(f))
    def test_no_forced_tiny_responses(self):
        from bot.persona import CODEX_SYSTEM, DEEPSEEK_PERSONA
        self.assertNotIn('@你 / 回复你 / 私聊：一到三句',DEEPSEEK_PERSONA)
        self.assertIn('沉浸',DEEPSEEK_PERSONA)
        self.assertIn('沉浸',CODEX_SYSTEM)
        self.assertIn('180',FORTUNE_READING_SYSTEM)

class ReadingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self,answer='（把签纸摊开）今天先慢慢来。'):
        memory=ChatMemory();memory.add(1,'星野','明天有面试，有点紧张')
        msg=SimpleNamespace(message_id=8,text='/yun',reply_to_message=None,reply_photo=AsyncMock(),reply_text=AsyncMock())
        user=SimpleNamespace(id=42,full_name='星野',username=None)
        update=SimpleNamespace(effective_message=msg,effective_user=user,effective_chat=SimpleNamespace(id=1,type='private'))
        provider=SimpleNamespace(ask=AsyncMock(return_value=answer))
        ctx=SimpleNamespace(args=[],bot=SimpleNamespace(),bot_data={'memory':memory,'locks':{},'settings':SimpleNamespace(secrets=()),'deepseek':provider,'reply_models':{},'chat_state':SimpleNamespace(get_model=lambda cid:'flash'),'runtime':make_runtime(provider,Path(tempfile.mkdtemp()))})
        return update,ctx,provider
    async def test_sender_returns_exact_fortune(self):
        u,c,p=self.fixture()
        f=build_fortune(42,'星野',date(2026,9,5))
        with patch('bot.fortune.build_fortune',return_value=f):
            result=await send_fortune(u.effective_message,42,'星野')
        self.assertIs(result,f)
    async def test_yun_photo_then_contextual_ai(self):
        from bot.main import yun_cmd
        u,c,p=self.fixture()
        f=build_fortune(42,'星野',date(2026,9,5))
        order=[]
        async def send(*args,**kwargs):order.append('photo');return f
        async def ask(*args,**kwargs):order.append('ai');return '签纸展开了，面试前先放慢呼吸。'
        p.ask.side_effect=ask
        with patch('bot.main._deny_if_unauthorized',AsyncMock(return_value=False)),patch('bot.main.send_fortune',side_effect=send),patch('bot.main._deliver_reply',AsyncMock()),patch('bot.main._store_model'):
            await yun_cmd(u,c)
        self.assertEqual(order,['photo','ai'])
        prompt=p.ask.call_args.args[0]
        self.assertIn('明天有面试',prompt);self.assertIn(f.display_advice,prompt)
        self.assertIn(FORTUNE_READING_SYSTEM,p.ask.call_args.kwargs['system'])
        self.assertIn(f.display_advice,c.bot_data['memory'].render(1))
    async def test_reading_failure_keeps_substantial_reply(self):
        from bot.main import _interpret_fortune
        u,c,p=self.fixture();p.ask.side_effect=DeepSeekError('test outage')
        f=build_fortune(42,'星野',date(2026,9,5))
        with patch('bot.main._deliver_reply',AsyncMock()) as deliver,patch('bot.main._store_model'):
            await _interpret_fortune(u,c,f)
        body=deliver.call_args.args[5]
        self.assertGreater(len(body),110);self.assertIn(f.display_title,body)
        self.assertNotIn('test outage',body)
    async def test_empty_reading_uses_local_text(self):
        from bot.main import _interpret_fortune
        u,c,p=self.fixture('');f=build_fortune(42,'星野',date(2026,9,5))
        with patch('bot.main._deliver_reply',AsyncMock()) as deliver,patch('bot.main._store_model'):
            await _interpret_fortune(u,c,f)
        self.assertGreater(len(deliver.call_args.args[5]),110)
    async def test_codex_receives_same_reading_rules(self):
        from bot.main import _interpret_fortune
        u,c,p=self.fixture();c.bot_data['codex']=p;c.bot_data['runtime'].codex=p;c.bot_data['chat_state']=SimpleNamespace(get_model=lambda cid:'codex')
        with patch('bot.main._deliver_reply',AsyncMock()),patch('bot.main._store_model'):
            await _interpret_fortune(u,c,build_fortune(42,'星野'))
        self.assertIn(FORTUNE_READING_SYSTEM,p.ask.call_args.kwargs['system'])
