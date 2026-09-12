"""嵌套 skill：Markdown + frontmatter，支持 include 递归展开与按需加载（渐进披露）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)

MAX_DEPTH = 3
DEFAULT_BUDGET = 6000


@dataclass(frozen=True)
class Skill:
    name: str
    key: str
    path: Path
    description: str
    when: tuple[str, ...]
    always: bool
    includes: tuple[str, ...]
    body: str

    def catalog_line(self) -> str:
        trigger = f"（命中这些词时自动生效：{'|'.join(self.when)}）" if self.when else ""
        return f"- {self.name}：{self.description}{trigger}"


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆出 --- 之间的 frontmatter；没有就返回空 meta + 原文。"""
    body = text.lstrip("\ufeff")
    if not body.startswith("---"):
        return {}, body.strip()
    parts = body.split("---", 2)
    if len(parts) < 3:
        return {}, body.strip()
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        logger.warning("Skill frontmatter is not valid YAML; ignoring it")
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2].strip()


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("\n", "|").split("|")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class SkillRegistry:
    """扫描 skills 目录；skill 之间可以用 includes 互相嵌套。"""

    def __init__(
        self,
        root: Path | None,
        *,
        budget: int = DEFAULT_BUDGET,
        max_depth: int = MAX_DEPTH,
    ) -> None:
        self.root = root
        self.budget = max(500, budget)
        self.max_depth = max(1, max_depth)
        self._skills: dict[str, Skill] = {}

    def load(self) -> int:
        self._skills = {}
        if self.root is None or not Path(self.root).is_dir():
            return 0
        for path in sorted(Path(self.root).rglob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = split_frontmatter(raw)
            if not body:
                continue
            key = path.relative_to(self.root).with_suffix("").as_posix()
            name = str(meta.get("name") or key).strip()
            skill = Skill(
                name=name,
                key=key,
                path=path,
                description=str(meta.get("description") or "").strip(),
                when=tuple(_as_str_list(meta.get("when"))),
                always=bool(meta.get("always")),
                includes=tuple(_as_str_list(meta.get("includes"))),
                body=body,
            )
            self._skills.setdefault(key, skill)
            self._skills.setdefault(name, skill)
        logger.info("Loaded %s skill entries from %s", len(self._distinct()), self.root)
        return len(self._distinct())

    def _distinct(self) -> list[Skill]:
        seen: dict[str, Skill] = {}
        for skill in self._skills.values():
            seen.setdefault(skill.key, skill)
        return list(seen.values())

    def all(self) -> list[Skill]:
        return self._distinct()

    def names(self) -> list[str]:
        return [skill.key for skill in self._distinct()]

    def get(self, name: str) -> Skill | None:
        if not name:
            return None
        return self._skills.get(name.strip())

    def expand(self, name: str, seen: set[str] | None = None, depth: int = 0) -> str:
        """展开一个 skill（含嵌套 include）；循环和超深都会被截断。"""
        skill = self.get(name)
        if skill is None:
            return ""
        guard = seen if seen is not None else set()
        if skill.key in guard or depth > self.max_depth:
            return ""
        guard.add(skill.key)
        blocks = [skill.body]
        for child in skill.includes:
            child_body = self.expand(child, guard, depth + 1)
            if child_body:
                blocks.append(f"## 嵌套：{child}\n{child_body}")
        return "\n\n".join(blocks)

    def auto_active(self, text: str) -> list[Skill]:
        """always 的 skill 加命中关键词的 skill。"""
        body = text or ""
        active: list[Skill] = []
        for skill in self._distinct():
            if skill.always or any(word in body for word in skill.when):
                active.append(skill)
        return active

    def active_section(self, text: str) -> str:
        """自动生效的 skill 正文，带上预算上限（超了就截断）。"""
        guard: set[str] = set()
        blocks: list[str] = []
        used = 0
        for skill in self.auto_active(text):
            if used >= self.budget:
                break
            body = self.expand(skill.key, guard)
            if not body:
                continue
            if used + len(body) > self.budget:
                body = body[: max(0, self.budget - used)].rstrip() + "\n（技能过长，已截断）"
            used += len(body)
            blocks.append(f"### 技能 {skill.name}\n{body}")
        return "\n\n".join(blocks)

    def catalog(self, *, limit: int = 2000) -> str:
        """给模型看的 skill 目录，用来判断要不要 use_skill 加载。"""
        lines: list[str] = []
        used = 0
        for skill in self._distinct():
            line = skill.catalog_line()
            if used + len(line) > limit:
                lines.append("- （其余 skill 已省略）")
                break
            used += len(line)
            lines.append(line)
        return "\n".join(lines)


def load_skills(root: Path | None, *, budget: int = DEFAULT_BUDGET) -> SkillRegistry:
    registry = SkillRegistry(root, budget=budget)
    registry.load()
    return registry