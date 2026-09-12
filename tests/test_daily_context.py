import tempfile
import unittest
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.core.runtime import RanranRuntime
from bot.daily_analysis import DailyAnalyzer, split_transcript
from bot.daily_journal import BEIJING, DailyJournal, DailySnapshot


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


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.sqlite3';self.j=DailyJournal(self.path)
        # 保留期按"今天"裁剪，所以用相对日期，别让测试随着日历过期。
        self.day=datetime.now(BEIJING).date()
    def tearDown(self):self.tmp.cleanup()
    def add(self,mid,text='文字',chat=1,user=7,when=None,edited=None):
        self.j.add(chat_id=chat,user_id=user,message_id=mid,name='同名',text=text,kind='text',sent_at=when or datetime.combine(self.day,time(4),tzinfo=UTC),edited_at=edited)
    def test_entire_day_beyond_recent_40_and_restart(self):
        for i in range(150):self.add(i,f'发言{i}')
        snap=DailyJournal(self.path).snapshot(1,7,self.day)
        self.assertEqual(snap.count,150);self.assertIn('发言0',snap.transcript);self.assertIn('发言149',snap.transcript)
    def test_identity_chat_date_isolation(self):
        self.add(1,'本人今天');self.add(2,'同名别人',user=8);self.add(3,'另一个群',chat=2)
        self.add(4,'昨天',when=datetime.combine(self.day-timedelta(days=1),time(4),tzinfo=UTC))
        snap=self.j.snapshot(1,7,self.day)
        self.assertEqual(snap.count,1);self.assertIn('本人今天',snap.transcript)
        for secret in ('同名别人','另一个群','昨天'):self.assertNotIn(secret,snap.transcript)
    def test_midnight_uses_message_time(self):
        self.add(1,'午夜前',when=datetime.combine(self.day,time(15,59),tzinfo=UTC))
        self.add(2,'午夜后',when=datetime.combine(self.day,time(16,0),tzinfo=UTC))
        self.assertEqual(self.j.snapshot(1,7,self.day).count,1)
        self.assertEqual(self.j.snapshot(1,7,self.day+timedelta(days=1)).count,1)
    def test_edits_deduplicate_and_keep_original_day(self):
        self.add(1,'旧');self.add(1,'更新',edited=datetime.combine(self.day+timedelta(days=1),time(4),tzinfo=UTC));self.add(1,'旧')
        snap=self.j.snapshot(1,7,self.day);self.assertEqual(snap.count,1)
        self.assertIn('更新',snap.transcript);self.assertNotIn('旧',snap.transcript)
    def test_no_message_truncation(self):
        self.add(1,'开头'+'中'*7000+'结尾')
        text=self.j.snapshot(1,7,self.day).transcript
        self.assertIn('结尾',text);self.assertEqual(text.count('中'),7000)
    def test_retention(self):
        old=self.day-timedelta(days=30)
        self.add(1,'旧',when=datetime.combine(old,time(4),tzinfo=UTC));self.add(2,'今天')
        self.j.prune(self.day)
        self.assertEqual(self.j.snapshot(1,7,old).count,0)
        self.assertEqual(self.j.snapshot(1,7,self.day).count,1)

class AnalyzerTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self,text,count=100,user=7):return DailySnapshot(1,user,date(2026,9,5),count,text,'2026-09-05T00:00:00+08:00')
    async def test_every_character_enters_chunk_analysis(self):
        text='START'+('abc中文\n'*4000)+'END';chunks=split_transcript(text)
        self.assertEqual(''.join(chunks),text);self.assertTrue(all(len(c)<=7000 for c in chunks))
        ask=AsyncMock(return_value='明确事实：用户忙于项目。证据：本人表述。')
        result=await DailyAnalyzer().analyze(self.snapshot(text),ask,model_key='flash')
        self.assertTrue(result.complete);self.assertEqual(result.source_chunks,len(chunks))
        for c in chunks:self.assertTrue(any(c in call.args[0] for call in ask.call_args_list))
    async def test_cache_is_identity_and_content_scoped(self):
        analyzer=DailyAnalyzer();ask=AsyncMock(return_value='事实：准备面试。')
        await analyzer.analyze(self.snapshot('本人发言'),ask,model_key='flash')
        await analyzer.analyze(self.snapshot('本人发言'),ask,model_key='flash');self.assertEqual(ask.await_count,1)
        await analyzer.analyze(self.snapshot('本人发言',user=8),ask,model_key='flash');self.assertEqual(ask.await_count,2)
        await analyzer.analyze(self.snapshot('本人新增内容'),ask,model_key='flash');self.assertEqual(ask.await_count,3)
    async def test_empty_does_not_invent(self):
        ask=AsyncMock();result=await DailyAnalyzer().analyze(self.snapshot('',count=0),ask,model_key='flash')
        ask.assert_not_awaited();self.assertIn('没有',result.text)
    async def test_reduces_all_long_summaries(self):
        ask=AsyncMock(return_value='事实与证据。'*250)
        result=await DailyAnalyzer().analyze(self.snapshot('a'*70000),ask,model_key='flash')
        self.assertTrue(result.complete);self.assertEqual(result.source_chunks,10)
        self.assertGreater(ask.await_count,10)
        self.assertLess(len(result.text),7400)
    async def test_failed_analysis_not_cached(self):
        analyzer=DailyAnalyzer();ask=AsyncMock(side_effect=RuntimeError('offline'))
        snap=self.snapshot('今天有面试')
        await analyzer.analyze(snap,ask,model_key='flash')
        ask.side_effect=None;ask.return_value='事实：今天有面试。'
        result=await analyzer.analyze(snap,ask,model_key='flash')
        self.assertTrue(result.complete);self.assertEqual(ask.await_count,2)
    async def test_failure_never_claims_complete(self):
        ask=AsyncMock(side_effect=RuntimeError('offline'))
        result=await DailyAnalyzer().analyze(self.snapshot('有消息'),ask,model_key='flash')
        self.assertFalse(result.complete);self.assertIn('未完成',result.text)

class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_and_real_user(self):
        from bot.main import on_daily_message
        with tempfile.TemporaryDirectory() as td:
            j=DailyJournal(Path(td)/'journal.db');ctx=SimpleNamespace(bot_data={'daily_journal':j})
            day=datetime.now(BEIJING).date()
            msg=SimpleNamespace(message_id=1,text='今天面试',caption=None,date=datetime.combine(day,time(4),tzinfo=UTC),edit_date=None,sender_chat=None,forward_origin=None)
            u=SimpleNamespace(effective_chat=SimpleNamespace(id=1),effective_user=SimpleNamespace(id=7,full_name='小七',username=None,is_bot=False),effective_message=msg)
            with patch('bot.main._can_use',return_value=False):await on_daily_message(u,ctx)
            self.assertEqual(j.snapshot(1,7,day).count,0)
            with patch('bot.main._can_use',return_value=True):await on_daily_message(u,ctx)
            self.assertEqual(j.snapshot(1,7,day).count,1)
    async def test_caption_forward_voice_and_bot_exclusion(self):
        from bot.main import on_daily_message
        with tempfile.TemporaryDirectory() as td:
            j=DailyJournal(Path(td)/'journal.db');ctx=SimpleNamespace(bot_data={'daily_journal':j})
            user=SimpleNamespace(id=7,full_name='小七',username=None,is_bot=False)
            day=datetime.now(BEIJING).date()
            msg=SimpleNamespace(message_id=1,text=None,caption='作品集封面做好了',date=datetime.combine(day,time(4),tzinfo=UTC),edit_date=None,sender_chat=None,forward_origin=None)
            u=SimpleNamespace(effective_chat=SimpleNamespace(id=1),effective_user=user,effective_message=msg)
            with patch('bot.main._can_use',return_value=True):
                await on_daily_message(u,ctx)
                msg.message_id=2;msg.caption=None;msg.voice=True;await on_daily_message(u,ctx)
                msg.message_id=3;msg.text='转来的故事';msg.forward_origin=object();await on_daily_message(u,ctx)
                msg.message_id=4;user.is_bot=True;await on_daily_message(u,ctx)
            snap=j.snapshot(1,7,day);self.assertEqual(snap.count,3)
            self.assertIn('作品集封面做好了',snap.transcript)
            self.assertIn('未转录或识别',snap.transcript)
            self.assertIn('不视为本人的经历',snap.transcript)
    async def test_final_reading_uses_own_daily_analysis_not_other_people(self):
        from bot.chat_state import ChatMemory
        from bot.fortune import build_fortune
        from bot.main import _interpret_fortune
        with tempfile.TemporaryDirectory() as td:
            j=DailyJournal(Path(td)/'journal.db')
            j.add(chat_id=1,user_id=7,message_id=1,name='小七',text='我上午在准备面试',kind='text',sent_at=datetime(2026,9,5,1,tzinfo=UTC))
            m=ChatMemory();m.add(1,'别人','我刚失恋了')
            msg=SimpleNamespace(message_id=2,reply_to_message=None,reply_text=AsyncMock())
            u=SimpleNamespace(effective_chat=SimpleNamespace(id=1,type='group'),effective_user=SimpleNamespace(id=7,full_name='小七',username=None),effective_message=msg)
            provider=SimpleNamespace(ask=AsyncMock(side_effect=['明确事实：上午准备面试。证据：本人发言。','（展开签纸）面试前先慢慢呼吸。']))
            c=SimpleNamespace(bot=SimpleNamespace(),bot_data={'daily_journal':j,'daily_analyzer':DailyAnalyzer(),'memory':m,'locks':{},'settings':SimpleNamespace(secrets=()),'deepseek':provider,'reply_models':{},'chat_state':SimpleNamespace(get_model=lambda cid:'flash'),'runtime':make_runtime(provider,Path(tempfile.mkdtemp()))})
            with patch('bot.main._deliver_reply',AsyncMock()),patch('bot.main._store_model'):
                await _interpret_fortune(u,c,build_fortune(7,'小七',date(2026,9,5)))
            self.assertEqual(provider.ask.await_count,2)
            final=provider.ask.call_args.args[0];self.assertIn('准备面试',final);self.assertNotIn('失恋',final)
