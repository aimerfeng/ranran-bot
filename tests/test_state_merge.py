"""多进程共用同一份状态/记忆文件时不能互相覆盖。

MCP server 可能被多个客户端同时打开（比如 Claude Desktop 与 Cursor），
它们读写的是同一个 data/runtime_*.json；整份覆盖会丢掉对方的写入。
"""
import json
import tempfile
import unittest
from pathlib import Path

from bot.chat_state import ChatMemory, ChatStateStore


class ChatStateMergeTests(unittest.TestCase):
    def test_two_stores_do_not_clobber_each_other(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            first, second = ChatStateStore(path), ChatStateStore(path)

            first.set_model(1, "pro")          # 进程 A 改会话 1
            second.set_nsfw(2, True)           # 进程 B 改会话 2（B 启动时还没看到 A 的写入）

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["1"]["model"], "pro")
            self.assertTrue(saved["2"]["nsfw"])

    def test_read_only_access_is_not_written_back(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            writer = ChatStateStore(path)
            writer.set_model(1, "pro")

            reader = ChatStateStore(path)
            reader.get_model(1)                # 只读
            reader.nsfw(2)
            writer.set_model(1, "flash")       # 别处又改了会话 1

            reader.set_auto_reply(3, False)    # 读取方只改会话 3
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["1"]["model"], "flash")   # 不被读取方的旧值盖掉
            self.assertFalse(saved["3"]["auto_reply"])


class ChatMemoryMergeTests(unittest.TestCase):
    def test_two_writers_keep_each_others_lines(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "memory.json"
            first = ChatMemory(path=path)
            second = ChatMemory(path=path)

            first.add(1, "甲", "第一条")
            second.add(1, "乙", "第二条")      # 第二个进程启动时还没读到第一条

            saved = json.loads(path.read_text(encoding="utf-8"))
            texts = [row["text"] for row in saved["1"]]
            self.assertIn("第一条", texts)
            self.assertIn("第二条", texts)

    def test_duplicates_are_not_written_twice(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "memory.json"
            memory = ChatMemory(path=path)
            memory.add(1, "甲", "重复内容")
            memory.add(1, "甲", "重复内容")
            texts = [row["text"] for row in json.loads(path.read_text(encoding="utf-8"))["1"]]
            self.assertEqual(texts.count("重复内容"), 1)


if __name__ == "__main__":
    unittest.main()
