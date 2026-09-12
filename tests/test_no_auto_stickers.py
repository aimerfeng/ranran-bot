import ast,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from bot.chat_state import ChatMemory

class NoAutoStickerTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        msg=SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(message_id=18)),reply_sticker=AsyncMock())
        update=SimpleNamespace(effective_message=msg,effective_chat=SimpleNamespace(id=1))
        bank=SimpleNamespace(pick=Mock(return_value=SimpleNamespace(file_id='fake-sticker')))
        context=SimpleNamespace(bot_data={'memory':ChatMemory(),'reply_models':{},'stickers':bank})
        return update,context,bank
    async def test_tagged_replies_never_send_stickers(self):
        from bot.main import _deliver_reply
        for trigger in ('私聊','@提到你','回复你的消息','每日解签','主动接话','有人丢表情'):
            u,c,bank=self.fixture()
            with patch('bot.main._store_model'):
                await _deliver_reply(u,c,None,'flash',trigger,'今天也慢慢来呀。\n[表情:笑]',playful=True)
            u.effective_message.reply_sticker.assert_not_awaited()
            self.assertEqual(u.effective_message.reply_text.call_args.args[0],'今天也慢慢来呀。')
            bank.pick.assert_not_called()
    async def test_plain_reply_does_not_randomly_attach(self):
        from bot.main import _deliver_reply
        u,c,bank=self.fixture()
        with patch('bot.stickers.random.random',return_value=0),patch('bot.main._store_model'):
            await _deliver_reply(u,c,None,'flash','私聊','你好呀',playful=True)
        u.effective_message.reply_sticker.assert_not_awaited()
    async def test_tag_only_direct_reply_still_has_text(self):
        from bot.main import _deliver_reply
        u,c,bank=self.fixture()
        with patch('bot.main._store_model'):
            await _deliver_reply(u,c,None,'flash','私聊','[表情:爱]',playful=True)
        u.effective_message.reply_sticker.assert_not_awaited()
        self.assertTrue(u.effective_message.reply_text.call_args.args[0])
        self.assertNotIn('[表情',u.effective_message.reply_text.call_args.args[0])
    async def test_tag_only_auto_reply_is_silent(self):
        from bot.main import _deliver_reply
        u,c,bank=self.fixture()
        await _deliver_reply(u,c,None,'flash','主动接话','[表情:笑]',playful=True)
        u.effective_message.reply_text.assert_not_awaited();u.effective_message.reply_sticker.assert_not_awaited()
    async def test_error_text_also_strips_tags(self):
        from bot.main import _deliver_reply
        u,c,bank=self.fixture()
        with patch('bot.main._store_model'):
            await _deliver_reply(u,c,None,'flash','私聊','稍后再试。 [表情:哭]',playful=False)
        self.assertEqual(u.effective_message.reply_text.call_args.args[0],'稍后再试。')
    async def test_welcome_is_text_only(self):
        from bot.main import on_new_members
        u,c,bank=self.fixture();c.bot=SimpleNamespace(id=99)
        u.effective_message.new_chat_members=[SimpleNamespace(id=7,is_bot=False,full_name='小七',username=None)]
        with patch('bot.main._deny_if_unauthorized',AsyncMock(return_value=False)):
            await on_new_members(u,c)
        u.effective_message.reply_text.assert_awaited_once();u.effective_message.reply_sticker.assert_not_awaited()

class PromptTests(unittest.TestCase):
    def test_models_instructed_to_avoid_sticker_tags(self):
        from bot.persona import IMMERSIVE_STYLE
        from bot.fortune_reading import FORTUNE_READING_SYSTEM
        self.assertNotIn('想丢表情包可以',IMMERSIVE_STYLE)
        self.assertNotIn('可选一个合适的 [表情:标签]',FORTUNE_READING_SYSTEM)
