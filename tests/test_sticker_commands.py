import importlib.util
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from telegram import InputFile, Update, Message, Chat, User, PhotoSize, Document, Sticker, MessageEntity
from telegram.error import BadRequest
from test_sticker_maker import image_bytes


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('bot.sticker_commands'), '制图命令尚未实现')
        from bot import sticker_commands
        return sticker_commands

    def fixture(self, text='/sticker', source=None, allowed=True):
        msg=NS(text=text, reply_to_message=source, reply_text=AsyncMock(), reply_sticker=AsyncMock(), reply_photo=AsyncMock())
        msg.reply_sticker.return_value=NS(sticker=NS(file_id='result-id'))
        ctx=NS(args=None, bot=NS(get_file=AsyncMock()), bot_data={'settings':NS(owner_id=7), 'whitelist':NS(contains=lambda _:allowed)})
        update=NS(effective_message=msg,effective_user=NS(id=7,is_bot=False),effective_chat=NS(id=-100,type='supergroup'))
        return update,ctx

    def source(self, **kw):
        return NS(photo=(),document=None,sticker=None,animation=None,video=None,text=None,caption=None,from_user=NS(full_name='小七'),sender_chat=None,forward_origin=None,message_id=123,**kw) if not kw else NS(**dict(dict(photo=(),document=None,sticker=None,animation=None,video=None,text=None,caption=None,from_user=NS(full_name='小七'),sender_chat=None,forward_origin=None,message_id=123),**kw))

    async def test_missing_source_help(self):
        m=self.module(); u,c=self.fixture()
        await m.sticker_cmd(u,c)
        self.assertIn('回复一张',u.effective_message.reply_text.call_args.args[0])
        c.bot.get_file.assert_not_awaited()

    async def test_denied_all_commands(self):
        m=self.module()
        for cmd in (m.sticker_cmd,m.quote_cmd,m.choose_cmd):
            u,c=self.fixture(allowed=False)
            await cmd(u,c)
            u.effective_message.reply_text.assert_not_awaited()
            u.effective_message.reply_sticker.assert_not_awaited()
            u.effective_message.reply_photo.assert_not_awaited()

    async def test_photo_caption_cache_and_different_caption(self):
        m=self.module(); src=self.source(photo=[NS(file_id='small',file_unique_id='s',file_size=100),NS(file_id='large',file_unique_id='l',file_size=100)])
        u,c=self.fixture('/sticker@mybot 我真的谢',src)
        with patch.object(m,'download_source',AsyncMock(return_value=image_bytes())) as download:
            await m.sticker_cmd(u,c)
            self.assertIsInstance(u.effective_message.reply_sticker.call_args.args[0],InputFile)
            self.assertEqual(download.call_args.args[1].file_id,'large')
            c.bot_data['fun_media'].cooldowns.clear()
            await m.sticker_cmd(u,c)
            self.assertEqual(u.effective_message.reply_sticker.call_args.args[0],'result-id')
            self.assertEqual(download.await_count,1)
            c.bot_data['fun_media'].cooldowns.clear(); u.effective_message.text='/sticker 另一句'
            await m.sticker_cmd(u,c)
            self.assertEqual(download.await_count,2)

    async def test_source_formats_and_oversize(self):
        m=self.module()
        for source in (self.source(document=NS(file_id='doc',file_unique_id='d',file_size=50)),self.source(sticker=NS(file_id='st',file_unique_id='st',file_size=50,is_animated=False,is_video=False))):
            u,c=self.fixture(source=source)
            with patch.object(m,'download_source',AsyncMock(return_value=image_bytes())):
                await m.sticker_cmd(u,c)
            u.effective_message.reply_sticker.assert_awaited_once()
        for source in (self.source(animation=object()),self.source(sticker=NS(is_animated=True,is_video=False)),self.source(document=NS(file_id='doc',file_size=20*1024*1024))):
            u,c=self.fixture(source=source)
            await m.sticker_cmd(u,c)
            u.effective_message.reply_sticker.assert_not_awaited()
            u.effective_message.reply_text.assert_awaited_once()

    async def test_upload_error_and_cooldown(self):
        m=self.module(); u,c=self.fixture(source=self.source(photo=[NS(file_id='a',file_unique_id='a',file_size=50)]))
        with patch.object(m,'download_source',AsyncMock(return_value=image_bytes())):
            u.effective_message.reply_sticker.side_effect=BadRequest('upload failed')
            await m.sticker_cmd(u,c)
        self.assertIn('发送失败',u.effective_message.reply_text.call_args.args[0])
        await m.sticker_cmd(u,c)
        self.assertIn('秒',u.effective_message.reply_text.call_args.args[0])

    async def test_quote_original_not_command_arguments(self):
        m=self.module(); u,c=self.fixture('/quote',self.source(text='明天一定早睡。'))
        with patch.object(m,'make_quote',return_value=b'png') as render:
            await m.quote_cmd(u,c)
        render.assert_called_once_with('明天一定早睡。','小七',123)
        u.effective_message.reply_photo.assert_awaited_once()
        u,c=self.fixture('/quote 编造句子',self.source(text='原话'))
        await m.quote_cmd(u,c)
        u.effective_message.reply_photo.assert_not_awaited()

    async def test_choose_spaces_and_pipes(self):
        m=self.module()
        for text,choices in (('/choose 火锅 烤肉 拉面',['火锅','烤肉','拉面']),('/choose 出去吃饭 | 在家 做饭',['出去吃饭','在家 做饭'])):
            u,c=self.fixture(text)
            with patch.object(m.secrets,'choice',return_value=choices[-1]) as pick:
                await m.choose_cmd(u,c)
                pick.assert_called_once_with(choices)
            self.assertIn(choices[-1],u.effective_message.reply_text.call_args.args[0])
        for text in ('/choose','/choose 火锅','/choose a || b','/choose a a'):
            u,c=self.fixture(text); await m.choose_cmd(u,c)
            self.assertIn('至少',u.effective_message.reply_text.call_args.args[0])

    async def test_menu_and_routing(self):
        self.module()
        from bot.main import PUBLIC_COMMANDS,HELP_PUBLIC,build_application
        from bot.settings import Settings
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),Path(tmp),'123:abc',7,'','deepseek-chat',180,())
            app=build_application(settings)
        for name in ('sticker','quote','choose'):
            self.assertIn(name,[c.command for c in PUBLIC_COMMANDS])
            self.assertIn('/'+name,HELP_PUBLIC)
            self.assertTrue(any(name in getattr(h,'commands',()) for h in app.handlers[0]))

    async def test_private_access_and_quote_attribution(self):
        m=self.module()
        u,c=self.fixture('/choose a b'); u.effective_chat=NS(id=7,type='private')
        await m.choose_cmd(u,c); u.effective_message.reply_text.assert_awaited_once()
        u,c=self.fixture('/choose a b'); u.effective_chat=NS(id=8,type='private'); u.effective_user=NS(id=8,is_bot=False)
        await m.choose_cmd(u,c); u.effective_message.reply_text.assert_not_awaited()
        for source,expected in ((self.source(sender_chat=NS(title='匿名管理员')),'匿名管理员'),
                                (self.source(forward_origin=NS(sender_user_name='原作者')),'原作者'),
                                (self.source(forward_origin=NS(sender_user=NS(full_name='转发原作者'))),'转发原作者')):
            self.assertEqual(m._author(source),expected)

    async def test_quote_caption_and_missing(self):
        m=self.module(); u,c=self.fixture('/quote',self.source(caption='图的说明'))
        with patch.object(m,'make_quote',return_value=b'png') as render:
            await m.quote_cmd(u,c)
        render.assert_called_once_with('图的说明','小七',123)
        for source in (None,self.source()):
            u,c=self.fixture('/quote',source); await m.quote_cmd(u,c)
            u.effective_message.reply_photo.assert_not_awaited()

    async def test_corrupt_input_and_expired_cache(self):
        m=self.module(); u,c=self.fixture(source=self.source(photo=[NS(file_id='a',file_unique_id='a',file_size=50)]))
        with patch.object(m,'download_source',AsyncMock(return_value=b'invalid')):
            await m.sticker_cmd(u,c)
        u.effective_message.reply_sticker.assert_not_awaited()
        self.assertEqual(len(c.bot_data['fun_media'].cache),0)
        c.bot_data['fun_media'].cooldowns.clear()
        with patch.object(m,'download_source',AsyncMock(return_value=image_bytes())):
            await m.sticker_cmd(u,c)
            c.bot_data['fun_media'].cooldowns.clear()
            u.effective_message.reply_sticker.side_effect=[BadRequest('file id expired'),NS(sticker=NS(file_id='new-id'))]
            await m.sticker_cmd(u,c)
        self.assertIn('new-id',c.bot_data['fun_media'].cache.values())

    async def test_busy_limit_released_and_cache_bounded(self):
        import asyncio
        m=self.module(); runtime=m.FunMediaRuntime(); event=asyncio.Event()
        a=asyncio.create_task(runtime.run(1,event.wait)); b=asyncio.create_task(runtime.run(2,event.wait))
        await asyncio.sleep(0)
        with self.assertRaises(ValueError): await runtime.run(3,event.wait)
        self.assertEqual(runtime.active,2)
        event.set(); await asyncio.gather(a,b)
        self.assertEqual(runtime.active,0)
        async def fail(): raise ValueError('bad')
        with self.assertRaises(ValueError): await runtime.run(4,fail)
        self.assertEqual(runtime.active,0)
        for i in range(300): runtime.remember(i,str(i))
        self.assertEqual(len(runtime.cache),256)

    async def test_real_telegram_command_with_bot_suffix(self):
        m=self.module()
        bot=NS(username='mybot')
        msg=Message(message_id=2,date=datetime.now(timezone.utc),chat=Chat(-100,'supergroup'),from_user=User(7,'小七',False),
                    text='/sticker@mybot 我真的谢',entities=[MessageEntity('bot_command',0,14)])
        msg.set_bot(bot)
        from telegram.ext import CommandHandler
        handler=CommandHandler('sticker',m.sticker_cmd)
        self.assertNotEqual(handler.check_update(Update(1,message=msg)),False)
        self.assertEqual(m._args(Update(1,message=msg),NS(args=['我真的谢'])),'我真的谢')

    async def test_download_size_checks(self):
        m=self.module(); bot=NS(get_file=AsyncMock(return_value=NS(file_size=m.MAX_INPUT_BYTES+1,file_path='unused')))
        with self.assertRaises(ValueError): await m.download_source(bot,NS(file_id='abc'))
        with self.assertRaises(ValueError): m._read_bounded('http://example.com/file')
        with self.assertRaises(ValueError): m._read_bounded('https://example.com/file')
        class Response:
            headers={}
            def __init__(self): self.remaining=m.MAX_INPUT_BYTES+1
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n):
                count=min(n,self.remaining); self.remaining-=count; return b'x'*count
        with patch.object(m.urllib.request,'build_opener',return_value=NS(open=lambda *a,**kw:Response())):
            with self.assertRaises(ValueError): m._read_bounded('https://api.telegram.org/file/botTEST/photo.jpg')

    async def test_sdk_signature_for_actual_sticker_upload(self):
        import inspect
        m=self.module(); u,c=self.fixture(source=self.source(photo=[NS(file_id='a',file_unique_id='a',file_size=50)]))
        with patch.object(m,'download_source',AsyncMock(return_value=image_bytes())):
            await m.sticker_cmd(u,c)
        args,kwargs=u.effective_message.reply_sticker.call_args
        inspect.signature(Message.reply_sticker).bind(None,*args,**kwargs)

    async def test_plural_stickers_routes_media_to_maker_and_keeps_inventory_without_media(self):
        from bot.main import stickers_cmd
        from bot.sticker_commands import sticker_cmd
        u,c=self.fixture('/stickers',self.source(photo=[NS(file_id='a',file_unique_id='a',file_size=50)]))
        with patch('bot.main.sticker_cmd', new=AsyncMock()) as maker:
            await stickers_cmd(u,c)
            maker.assert_awaited_once_with(u,c)
        u,c=self.fixture('/stickers',None)
        bank=NS(summary=lambda:'现在有 59 张表情包',pick=lambda:None)
        c.bot_data['stickers']=bank
        await stickers_cmd(u,c)
        self.assertEqual(u.effective_message.reply_text.call_args.args[0],'现在有 59 张表情包')
