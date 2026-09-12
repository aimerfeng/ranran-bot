import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace as NS
from tempfile import NamedTemporaryFile

class PackTests(unittest.IsolatedAsyncioTestCase):
 async def test_stable_name_and_create_then_add_and_rename(self):
  from bot.sticker_packs import GroupStickerPacks
  bot=NS(get_me=AsyncMock(return_value=NS(username='mybot')),create_new_sticker_set=AsyncMock(return_value=True),add_sticker_to_set=AsyncMock(return_value=True),set_sticker_set_title=AsyncMock(return_value=True))
  packs=GroupStickerPacks(__import__('pathlib').Path(NamedTemporaryFile(delete=False).name))
  a=await packs.add(bot,chat_id=-1001,title='旧群',user_id=7,file_id='f1')
  b=await packs.add(bot,chat_id=-1001,title='新群',user_id=7,file_id='f2')
  self.assertEqual(a.name,b.name); bot.create_new_sticker_set.assert_awaited_once(); bot.add_sticker_to_set.assert_awaited_once(); bot.set_sticker_set_title.assert_awaited_once_with(a.name,'新群')


import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace as NS

class GroupPackCommandTests(unittest.IsolatedAsyncioTestCase):
 async def test_success_reports_add_sticker_link(self):
  from bot.sticker_commands import _ensure_group_pack
  pack=NS(name='group_x_by_bot',title='测试群',ids=('x',))
  msg=NS(reply_text=AsyncMock())
  ctx=NS(bot_data={'group_packs':NS(add=AsyncMock(return_value=pack)),'settings':NS(owner_id=7)},bot=NS())
  update=NS(effective_chat=NS(id=-100,title='测试群'),effective_message=msg)
  await _ensure_group_pack(update,ctx,'x')
  self.assertIn('https://t.me/addstickers/group_x_by_bot',msg.reply_text.call_args.args[0])
