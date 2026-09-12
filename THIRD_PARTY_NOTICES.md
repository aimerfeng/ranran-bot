# 第三方声明

本仓库的许可证（MIT）只覆盖本项目自己的代码。以下第三方内容各有其权利人，
**不在本项目的 MIT 许可范围内**。

## 1. nonebot_plugin_fortune（/yun 每日签功能的上游）

- 上游：https://github.com/MinatoAquaCrews/nonebot_plugin_fortune
- 固定提交：`136a6db97bd8c778811fdb2f60d07b6b95f3e70e`
- 用途：`bot/fortune.py`、`bot/fortune_poetry.py` 的抽签与签面排版逻辑衍生自该项目
  （`utils.py` 的 `drawing()`），排版位置与绘制流程做了 Pillow 新版适配。
- 上游为 MIT 许可，其完整许可文本见 `bot/assets/fortune/LICENSE`，也附在下方。

## 2. 签底图片与字体（**本仓库不包含**）

- `bot/assets/fortune/img/**`（12 套主题共 595 张）与 `bot/assets/fortune/font/**`
  （`sakura.ttf`、`Mamelon.otf`）的权利归各自权利人所有，上游仅标注了来源：
  原神 / PCR / Hololive 签图来源 opqqq-plugin，东方签底由江樂丝提供，
  其他主题来源 FloatTech/zbpdata 等。
- 为避免再分发这些素材，本仓库用 `.gitignore` 排除了它们。
  需要运行时请执行 `python scripts/fetch_fortune_assets.py`，脚本按
  `bot/assets/fortune/manifest.json` 记录的上游仓库、固定提交与 SHA-256 拉取并校验。
- 是否使用这些素材、以及使用方式，请自行确认符合各权利人的要求。

## 3. 签语文本

- `bot/assets/fortune/poetry.json` 为本项目原创拟古签语库（400 条），随本项目按 MIT 发布。
- `bot/assets/fortune/copywriting.json` 为上游旧白话文案库，仅保留兼容读取。

## 上游 MIT 许可全文

```text
MIT License

Copyright (c) 2022 KafCoppelia

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 其他依赖

运行依赖（python-telegram-bot、httpx、Pillow、PyYAML、python-dotenv）各自遵循其
自身许可；模型能力来自 DeepSeek API 与本机 Codex CLI，与本仓库许可无关。
