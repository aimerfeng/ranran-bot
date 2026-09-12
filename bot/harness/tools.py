"""工具注册表：模型能调用的能力都在这里声明，harness 只认这个接口。"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from bot.harness.types import Artifact, ToolOutcome

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """工具执行失败；错误文案会作为工具结果回给模型，让它自己决定怎么办。"""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[ToolOutcome]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """按名字分发工具调用；任何异常都转成给模型看的文字，不炸掉整轮对话。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in (tools or [])}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self._tools) or "无"
            return ToolOutcome(content=f"没有名为 {name} 的工具。可用工具：{available}")
        try:
            result = tool.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
        except TypeError as exc:
            logger.info("Tool %s bad arguments: %s", name, exc)
            return ToolOutcome(content=f"工具 {name} 的参数不对：{exc}")
        except ToolError as exc:
            return ToolOutcome(content=f"工具 {name} 执行失败：{exc}")
        except Exception as exc:
            logger.exception("Tool %s crashed", name)
            return ToolOutcome(content=f"工具 {name} 出错：{type(exc).__name__}: {exc}")
        if isinstance(result, ToolOutcome):
            return result
        if isinstance(result, str):
            return ToolOutcome(content=result)
        raise ToolError(f"工具有效返回值必须是 ToolOutcome 或 str，得到 {type(result).__name__}")


def json_tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    handler: Callable[..., Awaitable[ToolOutcome]],
    *,
    required: list[str] | None = None,
) -> Tool:
    """声明一个 JSON Schema 工具，省掉到处手写 parameters 的样板。"""
    return Tool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required if required is not None else list(properties),
            "additionalProperties": False,
        },
        handler=handler,
    )