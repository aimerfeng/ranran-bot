"""按 manifest.json 拉取 /yun 每日签需要的第三方素材（签底图片与字体）。

本仓库不包含这些素材（权利归各自权利人，见 THIRD_PARTY_NOTICES.md）。
manifest.json 记录了上游仓库、固定提交与每个文件的 SHA-256，因此可以精确复现。

用法：
    python scripts/fetch_fortune_assets.py                # 只补缺失或损坏的文件
    python scripts/fetch_fortune_assets.py --force        # 全部重新下载
    python scripts/fetch_fortune_assets.py --limit 3      # 只处理前 3 个（自检用）
    python scripts/fetch_fortune_assets.py --list         # 只看清单不下载

如果 raw.githubusercontent.com 不可达，脚本会自动改走 GitHub API；
把 token 放进环境变量可以避免 API 限流：
    GITHUB_TOKEN=ghp_xxx python scripts/fetch_fortune_assets.py
    （已登录 gh CLI 时也可用：$env:GITHUB_TOKEN = gh auth token）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = ROOT / "bot" / "assets" / "fortune"
MANIFEST = DEFAULT_TARGET / "manifest.json"
# 上游仓库里素材所在的目录前缀（manifest 里的 path 是相对这个前缀的）
UPSTREAM_PREFIX = "nonebot_plugin_fortune/resource"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
API = "https://api.github.com/repos/{repo}/contents/{path}"
TIMEOUT = httpx.Timeout(connect=20.0, read=180.0, write=30.0, pool=20.0)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_ok(path: Path, expected_sha: str, expected_bytes: int) -> bool:
    if not path.is_file():
        return False
    if expected_bytes and path.stat().st_size != expected_bytes:
        return False
    return sha256_of(path) == expected_sha


def github_token() -> str:
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if token:
        return token
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in {"GITHUB_TOKEN", "GH_TOKEN"} and value.strip():
                return value.strip()
    return ""


def download(client: httpx.Client, repo: str, commit: str, rel: str, token: str):
    """先试 raw，再试 GitHub API（raw 被墙时用）。返回 (bytes, 说明) 或 (None, 失败原因)。"""
    errors: list[str] = []
    for candidate in (f"{UPSTREAM_PREFIX}/{rel}", f"resource/{rel}", rel):
        url = RAW.format(repo=repo, commit=commit, path=candidate)
        try:
            response = client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            errors.append(f"raw {candidate}: {type(exc).__name__}")
            continue
        if response.status_code == 200 and response.content:
            return response.content, f"raw:{candidate}"
        errors.append(f"raw {candidate}: HTTP {response.status_code}")

    headers = {"Accept": "application/vnd.github.raw"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for candidate in (f"{UPSTREAM_PREFIX}/{rel}", rel):
        url = API.format(repo=repo, path=candidate)
        try:
            response = client.get(url, params={"ref": commit}, headers=headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            errors.append(f"api {candidate}: {type(exc).__name__}")
            continue
        if response.status_code == 200 and response.content:
            return response.content, f"api:{candidate}"
        errors.append(f"api {candidate}: HTTP {response.status_code}")
    return None, "; ".join(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取 /yun 每日签的第三方素材")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="素材落地目录")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件")
    parser.add_argument("--list", action="store_true", help="只打印清单")
    args = parser.parse_args()

    manifest_path = MANIFEST if args.target == DEFAULT_TARGET else args.target / "manifest.json"
    if not manifest_path.is_file():
        print(f"找不到 manifest：{manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repo = manifest["repository"]
    commit = manifest["commit"]
    files = list(manifest.get("files") or [])
    if args.limit:
        files = files[: args.limit]

    total_bytes = sum(int(f.get("bytes") or 0) for f in files)
    print(f"上游 {repo} @ {commit[:10]}，共 {len(files)} 个文件 / {total_bytes / 1048576:.1f} MB")
    print(f"落地目录 {args.target}")
    if args.list:
        for item in files[:20]:
            print("  ", item["path"], item.get("bytes"), "bytes")
        if len(files) > 20:
            print(f"   ... 其余 {len(files) - 20} 个")
        return 0

    token = github_token()
    if not token:
        print("提示：未检测到 GITHUB_TOKEN，raw 不可达时可能被 API 限流。")

    done = skipped = failed = 0
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "ranran-bot-assets"}) as client:
        for index, item in enumerate(files, 1):
            rel = item["path"]
            sha = item["sha256"]
            size = int(item.get("bytes") or 0)
            path = args.target / rel
            if not args.force and is_ok(path, sha, size):
                skipped += 1
                continue
            data, note = download(client, repo, commit, rel, token)
            if data is None:
                failed += 1
                print(f"[{index}/{len(files)}] 失败 {rel} :: {note}")
                continue
            actual = hashlib.sha256(data).hexdigest()
            if actual != sha:
                failed += 1
                print(f"[{index}/{len(files)}] 校验不符 {rel}（期望 {sha[:12]}，实得 {actual[:12]}）")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            done += 1
            if index % 25 == 0 or index == len(files):
                print(f"[{index}/{len(files)}] 已下载 {done}，跳过 {skipped}，失败 {failed}（{note}）")

    print(f"完成：下载 {done}，跳过 {skipped}，失败 {failed}")
    if failed:
        print("有文件没拿到；确认网络或设置 GITHUB_TOKEN 后重跑（可加 --force）。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
