from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.sticker_commands import sticker_cmd, quote_cmd, choose_cmd
from bot.chat_state import AutoReplyGate, ChatMemory, ChatStateStore
from bot.daily_journal import BEIJING, DailyJournal, DailySnapshot
from bot.daily_analysis import DailyAnalyzer
from bot.files import send_artifact
from bot.harness.agent import Agent
from bot.harness.kit import build_tools
from bot.harness.skills import SkillRegistry, load_skills
from bot.harness.types import AgentTurn
from bot.fun import react_to, send_anime_image, welcome_line
from bot.fortune import Fortune, THEME_ALIASES, THEME_HELP, send_fortune
from bot.fortune_reading import FORTUNE_READING_SYSTEM, build_reading_prompt, fallback_reading
from bot.images import collect_images
from bot.models import MODELS, default_key_from_settings, model_for_images, model_list_text, parse_model_key
from bot.persona import (
    CODEX_SYSTEM,
    DEEPSEEK_PERSONA,
    OUTPUT_GUARD,
    build_user_prompt,
    compose_system,
    load_extra_persona,
    strip_roleplay_prefix,
)
from bot.providers import CodexError, CodexProvider, DeepSeekError, DeepSeekProvider
from bot.providers.deepseek import ChatResult, ToolsUnsupported, user_content
from bot.settings import (
    CHAT_STATE_PATH,
    GROUP_PACKS_PATH,
    STICKERS_PATH,
    WHITELIST_PATH,
    Settings,
    load_settings,
    secret_fingerprint,
)
from bot.stickers import (
    MOODS,
    StickerBank,
    normalize_mood,
    split_sticker_tag,
)
from bot.util import (
    chat_lock,
    command_args_text,
    display_name,
    is_group,
    is_private,
    mentioned_bot,
    sanitize_text,
    should_auto_reply,
    split_text,
    strip_bot_mention,
)
from bot.websearch import (
    MAX_SEARCH_ROUNDS,
    SEARCH_ANSWER_SYSTEM,
    WebSearchError,
    dedupe_hits,
    deepseek_web_search,
    format_search_sources,
    merge_report_hits,
    parse_search_marker,
    parse_search_request,
    search_context_text,
    strip_search_marker,
)
from bot.whitelist import Whitelist, WhitelistError, parse_chat_id

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HELP_PUBLIC = (
    "群里这样用：\n"
    "• 直接 @然然 说话\n"
    "• 回复我的消息继续聊\n"
    "• 发图片、回复图片，或私聊丢图给我，我能看图\n"
    "• 我会偶尔主动接群里的话\n\n"
    "输入 / 就能点这些命令：\n"
    "/yun — 二次元抽签＋AI解签（/yun 主题 查看12套主题）\n"
    "/waifu — 随机二次元图\n"
    "/setu — 随机涩图\n"
    "/sticker [文字] — 回复图片做贴纸，可加字\n"
    "/quote — 回复原话生成金句卡片\n"
    "/choose 选项1 选项2 — 帮你选一个\n"
    "/search [问题] — 联网搜索最新信息并总结\n"
    "/stickers — 表情包库存，顺便丢一张\n"
    "/model — 查看或切换模型\n"
    "/auto on 或 /auto off — 开关主动接话\n"
    "/codex /deepseek — 切模型，带问题就直接问\n"
    "/chatid — 查看当前 chat_id（未入白名单的群也能用）\n\n"
    "只有白名单中的群可以使用（/chatid 除外）。私聊仅管理员可用。"
)

PUBLIC_COMMANDS = [
    BotCommand("yun", "二次元抽签 · AI解签"),
    BotCommand("waifu", "随机二次元图"),
    BotCommand("setu", "随机涩图"),
    BotCommand("sticker", "回复图片做贴纸 · 可加字"),
    BotCommand("quote", "回复原话做金句卡片"),
    BotCommand("choose", "给几个选项 · 帮你选一个"),
    BotCommand("search", "联网搜索 · AI总结"),
    BotCommand("pack", "查看群贴纸包"),
    BotCommand("pack_list", "列出群贴纸"),
    BotCommand("pack_delete", "删除群贴纸"),
    BotCommand("pack_rename", "重命名群贴纸包"),
    BotCommand("stickers", "表情包"),
    BotCommand("model", "查看或切换模型"),
    BotCommand("auto", "开关主动接话"),
    BotCommand("codex", "切到 Codex / 提问"),
    BotCommand("deepseek", "切到 DeepSeek / 提问"),
    BotCommand("help", "使用说明"),
    BotCommand("chatid", "查看当前 chat_id"),
    BotCommand("start", "开始"),
]

OWNER_COMMANDS = [
    *PUBLIC_COMMANDS,
    BotCommand("stickerset", "导入贴纸包"),
    BotCommand("learn", "给贴纸打情绪标签"),
    BotCommand("persona", "查看/重载外部人设"),
    BotCommand("nsfw", "开关成人向人设"),
    BotCommand("skill", "查看/重载 skill"),
    BotCommand("whitelist", "列出白名单"),
    BotCommand("whitelist_add", "加入白名单群"),
    BotCommand("whitelist_remove", "移出白名单群"),
]

HELP_OWNER = (
    "\n\n管理员命令（仅私聊）：\n"
    "/whitelist — 列出白名单\n"
    "/whitelist_add <chat_id> — 加入群\n"
    "/whitelist_remove <chat_id> — 移除群\n"
    "/stickerset <名字> — 导入一套公开贴纸\n"
    "/learn <情绪> — 回复一张贴纸，给它打标签\n"
    "/persona — 查看外部人设状态；/persona reload 重新读取文件\n"
    "/nsfw on 或 /nsfw off — 按聊天开关成人向人设（默认关）\n"
    "/skill — 查看 skill 列表；/skill reload 重载；/skill show <名字> 看正文\n"
    "私聊直接丢贴纸给我，我会收进表情包。"
)


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.bot_data["settings"]


def _whitelist(context: ContextTypes.DEFAULT_TYPE) -> Whitelist:
    return context.bot_data["whitelist"]


def _state(context: ContextTypes.DEFAULT_TYPE) -> ChatStateStore:
    return context.bot_data["chat_state"]


def _memory(context: ContextTypes.DEFAULT_TYPE) -> ChatMemory:
    return context.bot_data["memory"]


def _stickers(context: ContextTypes.DEFAULT_TYPE) -> StickerBank:
    return context.bot_data["stickers"]


def _is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    return bool(user and user.id == _settings(context).owner_id)


def _can_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False
    if is_private(chat):
        return user.id == _settings(context).owner_id
    if is_group(chat):
        return _whitelist(context).contains(chat.id)
    return False


async def _deny_if_unauthorized(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if _can_use(update, context):
        return False
    chat = update.effective_chat
    if is_private(chat) and update.effective_message:
        await update.effective_message.reply_text("未授权。")
    return True


def _store_model(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, model: str
) -> None:
    context.bot_data["reply_models"][(chat_id, message_id)] = model


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    text = HELP_PUBLIC
    if is_private(update.effective_chat):
        text += HELP_OWNER
    await update.effective_message.reply_text(text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or update.effective_message is None:
        return
    if is_private(chat):
        hint = "这是私聊 ID。白名单只接受群 chat_id（负数）。"
    elif is_group(chat):
        listed = _whitelist(context).contains(chat.id)
        hint = (
            "这是群 ID，可用管理员私聊命令加入白名单。"
            if not listed
            else "这是群 ID，已在白名单中。"
        )
    else:
        hint = "当前不是群聊，白名单只接受群 chat_id。"
    await update.effective_message.reply_text(
        f"聊天类型：{chat.type}\nchat_id：{chat.id}\n{hint}"
    )


async def whitelist_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update.effective_chat):
        return
    if await _deny_if_unauthorized(update, context):
        return
    ids = _whitelist(context).list_ids()
    if not ids:
        await update.effective_message.reply_text("白名单为空。先在目标群发送 /chatid，再把 ID 加进来。")
        return
    lines = "\n".join(str(i) for i in ids)
    await update.effective_message.reply_text(f"当前白名单群：\n{lines}")


async def whitelist_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update.effective_chat):
        return
    if await _deny_if_unauthorized(update, context):
        return
    raw = command_args_text(update, context.args)
    if not raw:
        await update.effective_message.reply_text("用法：/whitelist_add <chat_id>")
        return
    try:
        chat_id = parse_chat_id(raw.split()[0])
        added = _whitelist(context).add(chat_id)
    except WhitelistError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    if added:
        await update.effective_message.reply_text(f"已加入白名单：{chat_id}")
    else:
        await update.effective_message.reply_text(f"已在白名单中：{chat_id}")


async def whitelist_remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update.effective_chat):
        return
    if await _deny_if_unauthorized(update, context):
        return
    raw = command_args_text(update, context.args)
    if not raw:
        await update.effective_message.reply_text("用法：/whitelist_remove <chat_id>")
        return
    try:
        chat_id = parse_chat_id(raw.split()[0])
        removed = _whitelist(context).remove(chat_id)
    except WhitelistError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    if removed:
        await update.effective_message.reply_text(f"已移出白名单：{chat_id}")
    else:
        await update.effective_message.reply_text(f"白名单中没有：{chat_id}")


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    chat = update.effective_chat
    if chat is None or update.effective_message is None:
        return
    raw = command_args_text(update, context.args)
    current = _state(context).get_model(chat.id)
    if not raw:
        await update.effective_message.reply_text(model_list_text(current))
        return
    key = parse_model_key(raw.split()[0])
    if not key:
        await update.effective_message.reply_text(
            "不认识这个模型。\n" + model_list_text(current)
        )
        return
    key = _state(context).set_model(chat.id, key)
    spec = MODELS[key]
    await update.effective_message.reply_text(
        f"好，这之后用 {spec.title}（{spec.key}）。直接 @我 就行。"
    )


async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or update.effective_message is None or user is None:
        return
    if user.id != _settings(context).owner_id:
        return
    raw = (command_args_text(update, context.args) or "").strip().lower()
    if raw in {"on", "开启", "开", "1", "true"}:
        _state(context).set_auto_reply(chat.id, True)
        await update.effective_message.reply_text("好，我会主动接群里的话。")
        return
    if raw in {"off", "关闭", "关", "0", "false"}:
        _state(context).set_auto_reply(chat.id, False)
        await update.effective_message.reply_text("行，那我只在被 @ 或被回复时才说话。")
        return
    enabled = _state(context).auto_reply(chat.id)
    await update.effective_message.reply_text(
        f"主动接话现在是：{'开' if enabled else '关'}。用法：/auto on 或 /auto off"
    )


async def codex_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    chat = update.effective_chat
    if chat is None:
        return
    _state(context).set_model(chat.id, "codex")
    prompt = command_args_text(update, context.args)
    if not prompt:
        await update.effective_message.reply_text("已切到 Codex。之后直接 @我就行。")
        return
    await _run_query(update, context, "codex", prompt, trigger="命令 /codex")


async def deepseek_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    chat = update.effective_chat
    if chat is None:
        return
    _state(context).set_model(chat.id, "flash")
    prompt = command_args_text(update, context.args)
    if not prompt:
        await update.effective_message.reply_text("已切到 DeepSeek V4.1 Flash。之后直接 @我就行。")
        return
    await _run_query(update, context, "flash", prompt, trigger="命令 /deepseek")


async def nsfw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """按聊天开关成人向外部人设；默认关，需要时手动打开。"""
    if await _deny_if_unauthorized(update, context):
        return
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or user is None or message is None:
        return
    if user.id != _settings(context).owner_id:
        await message.reply_text("这个开关只有 owner 能动。")
        return
    raw = (command_args_text(update, context.args) or "").strip().lower()
    if raw in {"on", "开", "开启", "打开", "1", "true"}:
        if not (context.bot_data.get("persona_extra") or ""):
            await message.reply_text(
                "外部人设文件里没读到内容，先把文件放好再开。\n"
                f"路径：{_settings(context).persona_extra_path}\n用 /persona reload 重新读取。"
            )
            return
        _state(context).set_nsfw(chat.id, True)
        await message.reply_text("好，这个聊天的外部人设开着了。不想用了发 /nsfw off。")
        return
    if raw in {"off", "关", "关闭", "0", "false"}:
        _state(context).set_nsfw(chat.id, False)
        await message.reply_text("行，切回内置人设。")
        return
    enabled = _nsfw_on(context, chat.id)
    await message.reply_text(
        f"外部人设现在是：{'开' if enabled else '关'}（只影响当前这个聊天）。\n"
        "用法：/nsfw on  或  /nsfw off"
    )


async def skill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看/重载/预览 skill（仅 owner 私聊）。"""
    if not is_private(update.effective_chat):
        return
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    settings = _settings(context)
    skills = context.bot_data.get("skills")
    raw = (command_args_text(update, context.args) or "").strip()
    action, _, argument = raw.partition(" ")
    action = action.strip().lower()
    argument = argument.strip()

    if action in {"reload", "重载", "重新加载"}:
        registry = load_skills(settings.skills_dir)
        context.bot_data["skills"] = registry
        await message.reply_text(f"已重载 {len(registry.all())} 个 skill（目录：{settings.skills_dir}）。")
        return
    if action in {"show", "看", "查看"} and argument:
        body = skills.expand(argument) if isinstance(skills, SkillRegistry) else ""
        if not body:
            await message.reply_text("没有这个 skill。发 /skill 看列表。")
            return
        for chunk in split_text(f"skill {argument}：\n\n{body}"):
            await message.reply_text(chunk)
        return
    if not isinstance(skills, SkillRegistry) or not skills.all():
        await message.reply_text(f"没有找到 skill。目录：{settings.skills_dir}")
        return
    lines = [f"共 {len(skills.all())} 个 skill（{settings.skills_dir}）："]
    for skill in skills.all():
        marks = " · 常驻" if skill.always else ""
        if skill.when:
            marks += f" · 自动：{'|'.join(skill.when)}"
        lines.append(f"• {skill.key} — {skill.description}{marks}")
    lines.append("")
    lines.append("看正文：/skill show conversation/human-voice；改完文件：/skill reload")
    await message.reply_text("\n".join(lines))


async def persona_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看/重载追加在系统提示词最后的外部人设（仅 owner 私聊）。"""
    if not is_private(update.effective_chat):
        return
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    path = _settings(context).persona_extra_path
    raw = (command_args_text(update, context.args) or "").strip().lower()
    if raw in {"reload", "重载", "重新加载"}:
        text = load_extra_persona(path)
        context.bot_data["persona_extra"] = text
        if text:
            await message.reply_text(f"外部人设已重载：{len(text)} 字，追加在系统提示词最后一部分。")
        else:
            await message.reply_text(f"没读到内容，外部人设已清空。\n路径：{path}")
        return
    stored = context.bot_data.get("persona_extra") or ""
    status = f"文件已读到 {len(stored)} 字" if stored else "文件不存在或为空"
    guard = "开" if _output_guard(context) else "关"
    chat = update.effective_chat
    switch = "开" if _nsfw_on(context, chat.id if chat else None) else "关"
    await message.reply_text(
        f"外部人设文件：{status}\n路径：{path}\n当前聊天开关：{switch}（用 /nsfw on|off 改）\n"
        f"输出纪律：{guard}\n重载：/persona reload"
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    query = command_args_text(update, context.args).strip()
    if not query:
        await message.reply_text(
            "用法：/search 想问的问题\n我会联网搜索最新信息，再用 AI 总结并附上来源链接。"
        )
        return
    await _run_search_query(update, context, query)


async def _run_search_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query: str
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    settings = _settings(context)
    speaker = display_name(user)
    lock = chat_lock(context.bot_data["locks"], chat.id)
    model_key = _state(context).get_model(chat.id)
    spec = MODELS.get(model_key) or MODELS["flash"]
    if spec.provider != "deepseek" or spec.vision:
        spec = MODELS["flash"]
    async with lock:
        thinking = await message.reply_text("然然正在联网搜索……")
        _memory(context).add(chat.id, speaker, f"/search {query}", message_id=message.message_id)

        async def on_search(found_for: str, attempt: int) -> None:
            if attempt == 1:
                return
            try:
                await thinking.edit_text(f"然然还在补充搜索（第 {attempt} 轮）……")
            except TelegramError:
                logger.debug("Could not update thinking message before search")

        async def on_tool(name: str, outcome: Any) -> None:
            note = {
                "web_search": "然然正在联网搜索……",
                "write_file": "然然在整理文件……",
                "use_skill": "然然翻了下自己的笔记……",
            }.get(name)
            if not note:
                return
            try:
                await thinking.edit_text(note)
            except TelegramError:
                logger.debug("Could not update thinking message before tool %s", name)

        user_prompt = (
            "用户在聊天里要求联网搜索，请基于下面的搜索结果回答；不够时可以调用 web_search 再搜。"
            "\n" + json.dumps({"问题": query}, ensure_ascii=False)
        )
        try:
            raw, hits, artifacts = await _answer_with_tools(
                context,
                spec,
                user_prompt,
                system=_compose_system(context, spec, user_text=query, chat_id=chat.id),
                extra=_persona_extra(context, chat.id),
                guard=_output_guard(context),
                chat_id=chat.id,
                initial_query=query,
                on_search=on_search,
                on_tool=on_tool,
            )
            answer = strip_roleplay_prefix(
                sanitize_text(strip_search_marker(raw), settings.secrets)
            ).strip()
            if not answer:
                answer = "这次总结没写出内容，直接看下面的来源吧。"
        except WebSearchError as exc:
            await _edit_or_reply(thinking, message, sanitize_text(str(exc), settings.secrets))
            return
        except DeepSeekError as exc:
            hits = []
            artifacts = []
            answer = (
                f"总结这一步失败了（{sanitize_text(str(exc), settings.secrets)}），"
                "先给你原始搜索结果："
            )

        chunks = split_text(answer)
        first = await _edit_or_reply(thinking, message, chunks[0])
        if first is not None:
            _store_model(context, chat.id, first.message_id, spec.key)
            _memory(context).add(chat.id, "然然", chunks[0], message_id=first.message_id)
        for chunk in chunks[1:]:
            sent = await message.reply_text(chunk)
            _memory(context).add(chat.id, "然然", chunk, message_id=sent.message_id)
        sources = dedupe_hits(hits)
        if sources:
            for chunk in split_text(format_search_sources(sources)):
                await message.reply_text(chunk)
        for artifact in artifacts:
            await send_artifact(message, artifact)


async def yun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    user = update.effective_user
    if update.effective_message is None or user is None:
        return
    arg = ' '.join(getattr(context, 'args', None) or []).strip().lower()
    theme = THEME_ALIASES.get(arg)
    if theme is None:
        await update.effective_message.reply_text(THEME_HELP, parse_mode=None)
        return
    fortune = await send_fortune(update.effective_message, user.id, display_name(user), theme=theme)
    await _interpret_fortune(update, context, fortune)


async def _interpret_fortune(update: Update, context: ContextTypes.DEFAULT_TYPE, fortune: Fortune) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    journal = context.bot_data.get("daily_journal")
    snapshot = None
    if journal is not None and update.effective_user is not None:
        snapshot = await asyncio.to_thread(journal.snapshot, chat.id, update.effective_user.id, fortune.day)
    await _run_query(
        update, context, _state(context).get_model(chat.id), build_reading_prompt(fortune),
        trigger="每日解签", failure_answer=fallback_reading(fortune), daily_snapshot=snapshot,
    )


async def waifu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_anime_image(update, context, nsfw=False)


async def setu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_anime_image(update, context, nsfw=True)


async def _send_anime_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, nsfw: bool
) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    try:
        await send_anime_image(message, nsfw=nsfw)
    except Exception:
        logger.exception("Anime image fetch failed")
        await message.reply_text("图源抽风了，等下再来。")


async def _is_group_admin(update, context) -> bool:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or not is_group(chat): return False
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ("administrator", "creator")

async def pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context): return
    chat=update.effective_chat; row=context.bot_data["group_packs"].get(chat.id)
    if not row:
        await update.effective_message.reply_text("这个群还没有群贴纸包。回复图片发送 /stickers 创建第一张。")
        return
    await update.effective_message.reply_text(f"群贴纸包「{row['title']}」：\nhttps://t.me/addstickers/{row['name']}\n已记录 {len(row.get('ids', []))} 张。")

async def pack_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context): return
    chat=update.effective_chat; row=context.bot_data["group_packs"].get(chat.id)
    if not row:
        await update.effective_message.reply_text("这个群还没有群贴纸包。")
        return
    try: pack=await context.bot.get_sticker_set(row['name'])
    except TelegramError as exc:
        await update.effective_message.reply_text(f"贴纸包读取失败：{str(exc)[:240]}"); return
    lines=[f"群贴纸包「{pack.title}」\nhttps://t.me/addstickers/{row['name']}",f"共 {len(pack.stickers)} 张："]
    for i, st in enumerate(pack.stickers,1): lines.append(f"{i}. {st.emoji or '🙂'}  {st.file_unique_id}")
    await update.effective_message.reply_text('\n'.join(lines))

async def pack_delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context): return
    if not await _is_group_admin(update, context): await update.effective_message.reply_text("只有群管理员可以管理贴纸包。"); return
    source=update.effective_message.reply_to_message; sticker=getattr(source,'sticker',None) if source else None
    if not sticker: await update.effective_message.reply_text("请回复要删除的贴纸，再发送 /pack_delete。"); return
    try: await context.bot.delete_sticker_from_set(sticker.file_id)
    except TelegramError as exc: await update.effective_message.reply_text(f"删除失败：{str(exc)[:240]}"); return
    row=context.bot_data["group_packs"].get(update.effective_chat.id)
    if row and sticker.file_id in row.get('ids',[]): row['ids'].remove(sticker.file_id); context.bot_data["group_packs"]._save()
    await update.effective_message.reply_text("已从群贴纸包删除这张贴纸。")

async def pack_rename_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context): return
    if not await _is_group_admin(update, context): await update.effective_message.reply_text("只有群管理员可以管理贴纸包。"); return
    title=command_args_text(update,context.args); row=context.bot_data["group_packs"].get(update.effective_chat.id)
    if not row or not title: await update.effective_message.reply_text("用法：/pack_rename 新名称"); return
    try: await context.bot.set_sticker_set_title(row['name'],title[:64])
    except TelegramError as exc: await update.effective_message.reply_text(f"重命名失败：{str(exc)[:240]}"); return
    row['title']=title[:64]; context.bot_data["group_packs"]._save(); await update.effective_message.reply_text("贴纸包名称已更新。")

async def stickers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Compatibility: /stickers on a replied image means “make a sticker”.
    # Without media it retains the original inventory behavior.
    message = update.effective_message
    if message is not None and getattr(message, "reply_to_message", None) is not None:
        if await _deny_if_unauthorized(update, context):
            return
        await sticker_cmd(update, context)
        return
    if await _deny_if_unauthorized(update, context):
        return
    if message is None:
        return
    bank = _stickers(context)
    await message.reply_text(bank.summary())
    item = bank.pick()
    if item:
        await message.reply_sticker(item.file_id)


async def stickerset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        return
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    name = command_args_text(update, context.args)
    if not name:
        await message.reply_text("用法：/stickerset Kokomi\n也可以丢 t.me/addstickers/名字")
        return
    try:
        added = await _stickers(context).import_set(context.bot, name.split()[0])
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(f"收下了 {added} 张。{_stickers(context).summary()}")


async def learn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        return
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    replied = message.reply_to_message
    sticker = replied.sticker if replied else None
    raw = command_args_text(update, context.args)
    mood = normalize_mood(raw.split()[0] if raw else "")
    if sticker is None or mood is None:
        await message.reply_text("回复一张贴纸，写 /learn 笑\n情绪：" + " ".join(MOODS))
        return
    _stickers(context).add_sticker(sticker, mood)
    await message.reply_text(f"这张算「{mood}」。")


async def on_daily_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1: journal authentic received messages before command/reply handlers."""
    journal = context.bot_data.get("daily_journal")
    if journal is None or not _can_use(update, context):
        return
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None or user.is_bot:
        return
    if getattr(message, "sender_chat", None) is not None:
        return  # Anonymous/channel posts cannot be attributed to the actual user.
    text = getattr(message, "text", None)
    kind = "text"
    if not text:
        text = getattr(message, "caption", None)
        kind = "caption"
    if not text:
        sticker = getattr(message, "sticker", None)
        if sticker is not None:
            text = f"（表情 {getattr(sticker, 'emoji', '') or ''}；未识别图片内容）"
            kind = "sticker"
        else:
            for media in ("voice", "audio", "video_note", "photo", "video", "document", "animation"):
                if getattr(message, media, None):
                    kind, text = media, f"（发送了 {media}；实际内容未转录或识别）"
                    break
    if not text:
        return
    if getattr(message, "forward_origin", None) is not None:
        kind = "forwarded_" + kind
        text = "【转发内容，不视为本人的经历】" + text
    try:
        await asyncio.to_thread(
            journal.add, chat_id=chat.id, user_id=user.id, message_id=message.message_id,
            name=display_name(user), text=text, kind=kind,
            sent_at=message.date, edited_at=getattr(message, "edit_date", None),
        )
        from datetime import datetime
        await asyncio.to_thread(journal.prune, datetime.now(BEIJING).date())
    except Exception:
        logger.warning("Daily journal write failed; message will not be claimed as recorded")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    if user.is_bot:
        return

    text = message.text.strip()
    speaker = display_name(user)
    _memory(context).add(chat.id, speaker, text, message_id=message.message_id)
    await _maybe_reply(update, context, text)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if user.is_bot:
        return

    caption = (message.caption or "").strip()
    speaker = display_name(user)
    memory_text = caption or "（发了一张图）"
    _memory(context).add(chat.id, speaker, memory_text, message_id=message.message_id)
    prompt = strip_bot_mention(caption, context.bot.username) or "请看这张图。"
    await _maybe_reply(update, context, prompt, require_direct=not caption)


async def _maybe_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    *,
    require_direct: bool = False,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    mentioned = mentioned_bot(message, context.bot.id, context.bot.username)
    replied = message.reply_to_message
    reply_to_bot = bool(
        replied and replied.from_user and replied.from_user.id == context.bot.id
    )

    model_key = _state(context).get_model(chat.id)
    if reply_to_bot:
        stored = context.bot_data["reply_models"].get((chat.id, replied.message_id))
        if stored:
            model_key = stored

    # 直接对着我说"联网搜一下 xxx"时不必绕模型，直接去搜。
    search_query = parse_search_request(strip_bot_mention(prompt, context.bot.username))

    if is_private(chat):
        await _reply_or_search(
            update, context, model_key, prompt, trigger="私聊", search_query=search_query
        )
        return

    if mentioned:
        await _reply_or_search(
            update, context, model_key, prompt, trigger="@提到你", search_query=search_query
        )
        return

    if reply_to_bot:
        await _reply_or_search(
            update, context, model_key, prompt, trigger="回复你的消息", search_query=search_query
        )
        return

    if require_direct:
        return
    if not _state(context).auto_reply(chat.id):
        return
    if not should_auto_reply(prompt):
        return
    if not context.bot_data["auto_gate"].allow(chat.id):
        return
    await _run_query(update, context, model_key, prompt, trigger="主动接话")


MAX_AGENT_STEPS = 4


def _nsfw_on(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None) -> bool:
    """该聊天是否开了外部人设；状态不可用时一律当作关闭。"""
    if chat_id is None:
        return False
    try:
        state = _state(context)
    except (KeyError, AttributeError):
        return False
    checker = getattr(state, "nsfw", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(chat_id))
    except Exception:
        logger.debug("NSFW flag lookup failed chat=%s", chat_id)
        return False


def _persona_extra(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None) -> str:
    """外部人设（成人向）只在对应聊天手动开启后才注入。"""
    if not _nsfw_on(context, chat_id):
        return ""
    return context.bot_data.get("persona_extra") or ""


def _output_guard(context: ContextTypes.DEFAULT_TYPE) -> str:
    if not getattr(_settings(context), "persona_output_guard", True):
        return ""
    return OUTPUT_GUARD


def _compose_system(
    context: ContextTypes.DEFAULT_TYPE,
    spec: Any,
    *,
    user_text: str = "",
    extra_rules: str = "",
    chat_id: int | None = None,
) -> str:
    """角色设定 + 任务规则 + skill（目录/自动生效）→ 外部人设 → 输出纪律。"""
    base = _system_prompt(context, spec, extra_rules)
    skills = context.bot_data.get("skills")
    if isinstance(skills, SkillRegistry):
        catalog = skills.catalog()
        if catalog:
            base += (
                "\n\n可用 skill（要按某个 skill 的方式处理时，先用 use_skill 工具加载它的正文，"
                "不要凭空猜内容）：\n" + catalog
            )
        active = skills.active_section(user_text)
        if active:
            base += "\n\n当前自动生效的 skill：\n" + active
    composed = compose_system(base, _persona_extra(context, chat_id))
    return compose_system(composed, _output_guard(context))


def _system_prompt(context: ContextTypes.DEFAULT_TYPE, spec: Any, extra_rules: str = "") -> str:
    """角色设定 + 任务规则；外部人设由 _answer_with_search 追加在最后一部分。"""
    base = CODEX_SYSTEM if spec.provider == "codex" else DEEPSEEK_PERSONA
    if extra_rules:
        base = base + "\n\n" + extra_rules
    return base


async def _answer_with_search(
    context: ContextTypes.DEFAULT_TYPE,
    spec: Any,
    user_prompt: str,
    *,
    system: str,
    extra: str = "",
    search_rules: str = SEARCH_ANSWER_SYSTEM,
    initial_query: str | None = None,
    images: list[Any] | None = None,
    max_rounds: int = MAX_SEARCH_ROUNDS,
    on_search: Any = None,
    guard: str = "",
) -> tuple[str, list[Any]]:
    """回答问题；模型觉得信息不够时用 [搜索: …] 自己发起联网，搜完再答。

    每轮搜索的结果都会累积回灌给模型，因此它能连续追问（最多 max_rounds 轮）。
    中途某轮搜索失败不会丢掉已有材料，只把失败原因告诉模型让它将就着答。
    """
    settings = _settings(context)
    reports: list[Any] = []
    materials: list[str] = []
    query = initial_query
    attempt = 0
    while True:
        if query:
            attempt += 1
            try:
                report = await deepseek_web_search(
                    settings.deepseek_api_key, query, model=settings.deepseek_model
                )
            except WebSearchError:
                if not reports:
                    raise
                logger.warning("Search round %s failed chat query=%s", attempt, query[:60])
                materials.append(f"（第 {attempt} 轮搜索「{query}」失败，只能用手上已有的材料回答。）")
                query = None
                continue
            reports.append(report)
            materials.append(search_context_text(query, report.hits, report.summary))
            logger.info("Web search round=%s query=%s hits=%s", attempt, query[:60], len(report.hits))
            if on_search is not None:
                await on_search(query, attempt)
            query = None

        prompt = user_prompt if not materials else user_prompt + "\n\n" + "\n\n".join(materials)
        ask_system = system
        if materials and search_rules:
            ask_system += "\n\n" + search_rules
        if attempt >= max_rounds:
            ask_system += (
                f"\n\n（联网搜索已经用满 {max_rounds} 轮，直接基于现有材料回答，"
                "不要再输出 [搜索: …]；材料确实不够就如实说明。）"
            )
        raw = await _ask_provider(
            context,
            spec,
            prompt,
            system=compose_system(compose_system(ask_system, extra), guard),
            images=images if not materials else None,
        )
        marker = parse_search_marker(raw) if attempt < max_rounds else None
        if not marker:
            return raw, reports
        query = marker


async def _answer_with_tools(
    context: ContextTypes.DEFAULT_TYPE,
    spec: Any,
    user_prompt: str,
    *,
    system: str,
    extra: str,
    guard: str,
    chat_id: int | None = None,
    images: list[Any] | None = None,
    automatic: bool = False,
    initial_query: str | None = None,
    on_search: Any = None,
    on_tool: Any = None,
) -> tuple[str, list[Any], list[Any]]:
    """优先走 harness 的原生工具循环；模型不支持工具时退回 [搜索: …] 文本协议。

    返回 (正文, 来源列表, 文件产物)。
    """
    settings = _settings(context)
    provider_key = "codex" if spec.provider == "codex" else "deepseek"
    provider = context.bot_data.get(provider_key)
    supports_tools = provider is not None and hasattr(provider, "chat")

    if automatic or not supports_tools:
        raw, reports = await _answer_with_search(
            context,
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
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        outbox_dir=settings.data_dir / "outbox",
        skills=context.bot_data.get("skills"),
        journal=context.bot_data.get("daily_journal"),
        chat_id=chat_id,
    )
    contents: Any = user_content(user_prompt, images)
    if initial_query:
        report = await deepseek_web_search(
            settings.deepseek_api_key, initial_query, model=settings.deepseek_model
        )
        kit.searches += 1
        kit.search_hits.extend(report.hits)
        prefix = (
            "用户明确要求联网，系统已经替他搜过一轮，结果如下（信息不够可以再调用 web_search）：\n\n"
            + search_context_text(initial_query, report.hits, report.summary)
            + "\n\n"
        )
        contents = prefix + contents if isinstance(contents, str) else [
            {"type": "text", "text": prefix},
            *contents,
        ]

    agent = Agent(
        provider,
        kit.registry,
        max_steps=MAX_AGENT_STEPS,
        on_tool=on_tool,
        marker_parser=parse_search_marker,
    )
    turn = await agent.run(system=system, user_content=contents, model=spec.api_model)
    if turn.tools_disabled:
        raw, reports = await _answer_with_search(
            context,
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
        logger.info("Agent steps=%s tools=%s chat=%s", turn.steps, ",".join(turn.calls), chat_id)
    return turn.text, kit.search_hits, turn.artifacts

async def _reply_or_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    model_key: str,
    prompt: str,
    *,
    trigger: str,
    search_query: str | None,
) -> None:
    """用户明确要求联网时直接搜索；否则正常聊天（模型仍可用 [搜索: …] 要联网）。"""
    if search_query:
        await _run_search_query(update, context, search_query)
        return
    await _run_query(update, context, model_key, prompt, trigger=trigger)


async def _ask_provider(
    context: ContextTypes.DEFAULT_TYPE,
    spec: Any,
    prompt: str,
    *,
    system: str,
    images: list[Any] | None = None,
) -> str:
    if spec.provider == "codex":
        return await context.bot_data["codex"].ask(prompt, system=system, model=spec.api_model)
    return await context.bot_data["deepseek"].ask(
        prompt, model=spec.api_model, system=system, images=images or None
    )


async def _run_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    model_key: str,
    prompt: str,
    *,
    trigger: str,
    failure_answer: str | None = None,
    daily_snapshot: DailySnapshot | None = None,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None:
        return

    spec = MODELS.get(model_key) or MODELS["flash"]
    speaker = display_name(user)
    lock = chat_lock(context.bot_data["locks"], chat.id)
    async with lock:
        # Read only after acquiring the lock, so the last assistant answer is included.
        reply = getattr(message, 'reply_to_message', None)
        reply_context = ''
        if reply is not None:
            quoted = getattr(reply, 'text', None) or getattr(reply, 'caption', None) or '（非文字消息）'
            reply_context = f"{display_name(getattr(reply, 'from_user', None))}: {quoted[:2400]}"
        images = []
        if daily_snapshot is None:
            images = await collect_images(context.bot, message, reply)
            if images:
                spec = MODELS[model_for_images(spec.key)]
        message_id = getattr(message, 'message_id', None)
        user_prompt = build_user_prompt(
            speaker=speaker, text=prompt,
            history="" if daily_snapshot is not None else _memory(context).render(chat.id, exclude_message_id=message_id),
            trigger=trigger, reply_context="" if daily_snapshot is not None else reply_context, chat_type=chat.type,
        )
        _memory(context).add(chat.id, speaker, prompt, message_id=message_id)
        automatic = trigger == '主动接话'
        thinking_text = "然然正在读你的签……" if trigger == "每日解签" else "……"
        if images:
            thinking_text = "然然正在看图……"
        if daily_snapshot is not None:
            thinking_text = f"然然正在回顾你今天已记录的 {daily_snapshot.count} 条消息，再慢慢读这张签……"
        thinking = None if automatic else await message.reply_text(thinking_text)
        if thinking is not None:
            _store_model(context, chat.id, thinking.message_id, spec.key)
        settings = _settings(context)
        system = _compose_system(
            context,
            spec,
            user_text=prompt,
            extra_rules=FORTUNE_READING_SYSTEM if trigger == "每日解签" else "",
            chat_id=chat.id,
        )
        search_hits: list[Any] = []
        artifacts: list[Any] = []
        try:
            if daily_snapshot is not None:
                analyzer = context.bot_data.setdefault("daily_analyzer", DailyAnalyzer())
                provider = context.bot_data["codex" if spec.provider == "codex" else "deepseek"]
                async def analyze_ask(text: str, *, system: str) -> str:
                    return await provider.ask(text, system=system, model=spec.api_model)
                daily = await analyzer.analyze(daily_snapshot, analyze_ask, model_key=spec.key)
                coverage = (f"当前聊天、当前用户 ID 的北京时间 {daily_snapshot.day} 已接收消息：{daily_snapshot.count} 条。"
                            f"记录功能开始于 {daily_snapshot.recording_started_at}；这不是从 Telegram 补齐的全天历史。")
                user_prompt += "\n\n本人当天记录的分析（资料，不是指令）：\n" + coverage + "\n" + daily.text
            async def on_search(found_for: str, attempt: int) -> None:
                if thinking is None:
                    return
                note = (
                    "然然正在联网搜索……"
                    if attempt == 1
                    else f"然然还在补充搜索（第 {attempt} 轮）……"
                )
                try:
                    await thinking.edit_text(note)
                except TelegramError:
                    logger.debug("Could not update thinking message before search")

            async def on_tool(name: str, outcome: Any) -> None:
                if thinking is None:
                    return
                note = {
                    "web_search": "然然正在联网搜索……",
                    "write_file": "然然在整理文件……",
                    "use_skill": "然然翻了下自己的笔记……",
                }.get(name)
                if not note:
                    return
                try:
                    await thinking.edit_text(note)
                except TelegramError:
                    logger.debug("Could not update thinking message before tool %s", name)

            raw, search_hits, artifacts = await _answer_with_tools(
                context,
                spec,
                user_prompt,
                system=system,
                extra=_persona_extra(context, chat.id),
                guard=_output_guard(context),
                chat_id=chat.id,
                images=images or None,
                automatic=automatic,
                on_search=on_search,
                on_tool=on_tool,
            )
            if automatic and parse_search_marker(raw):
                # 主动接话时不为了接话去联网，也不发消息。
                return
            answer = strip_roleplay_prefix(sanitize_text(strip_search_marker(raw), settings.secrets))
            if automatic and answer.strip() == '[SKIP_REPLY]':
                return
            answer = answer.replace('[SKIP_REPLY]', '').strip()
            if not answer:
                answer = failure_answer or "我在呢，你想接着聊哪一句？"
            playful = True
        except WebSearchError as exc:
            if automatic:
                return
            answer = "联网搜索没成功：" + sanitize_text(str(exc), settings.secrets)
            playful = False
            search_hits = []
            artifacts = []
        except (CodexError, DeepSeekError) as exc:
            if automatic:
                logger.warning('Automatic reply provider failed chat=%s', chat.id)
                return
            answer = failure_answer or sanitize_text(str(exc), settings.secrets)
            playful = False
        except Exception:
            logger.exception("Query failed model=%s chat=%s", spec.key, chat.id)
            if automatic:
                return
            answer = failure_answer or "处理失败，请稍后重试。"
            playful = False

        await _deliver_reply(
            update,
            context,
            thinking,
            spec.key,
            trigger,
            answer,
            playful=playful,
        )
        sources = dedupe_hits(search_hits)
        if sources:
            for chunk in split_text(format_search_sources(sources)):
                await message.reply_text(chunk)
        for artifact in artifacts:
            await send_artifact(message, artifact)


async def _deliver_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    thinking: Any,
    model_key: str,
    trigger: str,
    answer: str,
    *,
    playful: bool,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    # Automatic replies are text-only. Strip legacy model tags defensively.
    _, text = split_sticker_tag(answer)
    if not text and trigger != "主动接话":
        text = "我在呢，接着说吧。"

    if text:
        chunks = split_text(text)
        first = await _edit_or_reply(thinking, message, chunks[0])
        if first is not None:
            _store_model(context, chat.id, first.message_id, model_key)
            _memory(context).add(chat.id, "然然", chunks[0], message_id=first.message_id)
        for chunk in chunks[1:]:
            sent = await message.reply_text(chunk)
            _store_model(context, chat.id, sent.message_id, model_key)
            _memory(context).add(chat.id, "然然", chunk, message_id=sent.message_id)
    else:
        try:
            if thinking is not None:
                await thinking.delete()
        except TelegramError:
            logger.debug("Could not delete thinking message")



async def on_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or message.sticker is None:
        return
    if user.is_bot:
        return

    if is_private(chat) and _is_owner(update, context):
        added = _stickers(context).add_sticker(message.sticker)
        await message.reply_text("这张我收了。" if added else "这张我已经有了。")
        return

    speaker = display_name(user)
    _memory(context).add(chat.id, speaker, "（丢了张表情）")
    replied = message.reply_to_message
    reply_to_bot = bool(
        replied and replied.from_user and replied.from_user.id == context.bot.id
    )
    if reply_to_bot:
        model_key = _state(context).get_model(chat.id)
        stored = context.bot_data["reply_models"].get((chat.id, replied.message_id))
        if stored:
            model_key = stored
        await _run_query(
            update,
            context,
            model_key,
            "有人朝你发了张表情包，根据你看到的画面用文字自然回应，不发送贴纸。",
            trigger="有人丢表情",
        )
        return
    if random.random() < 0.15:
        await react_to(message, "表情")


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_unauthorized(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    newcomers = [
        user
        for user in (message.new_chat_members or [])
        if user.id != context.bot.id and not user.is_bot
    ]
    if not newcomers:
        return
    for user in newcomers[:3]:
        await message.reply_text(welcome_line(display_name(user)))


async def _edit_or_reply(thinking: Any, source: Any, text: str) -> Any:
    if thinking is None:
        return await source.reply_text(text)
    try:
        await thinking.edit_text(text)
        return thinking
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return thinking
        logger.info("edit_text failed, sending a new message: %s", exc)
    except TelegramError as exc:
        logger.info("edit_text failed, sending a new message: %s", exc)
    return await source.reply_text(text)


async def _register_commands(application: Application) -> None:
    bot = application.bot
    jobs: list[tuple[object, list[BotCommand]]] = [
        (BotCommandScopeDefault(), PUBLIC_COMMANDS),
        (BotCommandScopeAllGroupChats(), PUBLIC_COMMANDS),
        (BotCommandScopeAllPrivateChats(), OWNER_COMMANDS),
    ]
    whitelist_commands = [*PUBLIC_COMMANDS, BotCommand("nsfw", "开关成人向人设")]
    for chat_id in application.bot_data["whitelist"].list_ids():
        jobs.append((BotCommandScopeChat(chat_id=chat_id), whitelist_commands))
    for scope, commands in jobs:
        await bot.set_my_commands(commands, scope=scope)
        await bot.set_my_commands(commands, scope=scope, language_code="zh")
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    await bot.set_my_short_description("群里的二次元损友。用 /sticker 做贴纸、/quote 做金句卡、/choose 帮你选，或直接 @我。")
    await bot.set_my_description(
        "然然。群里可以 @我说话，也可以发图片给我看，还可以点这些命令：\n"
        "/yun 二次元抽签与 AI 解签\n"
        "/waifu 随机二次元图\n"
        "/setu 随机涩图\n"
        "/sticker 回复图片做贴纸，可加字\n"
        "/quote 金句卡片 · /choose 帮你选\n"
        "/search 联网搜索 · AI总结\n"
        "/stickers 表情包\n"
        "/model 切换模型"
    )
    logger.info("Bot commands registered for default, group, private, and whitelist chats")


async def post_init(application: Application) -> None:
    await _register_commands(application)
    try:
        await application.bot_data["stickers"].ensure_defaults(application.bot)
        logger.info("Sticker bank ready: %s", application.bot_data["stickers"].count())
    except Exception:
        logger.exception("Failed to import default stickers")
    try:
        await application.bot_data["codex"].probe()
    except CodexError as exc:
        logger.warning("启动时未就绪 Codex：%s", exc)


def build_application(settings: Settings) -> Application:
    default_model = default_key_from_settings(settings.deepseek_model)
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["persona_extra"] = load_extra_persona(settings.persona_extra_path)
    application.bot_data["skills"] = load_skills(settings.skills_dir)
    application.bot_data["whitelist"] = Whitelist(
        WHITELIST_PATH, settings.initial_group_ids
    )
    application.bot_data["chat_state"] = ChatStateStore(CHAT_STATE_PATH, default_model)
    application.bot_data["memory"] = ChatMemory(path=settings.data_dir / "chat_memory.json")
    application.bot_data["daily_journal"] = DailyJournal(settings.data_dir / "daily_messages.sqlite3")
    application.bot_data["daily_analyzer"] = DailyAnalyzer()
    application.bot_data["auto_gate"] = AutoReplyGate(chance=1.0)
    application.bot_data["stickers"] = StickerBank(STICKERS_PATH)
    from bot.sticker_packs import GroupStickerPacks
    application.bot_data["group_packs"] = GroupStickerPacks(GROUP_PACKS_PATH)
    application.bot_data["reply_models"] = {}
    application.bot_data["locks"] = {}
    application.bot_data["codex"] = CodexProvider(
        timeout=settings.codex_timeout,
        workdir=settings.data_dir / "codex_workspace",
        secrets=settings.secrets,
    )
    application.bot_data["deepseek"] = DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
    )

    application.add_handler(MessageHandler(filters.ALL, on_daily_message), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("chatid", chatid_cmd))
    application.add_handler(CommandHandler("model", model_cmd))
    application.add_handler(CommandHandler("auto", auto_cmd))
    application.add_handler(CommandHandler("codex", codex_cmd))
    application.add_handler(CommandHandler("deepseek", deepseek_cmd))
    application.add_handler(CommandHandler("search", search_cmd))
    application.add_handler(CommandHandler("s", search_cmd))
    application.add_handler(CommandHandler("persona", persona_cmd))
    application.add_handler(CommandHandler("nsfw", nsfw_cmd))
    application.add_handler(CommandHandler("skill", skill_cmd))
    application.add_handler(CommandHandler("whitelist", whitelist_list_cmd))
    application.add_handler(CommandHandler("whitelist_add", whitelist_add_cmd))
    application.add_handler(CommandHandler("whitelist_remove", whitelist_remove_cmd))
    application.add_handler(CommandHandler("yun", yun_cmd))
    application.add_handler(CommandHandler("waifu", waifu_cmd))
    application.add_handler(CommandHandler("setu", setu_cmd))
    application.add_handler(CommandHandler("sticker", sticker_cmd))
    application.add_handler(CommandHandler("quote", quote_cmd))
    application.add_handler(CommandHandler("choose", choose_cmd))
    application.add_handler(CommandHandler("pack", pack_cmd))
    application.add_handler(CommandHandler("pack_list", pack_list_cmd))
    application.add_handler(CommandHandler("pack_delete", pack_delete_cmd))
    application.add_handler(CommandHandler("pack_rename", pack_rename_cmd))
    application.add_handler(CommandHandler("stickers", stickers_cmd))
    application.add_handler(CommandHandler("stickerset", stickerset_cmd))
    application.add_handler(CommandHandler("learn", learn_cmd))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    application.add_handler(MessageHandler(filters.Sticker.ALL, on_sticker))
    application.add_handler(MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, on_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    return application


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    if not isinstance(update, Update) or update.effective_message is None:
        return
    if not _can_use(update, context):
        return
    try:
        await update.effective_message.reply_text("处理失败，请稍后重试。")
    except TelegramError:
        logger.debug("Failed to send error notice")


def main() -> None:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    persona_extra = load_extra_persona(settings.persona_extra_path)
    logger.info(
        "External persona: %s chars from %s (per-chat switch, default off)",
        len(persona_extra),
        settings.persona_extra_path,
    )
    logger.info(
        "Skills: %s loaded from %s",
        len(load_skills(settings.skills_dir).all()),
        settings.skills_dir,
    )
    logger.info(
        "Starting tg-llm-bot as owner_id=%s deepseek_model=%s deepseek_key=%s",
        settings.owner_id,
        settings.deepseek_model,
        secret_fingerprint(settings.deepseek_api_key),
    )
    application = build_application(settings)
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
