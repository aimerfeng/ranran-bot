"""harness 的公共数据结构：工具调用、工具产物、一次 agent 回合的结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """模型发起的一次工具调用（arguments 已解析成 dict，解析失败时为空 dict）。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str = ""


@dataclass(frozen=True)
class Artifact:
    """工具产出的文件，会在回答之后作为 Telegram 文档发出去。"""

    path: Path
    filename: str
    size: int = 0


@dataclass
class ToolOutcome:
    """工具执行结果：content 回灌给模型；artifacts 交给投递层。"""

    content: str
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass
class AgentTurn:
    """一次 agent 循环的最终结果。"""

    text: str
    steps: int = 0
    calls: list[str] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tools_disabled: bool = False
