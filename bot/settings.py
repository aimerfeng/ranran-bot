from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.yaml"
WHITELIST_PATH = DATA_DIR / "whitelist.json"
CHAT_STATE_PATH = DATA_DIR / "chat_state.json"
STICKERS_PATH = DATA_DIR / "stickers.json"
GROUP_PACKS_PATH = DATA_DIR / "group_sticker_packs.json"
# 外部人设（追加在系统提示词最后一部分）；可用 PERSONA_EXTRA_PATH 指向别处。
PERSONA_EXTRA_PATH = DATA_DIR / "persona_extra.txt"
# 嵌套 skill 目录（Markdown + frontmatter）
SKILLS_DIR = ROOT / "skills"


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    telegram_bot_token: str
    owner_id: int
    deepseek_api_key: str
    deepseek_model: str
    codex_timeout: int
    initial_group_ids: tuple[int, ...]
    # 外部人设文件；None 表示不追加（保持旧调用方的位置参数兼容）。
    persona_extra_path: Path | None = None
    # 是否在系统提示词最后追加"只输出正文"的输出纪律（默认开）。
    persona_output_guard: bool = True
    # skill 目录；可用 SKILLS_DIR_PATH 覆盖。
    skills_dir: Path | None = None

    @property
    def secrets(self) -> tuple[str, ...]:
        values = [self.telegram_bot_token, self.deepseek_api_key]
        return tuple(v for v in values if v)


def _as_int(name: str, raw: str | None, default: int | None = None) -> int:
    if raw is None or raw.strip() == "":
        if default is not None:
            return default
        raise SystemExit(f"缺少环境变量 {name}，请复制 .env.example 为 .env 并填写。")
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise SystemExit(f"环境变量 {name} 必须是整数，当前值无效。") from exc


def _load_initial_group_ids() -> tuple[int, ...]:
    if not CONFIG_PATH.exists():
        return ()
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    raw_ids = (data.get("whitelist") or {}).get("group_ids") or []
    ids: list[int] = []
    for item in raw_ids:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value < 0:
            ids.append(value)
    return tuple(ids)


def _skills_dir() -> Path:
    raw = (os.getenv("SKILLS_DIR_PATH") or "").strip()
    if not raw:
        return SKILLS_DIR
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError):
        return SKILLS_DIR


def _persona_output_guard() -> bool:
    raw = (os.getenv("PERSONA_OUTPUT_GUARD") or "").strip().lower()
    if not raw:
        return True
    return raw not in {"0", "off", "false", "no", "关", "关闭"}


def _persona_extra_path() -> Path:
    raw = (os.getenv("PERSONA_EXTRA_PATH") or "").strip()
    if not raw:
        return PERSONA_EXTRA_PATH
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError):
        return PERSONA_EXTRA_PATH


def secret_fingerprint(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "missing"
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"


def load_settings() -> Settings:
    # A long-lived run_bot.bat keeps the User/Machine env from when it first
    # started. Without override, a rotated DEEPSEEK_API_KEY in .env is ignored.
    load_dotenv(ROOT / ".env", override=True)
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("缺少环境变量 TELEGRAM_BOT_TOKEN，请复制 .env.example 为 .env 并填写。")

    timeout_raw = os.getenv("CODEX_TIMEOUT_SECONDS")
    timeout = _as_int("CODEX_TIMEOUT_SECONDS", timeout_raw, default=180)
    if timeout < 10:
        timeout = 10

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings(
        root=ROOT,
        data_dir=DATA_DIR,
        telegram_bot_token=token,
        owner_id=_as_int("TELEGRAM_OWNER_ID", os.getenv("TELEGRAM_OWNER_ID")),
        deepseek_api_key=(os.getenv("DEEPSEEK_API_KEY") or "").strip(),
        deepseek_model=(os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash").strip()
        or "deepseek-v4-flash",
        codex_timeout=timeout,
        initial_group_ids=_load_initial_group_ids(),
        persona_extra_path=_persona_extra_path(),
        persona_output_guard=_persona_output_guard(),
        skills_dir=_skills_dir(),
    )
