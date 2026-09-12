import unittest

import httpx

from bot.websearch import (
    SearchHit,
    SearchReport,
    WebSearchError,
    deepseek_web_search,
    format_search_sources,
    parse_search_marker,
    parse_search_request,
    parse_search_response,
    search_context_text,
)


def _payload(summary: str = "显卡价格 9 月普涨。") -> dict:
    return {
        "content": [
            {
                "type": "web_search_tool_result",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://a.example/1",
                        "title": "A 一号",
                        "page_age": "2 days ago",
                    },
                    {
                        "type": "web_search_result",
                        "url": "https://b.example/2",
                        "title": "B 二号",
                        "page_age": "2025-09-01",
                    },
                    {"type": "web_search_result", "url": "", "title": "无链接"},
                ],
            },
            {
                "type": "web_search_tool_result",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://a.example/1",
                        "title": "A 重复",
                    }
                ],
            },
            {
                "type": "text",
                "text": "引用段落……",
                "citations": [
                    {"type": "web_search_result", "url": "https://a.example/1", "cited_text": "A 的摘要"},
                    {"type": "web_search_result", "url": "https://b.example/2", "cited_text": "B 的摘要"},
                ],
            },
            {"type": "text", "text": summary},
        ]
    }


class ParseTests(unittest.TestCase):
    def test_extracts_dedupes_joins_snippets_and_summary(self):
        report = parse_search_response(_payload())
        self.assertIsInstance(report, SearchReport)
        self.assertEqual(
            [h.url for h in report.hits], ["https://a.example/1", "https://b.example/2"]
        )
        self.assertEqual(report.hits[0].title, "A 一号")
        self.assertEqual(report.hits[0].snippet, "A 的摘要")
        self.assertEqual(report.hits[0].page_age, "2 days ago")
        self.assertEqual(report.hits[1].snippet, "B 的摘要")
        self.assertIn("显卡价格 9 月普涨", report.summary)

    def test_summary_is_truncated_when_too_long(self):
        report = parse_search_response(_payload(summary="长" * 6000))
        self.assertLessEqual(len(report.summary), 4100)
        self.assertTrue(report.summary.endswith("（汇总过长，已截断）"))

    def test_no_result_blocks_is_error(self):
        with self.assertRaises(WebSearchError):
            parse_search_response({"content": [{"type": "text", "text": "没有搜索"}]})

    def test_malformed_content_is_error(self):
        with self.assertRaises(WebSearchError):
            parse_search_response({"content": "nope"})

    def test_context_and_sources_include_real_urls(self):
        report = parse_search_response(_payload())
        text = search_context_text("今天天气如何", report.hits, report.summary)
        self.assertIn("今天天气如何", text)
        self.assertIn("https://a.example/1", text)
        self.assertIn("A 的摘要", text)
        self.assertIn("显卡价格 9 月普涨", text)
        sources = format_search_sources(report.hits)
        self.assertIn("来源：", sources)
        self.assertIn("https://b.example/2", sources)
        self.assertIn("（2025-09-01）", sources)


class WebSearchAskTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_sends_native_tool_and_caps_results(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_payload(), request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            report = await deepseek_web_search("sk-test", "天气", client=client)
        finally:
            await client.aclose()
        self.assertIsInstance(report, SearchReport)
        self.assertEqual(len(report.hits), 2)
        self.assertIsInstance(report.hits[0], SearchHit)

    async def test_success_request_shape(self):
        import json

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=_payload(), request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await deepseek_web_search("sk-test", "天气", client=client, max_results=1)
        finally:
            await client.aclose()
        self.assertTrue(captured["url"].endswith("/anthropic/v1/messages"))
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["headers"]["x-api-key"], "sk-test")
        body = captured["body"]
        self.assertEqual(
            body["tools"],
            [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        )
        self.assertIn("天气", body["messages"][0]["content"][0]["text"])

    async def test_missing_key_fails_before_http(self):
        with self.assertRaises(WebSearchError) as ctx:
            await deepseek_web_search("", "天气")
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))

    async def test_http_error_mapping(self):
        async def run_with(status: int) -> str:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(status, json={"error": {"message": "boom"}}, request=request)

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                with self.assertRaises(WebSearchError) as ctx:
                    await deepseek_web_search("sk-test", "天气", client=client)
                return str(ctx.exception)
            finally:
                await client.aclose()

        self.assertIn("无效", await run_with(401))
        self.assertIn("余额", await run_with(402))
        self.assertIn("频繁", await run_with(429))
        self.assertIn("HTTP 500", await run_with(500))

    async def test_empty_results_is_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"content": []}, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(WebSearchError):
                await deepseek_web_search("sk-test", "天气", client=client)
        finally:
            await client.aclose()


class SearchIntentTests(unittest.TestCase):
    def test_real_chat_phrasing_is_a_request(self):
        self.assertEqual(parse_search_request("小猪小猪 联网帮我搜索热点新闻"), "热点新闻")
        self.assertEqual(parse_search_request("联网帮我搜索热点新闻"), "热点新闻")
        self.assertEqual(parse_search_request("帮我查一下北京明天天气"), "北京明天天气")
        self.assertEqual(parse_search_request("@aimerranbot 搜一下最近的显卡价格"), "最近的显卡价格")
        self.assertEqual(parse_search_request("上网看看今天有什么科技新闻"), "今天有什么科技新闻")

    def test_capability_questions_and_statements_are_not_requests(self):
        for text in (
            "你能联网吗",
            "我去你还没法联网吗",
            "你有没有联网能力",
            "我查了一下劳动合同法第38条",
            "网上说显卡要涨价了",
            "年假一般是多少天",
            "加班费可以多给对吧",
        ):
            self.assertIsNone(parse_search_request(text), text)

    def test_empty_and_overlong_input(self):
        self.assertIsNone(parse_search_request(""))
        self.assertIsNone(parse_search_request("搜索" + "长" * 300))

    def test_marker_parsing(self):
        self.assertEqual(parse_search_marker("[搜索: 今日热点新闻]"), "今日热点新闻")
        self.assertEqual(parse_search_marker("【联网搜索：显卡价格】"), "显卡价格")
        self.assertEqual(
            parse_search_marker("先看一下\n[search: gpu price]\n再回答"), "gpu price"
        )
        self.assertIsNone(parse_search_marker("好的，我知道了"))
        self.assertIsNone(parse_search_marker("[搜索: ]"))


if __name__ == "__main__":
    unittest.main()
