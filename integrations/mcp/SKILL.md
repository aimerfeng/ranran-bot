---
name: connect-ranran
description: 把 ranran bot 的 MCP server 接入任意聊天平台或 AI 客户端：配置、工具契约、会话约定与自检清单。
when: 接入|集成|对接|MCP|其他平台|connect
always: false
includes: []
---

# 把 ranran 接进你的平台

ranran 是一个中文对话角色（然然）+ 一套能力（联网搜索 / 生成文件 / 技能说明 / 历史回溯）。
它通过 **MCP（stdio）** 暴露，任何支持 MCP 的客户端，或你自己写的平台适配器，都能接。

## 1. 准备服务端

```bash
git clone https://github.com/aimerfeng/ranran-bot && cd ranran-bot
python -m venv venv
pip install -r requirements.txt -r requirements-mcp.txt
copy .env.example .env      # 填 DEEPSEEK_API_KEY；只接 MCP 时 TELEGRAM_* 用占位值即可
```

> `load_settings()` 会校验 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_ID` 存在，所以纯 MCP 部署也要给它们占位值。
> 真正的模型能力来自 `DEEPSEEK_API_KEY`。

自检：

```bash
python integrations/mcp/client_example.py --chat "你好"
```

能列出 7 个工具、并收到一句然然的回复，说明服务端就绪。

## 2. 接法 A：MCP 客户端配置

通用 JSON（Claude Desktop / Cursor / 多数客户端同构）：

```json
{
  "mcpServers": {
    "ranran": {
      "command": "<绝对路径>/venv/Scripts/python.exe",
      "args": ["-m", "adapters.mcp.server"],
      "cwd": "<绝对路径>/ranran-bot",
      "env": { "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

要点：`command` 必须是**虚拟环境里的解释器绝对路径**（否则找不到 `mcp` 依赖）；`cwd` 必须是仓库根目录（否则读不到 `.env` 与 `skills/`）。
远程接入改用 HTTP：服务端跑 `python -m adapters.mcp.server --http`，客户端连 `http://<host>:<port>/mcp`。

## 3. 接法 B：在自己的平台代码里调用

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command=r"<绝对路径>\venv\Scripts\python.exe",
    args=["-m", "adapters.mcp.server"],
    cwd=r"<绝对路径>\ranran-bot",
)

async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("ranran_chat", {
            "message": user_text,
            "session_id": f"discord:{channel_id}:{user_id}",   # 会话隔离靠它
            "speaker": user_name,
        })
        reply = "".join(getattr(b, "text", "") for b in result.content)
```

平台适配器的通用形状：**收到消息 → `ranran_chat` → 把返回文本发回去**。别自己拼人格，人设与 skill 在服务端。

## 4. 工具契约

| 工具 | 参数 | 返回 | 什么时候用 |
| --- | --- | --- | --- |
| `ranran_chat` | `message`（必填）、`session_id`、`speaker` | 回复正文；有来源时附「来源：」；有文件时附绝对路径 | 平台的主要入口 |
| `ranran_session` | `session_id`、`model`、`nsfw` | 当前会话状态 | 切换模型（`flash`/`pro`/`codex`）、开关外部人设 |
| `web_search` | `query` | 编号化的标题/时间/摘要/链接 + 搜索汇总 | 只要资料、不要人格时 |
| `write_file` | `filename`、`content`、`format`（md/txt/csv） | 落盘绝对路径 | 生成可下载内容再自己上传 |
| `list_skills` | 无 | skill 目录（名字 + 说明 + 触发词） | 先看有哪些技能 |
| `use_skill` | `name` | 该 skill 的完整正文（含嵌套） | 拿到规则后自己执行 |
| `search_history` | `keyword`、`session_id` | 该会话里匹配的历史行 | 回查「之前说过什么」 |

约定：

- **业务失败不抛协议错误**：搜索失败、文件过大、没有该 skill 都以可读中文返回，调用方直接展示即可。
- **只有 `ranran_chat` / `web_search` 花模型额度**；`web_search` 一次会消耗一个完整模型轮次。
- 返回是文本 content，不是结构化 JSON；需要结构化就自己解析。

## 5. 会话设计

- `session_id` 决定记忆边界：一个平台的用户/频道对应一个稳定 id，例如 `discord:{guild}:{channel}:{user}`、`qq:{group}:{user}`。**同 id 共享记忆，跨 id 完全隔离。**
- 记忆与配置落盘在 `data/runtime_memory.json` / `data/runtime_sessions.json`，服务端重启不丢。
- 想让整个频道共用一段记忆，就用频道级 id，别拼用户 id。
- 同一个 `session_id` 不要并发调用（写入顺序不保证）；不同 id 可以并发。

## 6. 错误与兜底

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动即退出，提示缺少 `TELEGRAM_BOT_TOKEN` | `.env` 没填 | 填占位值或真实值 |
| `ModuleNotFoundError: mcp` | 没装依赖，或配置里用的是系统解释器 | `pip install -r requirements-mcp.txt`，配置写 venv 绝对路径 |
| 回复「DeepSeek API Key 无效」 | key 错或过期 | 改 `.env` 的 `DEEPSEEK_API_KEY` 后重启服务端 |
| 回复「无法连接 DeepSeek」 | 网络或代理问题 | 在 `.env` 配 `HTTPS_PROXY`，或部署到能直连的机器 |
| 记忆没生效 | `session_id` 每次都变 | 用稳定的平台侧 id |

## 7. 安全须知

- `.env` 与 `data/` 含密钥和真实聊天记录，**不要提交、不要外发**；运行日志的 URL 里带 bot token。
- **成人向外部人设默认关闭**，只有部署者在服务端配好人设文件、且会话显式 `/nsfw on`（或 `ranran_session(nsfw="on")`）才生效。
- 服务端放在你自己的可信环境里；开放 HTTP 传输时自行做鉴权（MCP 本身不管鉴权）。

## 8. 自检清单

1. `python integrations/mcp/client_example.py` 能连上并列出 7 个工具。
2. `--chat "你好"` 能收到符合角色口吻的回复。
3. 同一 `session_id` 连续调用两次，第二次能记起第一次说的内容。
4. 平台能正确发送回复；`write_file` 返回的路径能被平台读取并上传。
