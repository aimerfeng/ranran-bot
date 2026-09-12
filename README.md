# ranran-bot · 然然

一个跑在 Telegram 群里的中文 AI 机器人，角色是虚构的二次元女生「然然」。

它不是一问一答的套壳：对话走自建的 **agent harness**——原生 function calling 的工具循环（联网搜索 / 生成文件 / 按需加载 skill / 回查聊天记录），外加一套可嵌套的 skill 包来管住说话方式与上下文。模型可接 DeepSeek API，也可以接本机 Codex CLI。

只允许白名单里的群使用；其它群除 `/chatid` 外一律静默忽略。

## 功能

- `/start` `/help`：说明用法
- `/chatid`：查看当前 `chat_id`（**未入白名单的群也能用**，方便把群加进白名单）
- `/codex <问题>`：走本机 `codex exec`
- `/deepseek <问题>`：走 DeepSeek Chat Completions
- `/search <问题>`：联网搜索最新信息，AI 总结并附来源链接（走 DeepSeek 原生 `web_search_20250305`，复用 `DEEPSEEK_API_KEY`，无需额外密钥；别名 `/s`）
- 不用打命令也能搜：直接 @它说「联网帮我搜一下 xxx」「帮我查一下明天天气」，会自己去搜再总结；问题依赖实时信息（新闻、价格、天气、赛况）时，模型也会自己发起搜索
- 多轮自动搜索：一轮搜到的信息不够时，模型可以再要一轮（最多 3 轮，结果累积回灌），最后附上去重后的全部来源；某轮失败不会丢掉已有材料
- 回复机器人消息：继续用该消息对应的模型
- 仅 owner 私聊：`/whitelist`、`/whitelist_add <chat_id>`、`/whitelist_remove <chat_id>`、`/persona`、`/skill`
- `/nsfw on|off`：按聊天开关「外部人设」注入（**默认关、仅 owner**）。人设正文由使用者在仓库外自行提供，仓库内不含此类内容
- `/skill`：查看 skill 列表；`/skill show <名字>` 看正文；改完文件 `/skill reload` 生效
- 生成文件：要表格/清单/长文档时，bot 会生成 md/txt/csv 并**作为文档发出来**（存在 `data/outbox/`）
- 长回复转文件：正文超过 `REPLY_FILE_THRESHOLD`（默认 **500 字**）时不刷屏，改成发一个 `.md` 文件，并留一句「这次说得有点长，我整理成 md 文件了」；设为 `0` 关闭
- 外部人设：`PERSONA_EXTRA_PATH` 指向的文件会**追加在系统提示词的最后一部分**（与酒馆的注入点一致）；改完文件在私聊发 `/persona reload` 即时生效，`/persona` 查看状态。留空则用 `data/persona_extra.txt`
- 输出纪律：默认在系统提示词最末尾追加一段「只输出正文」的约束，避免外部人设导致旁白/内心戏被当成正文发出；`PERSONA_OUTPUT_GUARD=off` 可关

访问规则：

- 私聊：只有 `TELEGRAM_OWNER_ID` 可用
- 群聊：必须在白名单（`/chatid` 除外）
- 白名单只认群 / 超级群 ID（负数，例如 `-100...`）

## 群聊新玩法

以下命令沿用原有访问规则：白名单群可用，私聊仅 owner 可用。普通聊天仍不自动发贴纸。

| 操作 | 效果 |
| --- | --- |
| 回复图片发送 `/sticker` | 返回真正的 Telegram 静态贴纸，保持比例与透明通道 |
| 回复图片发送 `/sticker 我真的谢` | 返回带白字黑描边的贴纸，自动换行，不裁掉原图 |
| 回复文字或图片说明发送 `/quote` | 原文金句卡片，保留署名，不用 AI 改写 |
| `/choose 火锅 烤肉 拉面` | 从不同选项中随机选一个 |
| `/choose 出去吃饭 \| 在家 做饭` | 用竖线分隔带空格的选项（发送时无需反斜杠） |

- `/sticker`（制作）与 `/stickers`（现有库存）是两个命令。
- 输入：普通照片、作为文件上传的 PNG/JPG/WebP、已有静态贴纸。支持 `/sticker@机器人用户名`。
- 输入最多 10 MB / 2000 万像素；文字最多 80 字。输出一边恰好 512 像素，另一边不超过 512，WebP 不超过 512 KB。
- 动态 GIF、视频、动画贴纸不会偷偷抽成第一帧；暂请使用静态素材。
- 中文字体：Windows 自动使用微软雅黑/黑体；Linux/macOS 安装中文字体，或设置 `BOT_CJK_FONT` 指向字体文件。
- `/quote` 最多 500 字，保留原文换行；不接受命令后附加改写内容。转发显示原始署名；卡片里的消息编号是当前聊天中的引用编号。
- `/choose` 接受 2—20 个不同选项，每项最多 100 字；重复选项会去重。
- 制图同时最多处理 2 个任务；同一用户在同一聊天内两次制图间隔至少 5 秒。
- 贴纸结果在内存中缓存最多 256 条，按群、素材、文字和渲染版本隔离；重启后可重新生成。
- 生成结果不会自动收入 bot 全局贴纸库存，也不会自动加入贴纸包。群贴纸包、抠图和动态贴纸属于后续功能。

验证：`python -m unittest discover -s tests -q`。

## 准备工作

### 1. 用 BotFather 创建 Bot

1. 在 Telegram 打开 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot`，按提示取名
3. 保存它发给你的 token，填进 `.env` 的 `TELEGRAM_BOT_TOKEN`

### 2. 必须关闭隐私模式

**必须**对这个 bot 执行：

1. 在 BotFather 发送 `/setprivacy`
2. 选中你的 bot
3. 选择 **Disable**

隐私模式开启时，群里普通消息和部分 @ 可能收不到。斜杠命令通常仍能收到，但回复机器人消息、以及后续扩展都会受影响，所以请关掉。

### 3. 把 Bot 拉进群

把 bot 拉进目标群。设为管理员更稳（不是硬性要求，但能减少收不到消息的情况）。

### 4. 拿到群 chat_id 并加入白名单

1. 用 [@userinfobot](https://t.me/userinfobot)（或其它能显示 ID 的 bot）拿到你自己的数字用户 ID，填到 `TELEGRAM_OWNER_ID`
2. 填好 `.env` 并启动 bot 后，在目标群发送 `/chatid`，记下返回的负数 ID
3. 用 owner 私聊 bot 发送：

```text
/whitelist_add -100xxxxxxxxxx
```

也可以把初始 ID 写进 `config.yaml` 的 `whitelist.group_ids`。运行后命令增删会写到 `data/whitelist.json`（优先于 yaml）。

### 5. 填写 `.env`

```powershell
copy .env.example .env
```

用编辑器填写：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_OWNER_ID`
- `DEEPSEEK_API_KEY`

可选：

- `DEEPSEEK_MODEL`（默认 `deepseek-v4-flash`）
- `CODEX_TIMEOUT_SECONDS`（默认 `180`）

不要把真实 token / key 提交到 git。

### 6. 安装依赖并启动

需要 Python 3.11+。在项目根目录：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

也可以：`python -m bot`。

要用 `/yun` 每日签，先拉一次素材（165 MB，可跳过）：

```powershell
python scripts\fetch_fortune_assets.py
```

### 7. 架构：agent harness

对话不是"一问一答"，而是一次带工具的 agent 回合：

```text
Telegram 消息
  → 上下文组装：角色设定 + 自动生效的 skill + 外部人设(仅 /nsfw on 的聊天) + 输出纪律
  → Agent 循环（DeepSeek 原生 function calling，最多 4 步）
       ├─ web_search            联网搜索；一轮不够会自己换词再搜（上限 3 次）
       ├─ write_file            生成 md/txt/csv，回复后作为文档发出
       ├─ use_skill             按需加载 skill 正文（渐进披露）
       └─ search_chat_history   回查最近几天的真实聊天记录，避免记错或编造
  → 正文 + 去重后的来源链接 + 文件产物
```

- `bot/harness/`：`agent.py`（循环）、`tools.py`（注册表）、`kit.py`（内置工具）、`skills.py`（嵌套 skill）
- 模型或端点不支持工具调用时，自动退回 `[搜索: …]` 文本协议，能力不丢
- `skills/`：Markdown + frontmatter，`includes` 支持嵌套；`always: true` 常驻，`when: 词|词` 命中自动生效，其余由模型 `use_skill` 按需加载；总注入有字符预算

### 8. Codex CLI

`/codex` 调用本机已安装的 Codex CLI（`codex exec`）。请先在终端确认：

```powershell
codex --help
codex exec --help
```

并完成本机登录。未安装、未登录、超时或空输出时，bot 会回友好中文错误，不会把 key 写进回复。

## 接入其他平台（MCP）

编排逻辑已经收敛到平台无关的 `bot/core/runtime.py`：Telegram 适配器与 MCP 适配器共用同一套人设、skill、工具循环与记忆。因此同一个「然然」可以同时挂在多个平台上。

```text
Telegram 适配器 (bot/main.py)  ┐
                              ├──→ bot/core/runtime.py ──→ 模型 / 工具 / skill / 记忆
MCP 适配器 (adapters/mcp)     ┘
```

启动 MCP server（默认 stdio）：

```powershell
pip install -r requirements-mcp.txt
python -m adapters.mcp.server          # stdio，给 Claude Desktop / Cursor 等用
python -m adapters.mcp.server --http   # streamable-http，给远程平台用
```

客户端配置与工具契约见 [integrations/mcp/README.md](integrations/mcp/README.md)；把 [integrations/mcp/SKILL.md](integrations/mcp/SKILL.md) 交给 AI，它能自己完成接入。

自检（会真实调用一次模型）：

```powershell
python integrations/mcp/client_example.py --chat "你好"
```

暴露的工具：`ranran_chat`（整轮对话，按 `session_id` 记上下文）、`ranran_session`、`web_search`、`write_file`、`list_skills`、`use_skill`、`search_history`。


## 群访问策略

只允许白名单里的群。未入白名单的群：

- `/chatid` 正常回复
- 其它命令和对话：**静默忽略**（不会提示“你不在白名单”，以免暴露 bot）

## 项目结构

```text
bot/main.py              入口与命令处理
bot/core/runtime.py      平台无关核心：会话 + 提示词组装 + agent 回合
bot/core/text.py         脱敏与分条切分（各适配器共用）
adapters/mcp/server.py   MCP 适配器：stdio / streamable-http 暴露能力
integrations/mcp/        接入其他平台的说明与可跑的客户端示例
bot/harness/agent.py     agent 循环（工具调用 → 回灌 → 再问）
bot/harness/tools.py     工具注册表与 JSON Schema 声明
bot/harness/kit.py       内置工具：联网搜索 / 写文件 / 加载 skill / 回查历史
bot/harness/skills.py    嵌套 skill 加载器（include 展开、关键词激活、预算）
bot/whitelist.py         群白名单
bot/settings.py          环境变量与 config.yaml
bot/files.py             生成 md/txt/csv 并发成 Telegram 文档
bot/websearch.py         DeepSeek 原生联网搜索（Anthropic 兼容 Messages API）
bot/persona.py           角色设定、外部人设加载、输出纪律
bot/providers/codex.py   本机 Codex CLI
bot/providers/deepseek.py   Chat Completions / 原生工具调用
skills/                  嵌套 skill 包（Markdown + frontmatter）
config.yaml              初始白名单
.env.example             环境变量模板
```

## 第三方素材

仓库**不包含** `/yun` 每日签需要的 595 张签底与 2 个字体（165 MB）：它们的权利归各自权利人，不在本项目的 MIT 许可范围内。

需要这个功能时执行一次：

```powershell
python scripts\fetch_fortune_assets.py
```

脚本按 `bot/assets/fortune/manifest.json` 记录的上游仓库、固定提交与 SHA-256 精确复现，已存在的文件会跳过。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 开源协议

本项目代码以 **MIT** 发布，见 [LICENSE](LICENSE)。

`/yun` 每日签的抽签与排版逻辑衍生自 [nonebot_plugin_fortune](https://github.com/MinatoAquaCrews/nonebot_plugin_fortune)（MIT，Copyright (c) 2022 KafCoppelia），其许可全文与素材归属说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 安全提醒

- `.env`、`data/`、`*.log`、`artifacts/` 已在 `.gitignore` 中排除。**运行日志会包含 bot token**（Telegram API 的 URL 里带 token），不要提交或外发。
- `data/` 里有真实聊天记录（`chat_memory.json`）、消息日志（`daily_messages.sqlite3`）与群白名单，同样不要外发。
- 如果 token 曾进过公开仓库或截图，去 @BotFather 用 `/revoke` 重新签发。
