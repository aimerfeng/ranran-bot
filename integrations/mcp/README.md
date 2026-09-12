# MCP 接入（其他平台 / 其他 AI 客户端）

ranran 通过 **MCP** 暴露能力，默认 **stdio** 传输，任何支持 MCP 的客户端都能接：

- **AI 客户端**：Claude Desktop、Cursor、Claude Code 等，直接填下面的 JSON。
- **你自己的平台**：Discord / QQ / 微信 / 网页客服……写个小适配器调 `ranran_chat` 即可。
- **远程部署**：`python -m adapters.mcp.server --http` 起 streamable-http。

> 想让 AI 自己接入：把 [SKILL.md](SKILL.md) 丢给 coding agent，它会照着配好并自检。

## 安装

```bash
pip install -r requirements.txt -r requirements-mcp.txt
copy .env.example .env        # 填 DEEPSEEK_API_KEY；只接 MCP 时 TELEGRAM_* 给占位值即可
```

## 客户端配置

```json
{
  "mcpServers": {
    "ranran": {
      "command": "C:\\path\\to\\ranran-bot\\venv\\Scripts\\python.exe",
      "args": ["-m", "adapters.mcp.server"],
      "cwd": "C:\\path\\to\\ranran-bot",
      "env": { "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

`command` 用虚拟环境里的解释器绝对路径，`cwd` 用仓库根目录——这两条是最常见的踩坑点。

## 工具

| 工具 | 用途 |
| --- | --- |
| `ranran_chat` | 和然然聊一句，返回回复（带来源/文件路径），按 `session_id` 记上下文 |
| `ranran_session` | 查看/切换会话的模型与外部人设开关 |
| `web_search` | 只做联网检索，不带人格 |
| `write_file` | 生成 md/txt/csv 并返回落盘路径 |
| `list_skills` / `use_skill` | 列出技能说明 / 取某个技能正文 |
| `search_history` | 回查某个会话之前聊过什么 |

## 自检

```bash
python integrations/mcp/client_example.py --chat "你好"
```

详细契约、会话设计、错误对照表见 [SKILL.md](SKILL.md)。
