# 然然 Skill 包使用说明

本目录是「然然」Telegram 机器人的可嵌套 skill 库，以 Markdown 文件形式提供提示词片段。模型会根据场景自动或主动加载相关 skill，用来改善真人感与上下文管理。

## 目录结构

```
skills/
├── README.md
├── conversation/
│   ├── human-voice.md          # 根节点：真人感核心
│   ├── anti-assistant-speak.md # 助手腔黑名单与改写
│   └── emotional-attunement.md # 情绪支持
├── context/
│   ├── context-management.md   # 上下文与指代管理
│   └── group-etiquette.md      # 群聊礼仪
└── output/
    ├── file-delivery.md        # 文件交付规范
    └── memory-callbacks.md     # 旧细节引用
```

## 嵌套（include）机制

- 文件头部 `includes` 列表写的是**相对 skills 根目录的路径、不带 .md**。
- 被 include 的 skill 正文会**先展开，再拼接**到当前 skill 正文里。
- 例如 `conversation/human-voice.md` include 了 `conversation/anti-assistant-speak` 和 `conversation/emotional-attunement`，加载 human-voice 时会连带展开这两个子 skill。
- 约束：
  - 不要出现循环 include（A include B，B 又 include A）。
  - 嵌套深度不要超过 3 层。
  - 子 skill 通常不写自己的 `when`（它们靠父节点带出），避免重复注入。

## when 字段怎么用

- `when` 的值是用 `|` 分隔的关键词，命中聊天内容时自动激活该 skill。
- 只有**确实高频需要**的 skill 才写 `when`；低频的留空，靠模型读 `description` 主动加载。
- 例子：`when: "难过|伤心|委屈|emo"` 表示对方消息里出现这些词就自动加载情绪支持。
- `always: false` 是默认；如需无条件注入可改为 `true`（本包目前没有 always 的 skill）。

## 如何增删

- 新增 skill：在对应子目录新建 `xxx.md`，写好 frontmatter（name / description / when / always / includes）和正文。
- 删除 skill：直接删文件；若它被某文件 include，记得同步去掉那处 include。
- 改归属：把文件移到别的子目录即可，子目录只是分组，不影响加载，但 include 路径要跟着改。
- 命名统一用英文 kebab-case（小写 + 连字符）。

## 正文写作要求

- 600~1800 字，写成可执行规则（做什么 / 不做什么 + 正反例）。
- 用简体中文，不写空话，不复述背景。
- 反例要具体到句子，正例要能直接照着说。