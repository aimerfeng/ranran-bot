"""ranran 的 MCP server：把核心能力暴露给任意支持 MCP 的平台。

默认 stdio 传输（Claude Desktop / Cursor / Claude Code 等 MCP 客户端可直接接），
也可以用 --http 起 streamable-http 供远程平台接入。

启动：
    python -m adapters.mcp.server            # stdio
    python -m adapters.mcp.server --http     # streamable-http
"""
from __future__ import annotations

import argparse
import logging
import sys
from types import SimpleNamespace
from typing import Any

from mcp.server.mcpserver import MCPServer

from bot.core.runtime import RanranRuntime, runtime_from_env
from bot.files import FileError, create_artifact
from bot.websearch import WebSearchError, format_search_sources

logger = logging.getLogger(__name__)

INSTRUCTIONS = """你是「然然」的能力入口。

- 想要一个有性格、会记住上下文的对话角色，用 ranran_chat（记得传稳定的 session_id）。
- 只想要能力（联网搜索、生成文件、技能说明、回溯会话历史）就直接调对应工具。
- 联网搜索会消耗一次模型轮次，别为了闲聊反复调用。
- 成人向外部人设默认关闭，只有部署者显式开启后才生效。"""

TOOL_NAMES = (
    "ranran_chat",
    "ranran_session",
    "web_search",
    "write_file",
    "list_skills",
    "use_skill",
    "search_history",
)


def build_server(runtime: RanranRuntime | None = None) -> MCPServer:
    """装配 MCP 服务；runtime 可注入，便于测试。"""
    runtime = runtime if runtime is not None else runtime_from_env()
    server = MCPServer(
        name="ranran",
        title="然然 bot",
        instructions=INSTRUCTIONS,
        version="1.0.0",
    )
    outbox = runtime.settings.data_dir / "outbox" / "mcp"

    @server.tool(
        name="ranran_chat",
        description=(
            "和「然然」聊一句并拿到回复。她会用自己的角色设定和 skill 说话，"
            "能联网搜索、生成文件，并按 session_id 记住上下文。适合接入群聊/私聊机器人。"
        ),
    )
    async def ranran_chat(message: str, session_id: str = "default", speaker: str = "用户") -> str:
        """给然然发一条消息。

        Args:
            message: 用户说的话。
            session_id: 会话标识（建议用平台的用户 ID 或频道 ID），同一个 id 共享记忆。
            speaker: 说话人昵称，便于她区分是谁在讲。
        """
        reply = await runtime.chat(session_id, message, speaker=speaker)
        parts = [reply.text or "（这次没说出内容，换个说法再试）"]
        if reply.sources:
            parts.append(format_search_sources(reply.sources))
        if reply.artifacts:
            lines = "\n".join("- " + str(a.path) + f"（{a.size} 字节）" for a in reply.artifacts)
            parts.append("这次还生成了文件，直接读这些路径：\n" + lines)
        return "\n\n".join(parts)

    @server.tool(
        name="ranran_session",
        description="查看或调整某个会话的模型与外部人设开关（成人向人设默认关闭）。",
    )
    async def ranran_session(session_id: str = "default", model: str = "", nsfw: str = "") -> str:
        """读写会话配置：model 传 flash/pro/codex 等 key，nsfw 传 on/off，留空表示只读。"""
        info = runtime.session(session_id)
        changed: list[str] = []
        if model:
            try:
                key = runtime.set_session_model(session_id, model)
            except Exception as exc:  # 参数错误要原样回给调用方，而不是抛协议错误
                return f"设置模型失败：{exc}"
            info = SimpleNamespace(
                session_id=info.session_id, model_key=key,
                nsfw=info.nsfw, memory_key=info.memory_key,
            )
            changed.append(f"模型改为 {key}")
        if nsfw:
            enabled = nsfw.strip().lower() in {"on", "1", "true", "yes", "开"}
            runtime.set_session_nsfw(session_id, enabled)
            info = SimpleNamespace(
                session_id=info.session_id, model_key=info.model_key,
                nsfw=enabled, memory_key=info.memory_key,
            )
            changed.append("外部人设" + ("打开" if enabled else "关闭"))
        lines = [f"会话 {info.session_id}：模型 {info.model_key}，外部人设 " + ("开" if info.nsfw else "关")]
        if changed:
            lines.append("已更新：" + "、".join(changed))
        if info.nsfw and not runtime.persona_extra:
            lines.append("注意：外部人设文件没读到内容，打开也不会生效。")
        return "\n".join(lines)

    @server.tool(
        name="web_search",
        description="联网搜索实时信息（新闻、价格、天气、赛况等），返回标题、链接与摘要。",
    )
    async def web_search(query: str) -> str:
        """只做检索、不经过人格，适合给外部 agent 当资料工具。"""
        try:
            report = await runtime.search(query)
        except WebSearchError as exc:
            return f"联网搜索失败：{exc}"
        lines = [f"「{query}」的搜索结果（{len(report.hits)} 条）："]
        for index, hit in enumerate(report.hits, 1):
            block = [f"[{index}] {hit.title or hit.url}"]
            if hit.page_age:
                block.append(f"页面时间：{hit.page_age}")
            if hit.snippet:
                block.append(f"摘要：{hit.snippet}")
            block.append(f"链接：{hit.url}")
            lines.append("\n".join(block))
        if report.summary:
            lines.append("搜索服务汇总（仅供参考）：\n" + report.summary)
        return "\n\n".join(lines)

    @server.tool(
        name="write_file",
        description="把内容写成 md/txt/csv 文件并返回路径，方便调用方上传或展示。",
    )
    async def write_file(filename: str, content: str, format: str = "") -> str:
        """csv 会做规范转义并带 BOM，Excel 打开不乱码。"""
        try:
            artifact = create_artifact(outbox, filename=filename, content=content, fmt=format)
        except FileError as exc:
            return f"生成文件失败：{exc}"
        return f"已写入 {artifact.path}（{artifact.filename}，{artifact.size} 字节）"

    @server.tool(
        name="list_skills",
        description="列出然然可用的 skill（技能说明），需要时再用 use_skill 取正文。",
    )
    async def list_skills() -> str:
        catalog = runtime.skills.catalog() if runtime.skills else ""
        if not catalog:
            return "当前没有配置 skill。"
        return "可用 skill：\n" + catalog

    @server.tool(
        name="use_skill",
        description="取出某个 skill 的完整正文（含嵌套内容），按它执行。",
    )
    async def use_skill(name: str) -> str:
        body = runtime.skills.expand(name) if runtime.skills else ""
        if not body:
            available = "、".join(runtime.skills.names()) if runtime.skills else "无"
            return f"没有名为 {name} 的 skill。可用：{available}"
        return body

    @server.tool(
        name="search_history",
        description="在某个会话的历史记录里按关键词回查，用来确认之前聊过什么。",
    )
    async def search_history(keyword: str, session_id: str = "default") -> str:
        history = runtime.history(session_id)
        if not history:
            return f"会话 {session_id} 还没有历史记录。"
        word = (keyword or "").strip()
        if not word:
            return history
        hits = [line for line in history.splitlines() if word in line]
        if not hits:
            return f"会话 {session_id} 的历史里没有包含「{word}」的内容。"
        return f"会话 {session_id} 里包含「{word}」的记录：\n" + "\n".join(hits)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ranran 的 MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http", "sse"],
        help="传输方式，默认 stdio",
    )
    parser.add_argument("--http", action="store_true", help="等价于 --transport streamable-http")
    parser.add_argument("-v", "--verbose", action="store_true", help="打开调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdio 传输时 stdout 只能承载协议帧
    )
    transport = "streamable-http" if args.http else args.transport
    try:
        server = build_server()
    except SystemExit as exc:  # 常见于缺少 .env 配置
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2
    logger.info("ranran MCP server 启动，传输方式 %s", transport)
    server.run(transport=transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
