from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL_KEY = "flash"
VISION_MODEL_KEY = "vision"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    provider: str
    api_model: str | None
    title: str
    vision: bool = False


MODELS: dict[str, ModelSpec] = {
    "flash": ModelSpec("flash", "deepseek", "deepseek-v4-flash", "DeepSeek V4.1 Flash"),
    "vision": ModelSpec(
        "vision",
        "deepseek",
        "deepseek-v4-flash-vision-exp",
        "DeepSeek V4 Flash Vision",
        vision=True,
    ),
    "pro": ModelSpec("pro", "deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro"),
    "chat": ModelSpec("chat", "deepseek", "deepseek-chat", "DeepSeek Chat"),
    "codex": ModelSpec("codex", "codex", "gpt-6-astra", "Codex CLI (gpt-6-astra)"),
}

_ALIASES = {
    "flash": "flash",
    "flash-max": "flash",
    "flashmax": "flash",
    "flash_max": "flash",
    "max": "flash",
    "v4-flash": "flash",
    "v4flash": "flash",
    "v4.1": "flash",
    "v41": "flash",
    "v4.1flash": "flash",
    "v41flash": "flash",
    "deepseek-v4-flash": "flash",
    "deepseek-v4.1-flash": "flash",
    "deepseek-v4.1": "flash",
    "deepseekv4.1flash": "flash",
    "deepseek": "flash",
    "ds": "flash",
    "vision": "vision",
    "vl": "vision",
    "v4-vision": "vision",
    "v4vision": "vision",
    "flash-vision": "vision",
    "flashvision": "vision",
    "v4-flash-vision": "vision",
    "deepseek-v4-flash-vision-exp": "vision",
    "deepseek-v4-flash-vision": "vision",
    "multimodal": "vision",
    "看图": "vision",
    "pro": "pro",
    "v4-pro": "pro",
    "v4pro": "pro",
    "deepseek-v4-pro": "pro",
    "chat": "chat",
    "deepseek-chat": "chat",
    "codex": "codex",
}


def resolve_model_key(raw: str | None, fallback: str = DEFAULT_MODEL_KEY) -> str:
    if not raw:
        return fallback if fallback in MODELS else DEFAULT_MODEL_KEY
    key = _ALIASES.get(raw.strip().lower().replace(" ", ""))
    if key:
        return key
    if raw in MODELS:
        return raw
    return fallback if fallback in MODELS else DEFAULT_MODEL_KEY


def parse_model_key(raw: str) -> str | None:
    key = _ALIASES.get((raw or "").strip().lower().replace(" ", ""))
    if key:
        return key
    if raw in MODELS:
        return raw
    return None


def default_key_from_settings(deepseek_model: str) -> str:
    return resolve_model_key(deepseek_model, DEFAULT_MODEL_KEY)


def model_for_images(model_key: str) -> str:
    spec = MODELS.get(model_key)
    if spec is not None and spec.vision:
        return spec.key
    return VISION_MODEL_KEY


def model_list_text(current: str) -> str:
    lines = ["可用模型："]
    for spec in MODELS.values():
        mark = "（当前）" if spec.key == current else ""
        extra = " · 可看图" if spec.vision else ""
        lines.append(f"• {spec.key} — {spec.title}{extra}{mark}")
    lines.append("切换：/model flash  或  /model vision  或  /model pro  或  /model codex")
    lines.append("发图片时会自动走 Vision。")
    return "\n".join(lines)
