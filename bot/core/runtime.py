"""然然核心运行时：与平台无关的会话、提示词组装与 agent 回合。

Telegram / MCP / 其它平台的适配器都调用这里，核心层不 import 任何平台 SDK。
"""
from __future__ import annotations

import logging
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.chat_state import ChatMemory, ChatStateStore
from bot.core.text import sanitize_text
from bot.harness.agent import Agent
from bot.harness.kit import build_tools
from bot.harness.skills import SkillRegistry, load_skills
from bot.harness.types import AgentTurn, Artifact
from bot.models import DEFAULT_MODEL_KEY, MODELS, resolve_model_key
from bot.persona import (
    CODEX_SYSTEM,
    DEEPSEEK_PERSONA,
    OUTPUT_GUARD,
    build_roleplay_prompt,
    build_user_prompt,
    compose_roleplay,
    compose_system,
    load_extra_persona,
    strip_roleplay_prefix,
)
from bot.providers.deepseek import DeepSeekProvider, user_content
from bot.settings import Settings, load_settings
from bot.websearch import (
    MAX_SEARCH_ROUNDS,
    SEARCH_ANSWER_SYSTEM,
    SearchReport,
    WebSearchError,
    dedupe_hits,
    deepseek_web_search,
    merge_report_hits,
    parse_search_marker,
    search_context_text,
)

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 4


def session_key(session_id: str) -> int:
    """把任意 session_id 映射成稳定的正整数，供按聊天隔离的记忆/状态复用。"""
    return zlib.crc32((session_id or "default").encode("utf-8")) & 0x7FFFFFFF


@dataclass
class Reply:
    """一次完整回合的产出。"""

    text: str
    sources: list[Any] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    steps: int = 0
    tools_disabled: bool = False


@dataclass
class SessionInfo:
    """外部平台看到的会话状态。"""

    session_id: str
    model_key: str
    nsfw: bool
    memory_key: int


class RanranRuntime:
    """把「角色设定 + skill + 工具 + 模型」组装成一次可复用的对话回合。"""

    def __init__(
        self,
        settings: Settings,
        *,
        skills: SkillRegistry | None = None,
        persona_extra: str = "",
        deepseek: Any = None,
        codex: Any = None,
        journal: Any = None,
        guard_enabled: bool | None = None,
        session_state_path: Path | None = None,
        session_memory_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.skills = skills if skills is not None else load_skills(settings.skills_dir)
        self.persona_extra = persona_extra
        self.deepseek = deepseek if deepseek is not None else DeepSeekProvider(
            api_key=settings.deepseek_api_key, model=settings.deepseek_model
        )
        self.codex = codex
        self.journal = journal
        self.guard_enabled = (
            bool(getattr(settings, "persona_output_guard", True))
            if guard_enabled is None
            else bool(guard_enabled)
        )
        default_model = resolve_model_key(settings.deepseek_model, DEFAULT_MODEL_KEY)
        self._state = ChatStateStore(
            session_state_path or (settings.data_dir / "runtime_sessions.json"), default_model
        )
        self._memory = ChatMemory(
            path=session_memory_path or (settings.data_dir / "runtime_memory.json")
        )

    # ---------- 提示词 ----------

    def base_persona(self, spec: Any) -> str:
        return CODEX_SYSTEM if getattr(spec, "provider", "") == "codex" else DEEPSEEK_PERSONA

    def output_guard(self) -> str:
        return OUTPUT_GUARD if self.guard_enabled else ""

    def compose_prompt(
        self,
        spec: Any,
        *,
        user_text: str = "",
        extra_rules: str = "",
        nsfw: bool = False,
    ) -> str:
        """两套完全独立的系统提示词：

        - 普通模式：内置然然人设 + 任务规则 + skill → 外部人设 → 输出纪律
        - 角色扮演模式（外部人设已加载且该会话开启）：外部人设独立成栈，
          不再叠加内置人设、skill 目录与输出纪律——后者与成人向创作人设直接冲突
          （例如输出纪律禁止内心独白，而人设要求每段都有）。
        """
        if nsfw and self.persona_extra:
            return compose_roleplay(self.persona_extra, extra_rules=extra_rules)
        base = self.base_persona(spec)
        if extra_rules:
            base = base + "\n\n" + extra_rules
        if isinstance(self.skills, SkillRegistry):
            catalog = self.skills.catalog()
            if catalog:
                base += (
                    "\n\n可用 skill（要按某个 skill 的方式处理时，先用 use_skill 工具加载它的正文，"
                    "不要凭空猜内容）：\n" + catalog
                )
            active = self.skills.active_section(user_text)
            if active:
                base += "\n\n当前自动生效的 skill：\n" + active
        return compose_system(base, self.output_guard())

    # ---------- 引擎 ----------

    def provider_for(self, spec: Any) -> Any:
        if getattr(spec, "provider", "") == "codex":
            return self.codex
        return self.deepseek

    async def _ask_provider(
        self, spec: Any, prompt: str, *, system: str, images: list[Any] | None = None
    ) -> str:
        provider = self.provider_for(spec)
        if provider is None:
            raise RuntimeError("没有可用的模型提供方")
        if getattr(spec, "provider", "") == "codex":
            return await provider.ask(prompt, system=system, model=spec.api_model)
        return await provider.ask(
            prompt, model=spec.api_model, system=system, images=images or None
        )

    async def _answer_with_search(
        self,
        spec: Any,
        user_prompt: str,
        *,
        system: str,
        extra: str = "",
        guard: str = "",
        images: list[Any] | None = None,
        max_rounds: int = MAX_SEARCH_ROUNDS,
        on_search: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> tuple[str, list[SearchReport]]:
        """没有原生工具时的退路：模型用 [搜索: …] 表达联网意图，系统代它搜。"""
        reports: list[SearchReport] = []
        materials: list[str] = []
        query = None
        attempt = 0
        while True:
            if query:
                attempt += 1
                try:
                    report = await self.search(query)
                except WebSearchError:
                    if not reports:
                        raise
                    logger.warning("Search round %s failed query=%s", attempt, query[:60])
                    materials.append(f"（第 {attempt} 轮搜索「{query}」失败，只能用手上已有的材料回答。）")
                    query = None
                    continue
                reports.append(report)
                materials.append(search_context_text(query, report.hits, report.summary))
                logger.info("Web search round=%s query=%s hits=%s", attempt, query[:60], len(report.hits))
                if on_search is not None:
                    await on_search(query, attempt)
                query = None

            round_prompt = (
                user_prompt if not materials else user_prompt + "\n\n" + "\n\n".join(materials)
            )
            ask_system = system
            if materials:
                ask_system += "\n\n" + SEARCH_ANSWER_SYSTEM
            if attempt >= max_rounds:
                ask_system += (
                    f"\n\n（联网搜索已经用满 {max_rounds} 轮，直接基于现有材料回答，"
                    "不要再输出 [搜索: …]；材料确实不够就如实说明。）"
                )
            raw = await self._ask_provider(
                spec,
                round_prompt,
                system=compose_system(compose_system(ask_system, extra), guard),
                images=images if not materials else None,
            )
            marker = parse_search_marker(raw) if attempt < max_rounds else None
            if not marker:
                return raw, reports
            query = marker

    async def answer(
        self,
        spec: Any,
        user_prompt: str,
        *,
        system: str,
        extra: str = "",
        guard: str = "",
        nsfw: bool = False,
        chat_key: int | None = None,
        images: list[Any] | None = None,
        automatic: bool = False,
        initial_query: str | None = None,
        on_search: Callable[[str, int], Awaitable[None]] | None = None,
        on_tool: Callable[[str, Any], Awaitable[None]] | None = None,
    ) -> tuple[str, list[Any], list[Artifact]]:
        """优先走 harness 的原生工具循环；模型不支持工具时退回 [搜索: …] 文本协议。

        返回 (正文, 来源列表, 文件产物)。
        """
        if nsfw and self.persona_extra:
            # 角色扮演模式：外部人设独立成栈，调用方传进来的内置人设与输出纪律一律丢弃，
            # 否则输出纪律会重新落到末尾，把成人向人设的写作要求再盖掉一次。
            system = self.compose_prompt(spec, user_text=user_prompt, nsfw=True)
            extra = ""
            guard = ""

        provider = self.provider_for(spec)
        supports_tools = provider is not None and hasattr(provider, "chat")

        if automatic or not supports_tools:
            raw, reports = await self._answer_with_search(
                spec,
                user_prompt,
                system=system,
                extra=extra,
                guard=guard,
                images=images,
                max_rounds=0 if automatic else MAX_SEARCH_ROUNDS,
                on_search=on_search,
            )
            return raw, merge_report_hits(reports), []

        kit = build_tools(
            api_key=self.settings.deepseek_api_key,
            model=self.settings.deepseek_model,
            outbox_dir=self.settings.data_dir / "outbox",
            skills=self.skills,
            journal=self.journal,
            chat_id=chat_key,
        )
        contents: Any = user_content(user_prompt, images)
        if initial_query:
            report = await self.search(initial_query)
            kit.searches += 1
            kit.search_hits.extend(report.hits)
            prefix = (
                "用户明确要求联网，系统已经替他搜过一轮，结果如下（信息不够可以再调用 web_search）：\n\n"
                + search_context_text(initial_query, report.hits, report.summary)
                + "\n\n"
            )
            contents = (
                prefix + contents
                if isinstance(contents, str)
                else [{"type": "text", "text": prefix}, *contents]
            )

        agent = Agent(
            provider,
            kit.registry,
            max_steps=MAX_AGENT_STEPS,
            on_tool=on_tool,
            marker_parser=parse_search_marker,
        )
        turn: AgentTurn = await agent.run(
            system=system, user_content=contents, model=spec.api_model
        )
        if turn.tools_disabled:
            raw, reports = await self._answer_with_search(
                spec,
                user_prompt,
                system=system,
                extra=extra,
                guard=guard,
                images=images,
                max_rounds=MAX_SEARCH_ROUNDS,
                on_search=on_search,
            )
            return raw, merge_report_hits(reports), []
        if turn.calls:
            logger.info(
                "Agent steps=%s tools=%s chat=%s", turn.steps, ",".join(turn.calls), chat_key
            )
        return turn.text, kit.search_hits, turn.artifacts

    async def search(self, query: str, *, model: str | None = None) -> SearchReport:
        return await deepseek_web_search(
            self.settings.deepseek_api_key,
            query,
            model=model or self.settings.deepseek_model,
        )

    # ---------- 会话（给没有自己的状态层的平台用） ----------

    def session(self, session_id: str) -> SessionInfo:
        key = session_key(session_id)
        return SessionInfo(
            session_id=session_id or "default",
            model_key=self._state.get_model(key),
            nsfw=self._state.nsfw(key),
            memory_key=key,
        )

    def set_session_model(self, session_id: str, model_key: str) -> str:
        return self._state.set_model(session_key(session_id), model_key)

    def set_session_nsfw(self, session_id: str, enabled: bool) -> None:
        self._state.set_nsfw(session_key(session_id), enabled)

    def history(self, session_id: str, *, exclude_message_id: int | None = None) -> str:
        return self._memory.render(session_key(session_id), exclude_message_id=exclude_message_id)

    def remember(self, session_id: str, speaker: str, text: str) -> None:
        self._memory.add(session_key(session_id), speaker, text)

    async def chat(
        self,
        session_id: str,
        text: str,
        *,
        speaker: str = "用户",
        model_key: str | None = None,
        images: list[Any] | None = None,
        trigger: str = "外部平台",
        on_tool: Callable[[str, Any], Awaitable[None]] | None = None,
        on_search: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> Reply:
        """完整回合：组装提示词 → 跑 agent（含工具）→ 返回正文/来源/文件。"""
        session = self.session(session_id)
        spec = MODELS.get(model_key or session.model_key) or MODELS[DEFAULT_MODEL_KEY]
        if session.nsfw and self.persona_extra:
            user_prompt = build_roleplay_prompt(
                speaker=speaker, text=text, history=self.history(session_id), trigger=trigger
            )
        else:
            user_prompt = build_user_prompt(
                speaker=speaker,
                text=text,
                history=self.history(session_id),
                trigger=trigger,
                reply_context="",
                chat_type="private",
            )
        system = self.compose_prompt(spec, user_text=text, nsfw=session.nsfw)
        raw, hits, artifacts = await self.answer(
            spec,
            user_prompt,
            system=system,
            extra="",
            guard=self.output_guard(),
            nsfw=session.nsfw,
            chat_key=session.memory_key,
            images=images,
            on_tool=on_tool,
            on_search=on_search,
        )
        text_out = strip_roleplay_prefix(
            sanitize_text(raw, tuple(self.settings.secrets or ()))
        ).strip()
        self.remember(session_id, speaker, text)
        if text_out:
            self.remember(session_id, "然然", text_out)
        return Reply(
            text=text_out,
            sources=dedupe_hits(hits),
            artifacts=list(artifacts),
        )


def runtime_from_env(**kwargs: Any) -> RanranRuntime:
    """按 .env 配置构建运行时（MCP server 与脚本入口用）。"""
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    kwargs.setdefault("persona_extra", load_extra_persona(settings.persona_extra_path))
    return RanranRuntime(settings, **kwargs)
