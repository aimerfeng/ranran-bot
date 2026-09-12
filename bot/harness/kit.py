"""内置工具集：联网搜索、生成文件、按需加载 skill、回查聊天历史。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.files import FileError, create_artifact
from bot.harness.skills import SkillRegistry
from bot.harness.tools import ToolOutcome, ToolRegistry, json_tool
from bot.harness.types import Artifact
from bot.websearch import WebSearchError, deepseek_web_search

logger = logging.getLogger(__name__)

DEFAULT_MAX_SEARCHES = 3


@dataclass
class ToolKit:
    """一次回合里工具的共享状态：来源、用过的 skill、搜索次数。"""

    registry: ToolRegistry
    search_hits: list[Any] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    searches: int = 0
    files: list[Artifact] = field(default_factory=list)


def build_tools(
    *,
    api_key: str,
    model: str,
    outbox_dir: Path,
    skills: SkillRegistry | None = None,
    journal: Any = None,
    chat_id: int | None = None,
    max_searches: int = DEFAULT_MAX_SEARCHES,
) -> ToolKit:
    """装配模型可用的工具。"""
    kit = ToolKit(registry=ToolRegistry())

    async def web_search(query: str) -> ToolOutcome:
        query = (query or "").strip()
        if not query:
            return ToolOutcome(content="搜索词是空的，请给出具体关键词。")
        if kit.searches >= max_searches:
            return ToolOutcome(
                content=f"本次回复最多搜 {max_searches} 次，已经用完了，请基于现有结果回答。",
            )
        kit.searches += 1
        try:
            report = await deepseek_web_search(api_key, query, model=model)
        except WebSearchError as exc:
            return ToolOutcome(content=f"联网搜索失败：{exc}（可以换关键词再试，或直接说查不到）")

        fresh = [hit for hit in report.hits if hit.url not in {h.url for h in kit.search_hits}]
        kit.search_hits.extend(fresh)
        lines = [f"搜索「{query}」返回 {len(report.hits)} 条结果（全局累计 {len(kit.search_hits)} 条来源）："]
        for index, hit in enumerate(report.hits, 1):
            parts = [f"[{index}] {hit.title or hit.url}"]
            if hit.page_age:
                parts.append(f"页面时间：{hit.page_age}")
            if hit.snippet:
                parts.append(f"摘要：{hit.snippet}")
            parts.append(f"链接：{hit.url}")
            lines.append("\n".join(parts))
        if report.summary:
            lines.append("搜索服务汇总（仅供参考，引用以编号为准）：\n" + report.summary)
        lines.append("信息不够可以换个关键词再调用一次 web_search；来源链接系统会自动附在回答后面，你不用重复贴。")
        return ToolOutcome(content="\n\n".join(lines))

    async def write_file(filename: str, content: str, format: str = "") -> ToolOutcome:
        try:
            artifact = create_artifact(
                Path(outbox_dir), filename=filename, content=content, fmt=format
            )
        except FileError as exc:
            return ToolOutcome(content=f"生成文件失败：{exc}")
        kit.files.append(artifact)
        return ToolOutcome(
            content=(
                f"已生成文件 {artifact.filename}（{artifact.size} 字节）。"
                "它会在这条回复发出后作为文档发给用户，正文里简单说一句即可，不要整段复述文件内容。"
            ),
            artifacts=[artifact],
        )

    async def use_skill(name: str) -> ToolOutcome:
        wanted = (name or "").strip()
        if skills is None:
            return ToolOutcome(content="这台 bot 目前没有配置 skill。")
        body = skills.expand(wanted)
        if not body:
            available = ", ".join(skills.names()) or "无"
            return ToolOutcome(content=f"没有名为 {wanted} 的 skill。可用：{available}")
        if wanted not in kit.skills_used:
            kit.skills_used.append(wanted)
        return ToolOutcome(
            content=f"已加载 skill「{wanted}」，接下来的回复按它执行：\n\n{body}",
        )

    async def search_chat_history(keyword: str, days: int = 3) -> ToolOutcome:
        if journal is None or chat_id is None:
            return ToolOutcome(content="这个聊天没有可回查的历史记录。")
        try:
            rows = journal.search(chat_id, keyword, days=int(days or 3))
        except Exception as exc:
            logger.warning("History search failed: %s", exc)
            return ToolOutcome(content="历史记录查询失败。")
        if not rows:
            return ToolOutcome(content=f"最近几天没有找到包含「{keyword}」的记录；不要编造当时说过的话。")
        return ToolOutcome(
            content=f"最近几天包含「{keyword}」的记录（{len(rows)} 条，来自真实聊天记录）：\n" + "\n".join(rows),
        )

    kit.registry.add(json_tool(
        "web_search",
        "联网搜索实时信息（新闻、价格、天气、比分、最近发生的事）。信息不够时可以换关键词再搜一次。",
        {"query": {"type": "string", "description": "搜索关键词，尽量具体"}},
        web_search,
    ))
    kit.registry.add(json_tool(
        "write_file",
        ("生成文件并发给用户。用户要表格、清单、导出、长文档、可下载的东西时用它。"
         "format 取 md/txt/csv；csv 必须是带表头的规范 CSV（英文逗号分隔）。"),
        {
            "filename": {"type": "string", "description": "文件名，可带扩展名，例如 员工表.csv"},
            "content": {"type": "string", "description": "完整文件内容"},
            "format": {"type": "string", "enum": ["md", "txt", "csv"], "description": "文件格式"},
        },
        write_file,
        required=["filename", "content"],
    ))
    kit.registry.add(json_tool(
        "use_skill",
        "加载一份技能说明，用来改善表达方式或上下文处理（真人感、情绪支持、群聊礼仪、文件输出等）。",
        {"name": {"type": "string", "description": "skill 名字，例如 conversation/human-voice"}},
        use_skill,
    ))
    kit.registry.add(json_tool(
        "search_chat_history",
        "在最近几天的真实聊天记录里按关键词回查（用于确认之前说过的细节，避免记错或编造）。",
        {
            "keyword": {"type": "string", "description": "要回查的关键词"},
            "days": {"type": "integer", "description": "往回查几天，默认 3"},
        },
        search_chat_history,
        required=["keyword"],
    ))
    return kit
