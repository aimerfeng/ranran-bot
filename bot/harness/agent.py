"""Agent 循环：供应商 → 工具调用 → 结果回灌 → 再问，直到给出正文或用完预算。"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bot.harness.tools import ToolRegistry
from bot.harness.types import AgentTurn

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 4
BUDGET_NOTICE = "（工具调用次数已经用满，请直接基于手上已有的信息回答，不要再调用工具。）"


class Agent:
    """一次对话回合的执行器；工具是否可用由 provider 能力决定。"""

    def __init__(
        self,
        provider: Any,
        registry: ToolRegistry,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        on_tool: Callable[[str, Any], Awaitable[None]] | None = None,
        marker_parser: Callable[[str], str | None] | None = None,
        marker_tool: str = "web_search",
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.max_steps = max(1, max_steps)
        self.on_tool = on_tool
        # 有些模型（或提示词影响下）会用文本标记表达"我要联网"；这里把它兜成一次真正的工具调用。
        self.marker_parser = marker_parser
        self.marker_tool = marker_tool

    async def run(self, *, system: str, user_content: Any, model: str | None = None) -> AgentTurn:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": user_content})
        artifacts: list[Any] = []
        calls_made: list[str] = []
        notes: list[str] = []
        steps = 0
        tools_on = True

        while True:
            use_tools = tools_on and steps < self.max_steps
            if tools_on and not use_tools:
                tools_on = False
                messages.append({"role": "user", "content": BUDGET_NOTICE})
            result = await self.provider.chat(
                messages,
                model=model,
                tools=self.registry.schemas() if use_tools else None,
            )
            if getattr(result, "tools_unsupported", False):
                logger.info("Provider rejected tools; falling back to plain chat")
                return AgentTurn(
                    text=result.content,
                    steps=steps,
                    calls=calls_made,
                    artifacts=artifacts,
                    notes=notes,
                    tools_disabled=True,
                )
            calls = list(getattr(result, "tool_calls", []) or []) if use_tools else []
            if not calls:
                marker_query = None
                if use_tools and self.marker_parser is not None and self.marker_tool:
                    marker_query = self.marker_parser(result.content)
                if marker_query and self.registry.get(self.marker_tool) is not None:
                    steps += 1
                    messages.append({"role": "assistant", "content": result.content})
                    outcome = await self.registry.call(self.marker_tool, {"query": marker_query})
                    artifacts.extend(outcome.artifacts)
                    calls_made.append(self.marker_tool)
                    if self.on_tool is not None:
                        await self.on_tool(self.marker_tool, outcome)
                    messages.append({
                        "role": "user",
                        "content": (
                            f"（系统已经替你搜过「{marker_query}」）\n\n{outcome.content}\n\n"
                            "请基于这些结果回答，不要再输出 [搜索: …]。"
                        ),
                    })
                    continue
                return AgentTurn(
                    text=result.content,
                    steps=steps,
                    calls=calls_made,
                    artifacts=artifacts,
                    notes=notes,
                )

            steps += 1
            messages.append(result.assistant_message)
            for call in calls:
                calls_made.append(call.name)
                if call.parse_error:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"参数不是合法 JSON（{call.parse_error}），请重新调用并给出合法 JSON。",
                    })
                    continue
                outcome = await self.registry.call(call.name, call.arguments)
                artifacts.extend(outcome.artifacts)
                if self.on_tool is not None:
                    await self.on_tool(call.name, outcome)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": outcome.content,
                })
