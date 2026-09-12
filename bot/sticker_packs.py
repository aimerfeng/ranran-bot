from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from telegram import InputSticker


@dataclass(frozen=True)
class GroupPack:
 name: str
 title: str
 ids: tuple[str, ...]

class GroupStickerPacks:
 def __init__(self,path:Path): self.path=path; self.items=self._load()
 def _load(self):
  try:return json.loads(self.path.read_text(encoding='utf-8'))
  except (OSError,json.JSONDecodeError):return {}
 def get(self, chat_id:int): return self.items.get(str(chat_id))
 def _save(self): self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_text(json.dumps(self.items,ensure_ascii=False,indent=2),encoding='utf-8')
 async def add(self,bot,chat_id:int,title:str,user_id:int,file_id:str,emoji='🙂'):
  key=str(chat_id); row=self.items.get(key); username=(await bot.get_me()).username or 'bot'
  if not row:
   suffix=hashlib.sha256(key.encode()).hexdigest()[:12]; name=f'group_{suffix}_by_{username}'
   safe=(title or '群聊表情包').strip()[:60] or '群聊表情包'
   await bot.create_new_sticker_set(user_id,name,safe,[InputSticker(sticker=file_id,format='static',emoji_list=[emoji])])
   row={'name':name,'title':safe,'ids':[file_id]};self.items[key]=row
  elif title and title != row['title']:
   await bot.set_sticker_set_title(row['name'],title[:64]);row['title']=title[:64]
  if file_id not in row['ids'] and row.get('name'):
   await bot.add_sticker_to_set(user_id,row['name'],InputSticker(sticker=file_id,format='static',emoji_list=[emoji]));row['ids'].append(file_id)
  self._save();return GroupPack(row['name'],row['title'],tuple(row['ids']))
