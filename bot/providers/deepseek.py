from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from bot.harness.types import ToolCall
from bot.images import ImagePart
from bot.settings import secret_fingerprint

logger = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
VISION_MODEL = "deepseek-v4-flash-vision-exp"


class DeepSeekError(RuntimeError):
    pass


class ToolsUnsupported(DeepSeekError):
    """当前模型/端点不支持 function calling，harness 会退回无工具模式。"""


@dataclass
class ChatResult:
    """一次 chat 调用的结构化结果（正文 + 工具调用 + 原样回填用的 assistant 消息）。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    tools_unsupported: bool = False


def user_content(prompt: str, images: list[ImagePart] | None = None) -> str | list[dict[str, Any]]:
    if not images:
        return prompt
    blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": image.data_url(), "detail": "auto"},
            }
        )
    return blocks


class DeepSeekProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def ask(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        images: list[ImagePart] | None = None,
    ) -> str:
        if not self.api_key:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启。")

        chosen = model or self.model
        if images and chosen != VISION_MODEL:
            chosen = VISION_MODEL

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content(prompt, images)})
        payload = {
            "model": chosen,
            "messages": messages,
            "stream": False,
            "temperature": 0.95,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(DEEPSEEK_URL, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise DeepSeekError("DeepSeek 请求超时，请稍后再试。") from exc
        except httpx.HTTPError as exc:
            logger.warning("DeepSeek network error: %s", exc)
            raise DeepSeekError("无法连接 DeepSeek，请检查网络后重试。") from exc

        if response.status_code == 401:
            logger.warning(
                "DeepSeek 401 model=%s key=%s",
                chosen,
                secret_fingerprint(self.api_key),
            )
            raise DeepSeekError("DeepSeek API Key 无效，请检查 DEEPSEEK_API_KEY。")
        if response.status_code == 402:
            raise DeepSeekError("DeepSeek 余额不足或账号受限。")
        if response.status_code == 429:
            raise DeepSeekError("DeepSeek 请求过于频繁，请稍后再试。")
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise DeepSeekError(f"DeepSeek 请求失败（HTTP {response.status_code}）。{detail}".rstrip())

        try:
            data = response.json()
            message = data["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("DeepSeek 返回格式异常，请稍后重试。") from exc

        text = (content or "").strip()
        if not text:
            raise DeepSeekError("DeepSeek 没有返回内容，请换个问法再试。")
        return text

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.95,
    ) -> ChatResult:
        """带工具的一轮对话；不支持工具时不抛错，而是回报 tools_unsupported。"""
        if not self.api_key:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启。")

        chosen = model or self.model
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(DEEPSEEK_URL, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise DeepSeekError("DeepSeek 请求超时，请稍后再试。") from exc
        except httpx.HTTPError as exc:
            logger.warning("DeepSeek network error: %s", exc)
            raise DeepSeekError("无法连接 DeepSeek，请检查网络后重试。") from exc

        if response.status_code == 401:
            raise DeepSeekError("DeepSeek API Key 无效，请检查 DEEPSEEK_API_KEY。")
        if response.status_code == 402:
            raise DeepSeekError("DeepSeek 余额不足或账号受限。")
        if response.status_code == 429:
            raise DeepSeekError("DeepSeek 请求过于频繁，请稍后再试。")
        if response.status_code >= 400:
            detail = _error_detail(response)
            if tools and _tools_rejected(response.status_code, detail):
                logger.warning("Model %s rejected tools; falling back to plain chat", chosen)
                return ChatResult(tools_unsupported=True)
            raise DeepSeekError(f"DeepSeek 请求失败（HTTP {response.status_code}）。{detail}".rstrip())

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("DeepSeek 返回格式异常，请稍后重试。") from exc

        content = (message.get("content") or message.get("reasoning_content") or "").strip()
        raw_calls = message.get("tool_calls") or []
        calls: list[ToolCall] = []
        for index, raw in enumerate(raw_calls):
            function = raw.get("function") or {} if isinstance(raw, dict) else {}
            arguments_text = str(function.get("arguments") or "")
            parse_error = ""
            arguments: dict[str, Any] = {}
            if arguments_text.strip():
                try:
                    parsed = json.loads(arguments_text)
                    if not isinstance(parsed, dict):
                        raise ValueError("arguments 必须是 JSON 对象")
                    arguments = parsed
                except (ValueError, TypeError) as exc:
                    parse_error = str(exc)
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                    raw_arguments=arguments_text,
                    parse_error=parse_error,
                )
            )

        assistant_message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if raw_calls:
            assistant_message["tool_calls"] = raw_calls
        return ChatResult(
            content=content,
            tool_calls=calls,
            assistant_message=assistant_message,
            finish_reason=str(choice.get("finish_reason") or ""),
        )


def _tools_rejected(status: int, detail: str) -> bool:
    """判断 400 是不是因为端点/模型不认 tools 参数。"""
    if status != 400:
        return False
    lowered = (detail or "").lower()
    return any(word in lowered for word in ("tool", "function", "unsupported", "not support"))

def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return " " + message.strip()[:180]
        if isinstance(error, str) and error.strip():
            return " " + error.strip()[:180]
    except (ValueError, AttributeError):
        return ""
    return ""
