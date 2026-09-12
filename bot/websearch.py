from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# DeepSeek 原生联网搜索走 Anthropic 兼容 Messages API（不是 chat-completions 基址）。
SEARCH_URL = "https://api.deepseek.com/anthropic/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
SEARCH_MAX_TOKENS = 4096
SEARCH_MAX_USES = 5
DEFAULT_MAX_RESULTS = 6
SUMMARY_LIMIT = 4000


class WebSearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    page_age: str


@dataclass(frozen=True)
class SearchReport:
    hits: list[SearchHit]
    summary: str


def _citation_snippets(blocks: list[Any]) -> dict[str, str]:
    """从 text 块的 citations[] 里按 url 收集引用摘录（首次出现优先）。"""
    snippets: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for cite in block.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            url = str(cite.get("url") or "").strip()
            text = str(cite.get("cited_text") or "").strip()
            if url and text and url not in snippets:
                snippets[url] = text
    return snippets


def _summary_text(blocks: list[Any]) -> str:
    """拼接 text 块的正文，作为搜索服务生成的汇总（不保证每条都有引用）。"""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "").strip()
        if text:
            parts.append(text)
    summary = "\n\n".join(parts).strip()
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[:SUMMARY_LIMIT].rstrip() + "\n（汇总过长，已截断）"
    return summary


def parse_search_response(data: dict[str, Any]) -> SearchReport:
    """把 DeepSeek Messages 响应映射为去重后的搜索结果与汇总正文。

    没有 web_search_tool_result 块视为未触发原生搜索，直接报错而不是降级抓文本。
    """
    blocks = data.get("content") or []
    if not isinstance(blocks, list):
        raise WebSearchError("搜索响应格式异常，请稍后重试。")
    result_blocks = [
        block
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "web_search_tool_result"
    ]
    if not result_blocks:
        raise WebSearchError("这次没有触发原生联网搜索，请换个问法再试。")

    snippets = _citation_snippets(blocks)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in result_blocks:
        for item in block.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            hits.append(
                SearchHit(
                    url=url,
                    title=str(item.get("title") or "").strip(),
                    snippet=snippets.get(url, ""),
                    page_age=str(item.get("page_age") or "").strip(),
                )
            )
    return SearchReport(hits=hits, summary=_summary_text(blocks))


async def deepseek_web_search(
    api_key: str,
    query: str,
    *,
    model: str = "deepseek-v4-flash",
    max_results: int = DEFAULT_MAX_RESULTS,
    client: httpx.AsyncClient | None = None,
) -> SearchReport:
    """调用 DeepSeek 服务端原生 web_search_20250305 工具执行一次联网搜索。

    一次搜索会消耗一个完整模型轮次；返回去重后的结构化来源
    （url/title/page_age）以及搜索服务自己生成的汇总正文。
    """
    if not api_key:
        raise WebSearchError("未配置 DEEPSEEK_API_KEY，无法联网搜索。请在 .env 中填写后重启。")
    body = {
        "model": model,
        "max_tokens": SEARCH_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": f"Perform a web search for the query: {query}"}],
            }
        ],
        "tools": [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": SEARCH_MAX_USES}
        ],
    }
    headers = {
        "x-api-key": api_key,
        "authorization": f"Bearer {api_key}",
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
        "accept": "application/json",
    }
    timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)

    async def _post(transport: httpx.AsyncClient) -> httpx.Response:
        return await transport.post(SEARCH_URL, headers=headers, json=body)

    try:
        if client is not None:
            response = await _post(client)
        else:
            async with httpx.AsyncClient(timeout=timeout) as own:
                response = await _post(own)
    except httpx.TimeoutException as exc:
        raise WebSearchError("联网搜索超时，请稍后再试。") from exc
    except httpx.HTTPError as exc:
        logger.warning("Web search network error: %s", exc)
        raise WebSearchError("无法连接搜索服务，请检查网络后重试。") from exc

    if response.status_code == 401:
        raise WebSearchError("DEEPSEEK_API_KEY 无效或已过期，请检查 .env。")
    if response.status_code == 402:
        raise WebSearchError("DeepSeek 余额不足或账号受限，无法联网搜索。")
    if response.status_code == 429:
        raise WebSearchError("搜索请求过于频繁，请稍后再试。")
    if response.status_code >= 400:
        raise WebSearchError(f"联网搜索失败（HTTP {response.status_code}）。{_error_detail(response)}".rstrip())

    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("response is not an object")
        report = parse_search_response(data)
    except WebSearchError:
        raise
    except (ValueError, TypeError) as exc:
        raise WebSearchError("搜索响应解析失败，请稍后重试。") from exc

    if not report.hits:
        raise WebSearchError("没搜到可用结果，换个关键词再试试。")
    return SearchReport(hits=report.hits[:max_results], summary=report.summary)


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return " " + message.strip()[:180]
            if isinstance(error, str) and error.strip():
                return " " + error.strip()[:180]
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return " " + message.strip()[:180]
    except (ValueError, TypeError):
        return ""
    return ""


SEARCH_ANSWER_SYSTEM = """你在 Telegram 群或私聊中以「然然」的角色回答联网搜索的问题。
若已给出的搜索结果不足以回答，你可以只输出一行 [搜索: 更具体的关键词] 再要一轮联网（同一个问题最多再要几轮，系统会拦住超出的次数）；搜索结果里已经有的信息不要重复搜。
要求：
- 只依据提供的网页搜索结果和搜索服务汇总回答，不要编造结果里没有的信息；材料与问题无关或不足以回答时，如实说明。
- 先给结论，再给关键细节；在相应句子后用 [1]、[2] 这样的编号标注每条信息对应的来源。
- 「页面时间」字段代表结果页面的时间，不确定的信息不要当成确定事实。
- 结尾不要再列来源链接，来源会由系统单独附上。
- 保持自然语气：认真问题直接给实质答案，可以带一点温柔，不要演戏。
"""


def search_context_text(query: str, hits: list, summary: str = "") -> str:
    """组装给回答模型看的用户提示词，搜索结果用 JSON 包裹以与指令隔离。"""
    import json

    parts = [
        "用户在聊天里发起了联网搜索，请基于下面的网页搜索结果回答，并在相应句子后用 [编号] 标注引用来源。",
        json.dumps({"问题": query}, ensure_ascii=False),
        "网页搜索结果：",
    ]
    for index, hit in enumerate(hits, 1):
        parts.append(
            json.dumps(
                {
                    "编号": index,
                    "标题": hit.title,
                    "页面时间": hit.page_age,
                    "摘要": hit.snippet,
                    "链接": hit.url,
                },
                ensure_ascii=False,
            )
        )
    if summary:
        parts.append(
            "搜索服务返回的汇总（由搜索服务生成，只作参考；引用时仍以编号来源为准）：\n" + summary
        )
    parts.append("请用中文回答，先给结论再给关键细节；只依据这些材料，材料不足以回答时如实说明。")
    return "\n".join(parts)


def format_search_sources(hits: list) -> str:
    """生成附带真实链接的来源清单（URL 来自结构化结果，不经模型生成）。"""
    lines = ["来源："]
    for index, hit in enumerate(hits, 1):
        title = hit.title or hit.url
        line = f"{index}. {title}"
        if hit.page_age:
            line += f"（{hit.page_age}）"
        lines.append(line)
        lines.append(f"   {hit.url}")
    return "\n".join(lines)

# 一轮对话里最多允许自动联网几次（模型说信息不够时可以继续要下一轮）。
MAX_SEARCH_ROUNDS = 3
# 附给用户的来源条数上限（跨多轮去重后）。
SOURCE_LIMIT = 10


def strip_search_marker(text: str) -> str:
    """去掉残留的 [搜索: …] 标记，避免把内部控制标记发到聊天里。"""
    return SEARCH_MARKER_RE.sub("", text or "").strip()


def dedupe_hits(hits: list, limit: int = SOURCE_LIMIT) -> list[SearchHit]:
    """按 URL 去重并截断，供附在回答末尾。"""
    merged: list[SearchHit] = []
    seen: set[str] = set()
    for hit in hits or []:
        url = getattr(hit, "url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        merged.append(hit)
        if len(merged) >= limit:
            return merged
    return merged


def merge_report_hits(reports: list, limit: int = SOURCE_LIMIT) -> list[SearchHit]:
    """跨多轮搜索合并来源并按 URL 去重，供附在回答末尾。"""
    flat: list[SearchHit] = []
    for report in reports:
        flat.extend(getattr(report, "hits", []) or [])
    return dedupe_hits(flat, limit)

# 模型要求联网时输出的标记，例如：[搜索: 今日科技新闻]
SEARCH_MARKER_RE = re.compile(
    r"[\[【]\s*(?:联网搜索|搜索|搜|search)\s*[:：]\s*(?P<query>[^\]】\n]{1,120}?)\s*[\]】]",
    re.IGNORECASE,
)

# 明确要求联网搜索的措辞（用户自然语言，不是斜杠命令）。
_NET_WORDS = ("联网", "上网", "网上", "在线", "搜索引擎", "百度", "谷歌", "google", "baidu")
_MENTION_RE = re.compile(r"@[A-Za-z0-9_]{3,}")
_VOCATIVE_RE = re.compile(r"(小猪|猪猪|小然|然然|机器人|bot)")
_REQUEST_PREFIX_RE = re.compile(r"(帮我|帮忙|给我|替我|麻烦你|麻烦|请你|请)")
_SEARCH_VERB_RE = re.compile(
    r"(搜索一下|搜索|搜一下|搜下|搜搜看|搜搜|搜个|搜|百度一下|谷歌一下|"
    r"查询一下|查询|查找|查一查|查一下|查下|查查|查|找一找|找一下|找找看|找|"
    r"看一看|看看|看|了解|浏览|读)"
)
_TAIL_PARTICLE_RE = re.compile(r"(一下|的话|呢|吧|啊|呀|嘛)")
_SEARCH_PHRASE_RE = re.compile(
    r"(搜索一下|搜索下|搜索|搜一下|搜下|搜搜看|搜搜|搜个|帮我搜|帮忙搜|给我搜|替我搜|"
    r"查一下|查下|查询一下|查询|查找|查查|帮我查|帮忙查|给我查|替我查|"
    r"找一下|找找看|百度一下|谷歌一下)"
)
_VERB_RE = re.compile(r"(搜|查|找|看|了解|浏览|读)")
# 问的是"有没有联网能力"，不是让你去搜；以及过去式/转述。
_NOT_REQUEST_RE = re.compile(
    r"(能不能|可不可以|能否|是否会|会不会)(联网|搜索|上网)|"
    r"(你|您).{0,4}(能|可以|会|有).{0,4}(联网|搜索|上网)|"
    r"(联网|搜索|上网|查资料).{0,4}(能力|功能)|"
    r"(搜过|查过|搜了|查了|找过了)|"
    r"(网上|联网|在网上)(说|讲|聊|看到|看的|来说)"
)
# 抠掉触发词后剩不下实义内容时，不当成搜索请求（例如"我去你还没法联网吗"）。
_MEANINGLESS_RE = re.compile(
    r"^[\s我你他她它您这那有没有还没法能不吗么呢吧啊呀哦去来了的是在个的就都很太也还只把被给和与及请帮下看的什么玩意儿啊]*$"
)


def parse_search_marker(text: str) -> str | None:
    """从模型输出里解析 [搜索: 关键词] 标记；没有标记返回 None。"""
    match = SEARCH_MARKER_RE.search(text or "")
    if not match:
        return None
    query = " ".join(match.group("query").split()).strip(" ，,。.！!？?~～、:：")
    return query[:120] or None


def parse_search_request(text: str) -> str | None:
    """判断用户这句话是不是明确要求联网搜索，是则返回搜索词，否则 None。

    只认明确的请求措辞（搜一下 / 帮我查 / 联网搜索……），闲聊和"你能不能联网"
    这类提问交给模型自己回答；模型真需要实时信息时还有 [搜索: ...] 标记兜底。
    """
    body = " ".join((text or "").split())
    if not body or len(body) > 200:
        return None
    if _NOT_REQUEST_RE.search(body):
        return None
    phrase = _SEARCH_PHRASE_RE.search(body)
    net_word = any(word in body.lower() for word in _NET_WORDS)
    if not phrase and not (net_word and _VERB_RE.search(body)):
        return None
    # 先去掉称谓和请求前缀，再去触发词，避免"帮我搜索"被切成"索"。
    query = _MENTION_RE.sub(" ", body)
    query = _VOCATIVE_RE.sub(" ", query)
    query = _REQUEST_PREFIX_RE.sub(" ", query)
    for word in _NET_WORDS:
        query = query.replace(word, " ")
    query = _SEARCH_VERB_RE.sub(" ", query)
    query = _TAIL_PARTICLE_RE.sub(" ", query)
    query = " ".join(query.split()).strip(" ，,。.！!？?~～、:：;；'\"“”")
    if len(query) < 2 or _MEANINGLESS_RE.match(query):
        return None
    return query[:120]
