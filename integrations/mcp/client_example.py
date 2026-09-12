"""直接用 MCP 协议调用 ranran 的最小示例（也用于自测）。

跑之前先装 MCP 依赖：
    pip install -r requirements-mcp.txt

然后：
    python integrations/mcp/client_example.py                    # 跑一遍只读工具
    python integrations/mcp/client_example.py --chat "你好呀"     # 额外发一句给然然
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent.parent


def server_params() -> StdioServerParameters:
    """用当前解释器启动本仓库的 MCP server（stdio）。"""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "adapters.mcp.server"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )


async def run(chat: str | None, session_id: str) -> int:
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            server_info = getattr(info, "server_info", None) or getattr(info, "serverInfo", None)
            print(f"已连接：{server_info.name} {server_info.version}")

            tools = await session.list_tools()
            print(f"工具 {len(tools.tools)} 个：")
            for tool in tools.tools:
                print(f"  - {tool.name}: {(tool.description or '')[:60]}")

            result = await session.call_tool("list_skills", {})
            print("\n[list_skills]")
            print(_text(result)[:300])

            result = await session.call_tool(
                "write_file",
                {"filename": "接入示例.csv", "content": "平台,会话ID\n示例平台,user-1", "format": "csv"},
            )
            print("\n[write_file]")
            print(_text(result))

            if chat:
                result = await session.call_tool(
                    "ranran_chat", {"message": chat, "session_id": session_id, "speaker": "接入方"}
                )
                print(f"\n[ranran_chat] {chat}")
                print(_text(result))
    return 0


def _text(result) -> str:
    """把 MCP 返回的 content 拼成纯文本。"""
    chunks = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks) or str(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="ranran MCP 客户端示例")
    parser.add_argument("--chat", default="", help="额外发一句给然然（会真实调用模型）")
    parser.add_argument("--session-id", default="example-user", help="会话标识")
    args = parser.parse_args()
    return asyncio.run(run(args.chat or None, args.session_id))


if __name__ == "__main__":
    raise SystemExit(main())
