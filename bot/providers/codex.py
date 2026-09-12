from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


class CodexError(RuntimeError):
    pass


class CodexProvider:
    def __init__(self, timeout: int, workdir: Path, secrets: tuple[str, ...] = ()) -> None:
        self.timeout = timeout
        self.workdir = workdir
        self.secrets = secrets
        self._cmd: list[str] | None = None
        self._probed = False

    async def ask(
        self, prompt: str, *, system: str | None = None, model: str | None = None
    ) -> str:
        await self.probe()
        assert self._cmd is not None
        if system:
            prompt = f"{system}\n\n---\n\n{prompt}"
        self.workdir.mkdir(parents=True, exist_ok=True)
        out_path = self.workdir / f"last-{uuid4().hex}.txt"
        args = _exec_args(self._cmd, self.workdir, out_path, model)
        try:
            stdout, stderr, code = await self._run(args, timeout=self.timeout, stdin=prompt)
        except asyncio.TimeoutError as exc:
            raise CodexError(
                f"Codex 执行超时（{self.timeout} 秒）。请缩短问题，或调大 CODEX_TIMEOUT_SECONDS。"
            ) from exc
        finally:
            answer = _read_optional(out_path)
            _remove_quiet(out_path)

        if answer.strip():
            return answer.strip()
        logger.warning(
            "Codex exec failed code=%s stderr=%s stdout=%s",
            code,
            _clip(stderr),
            _clip(stdout),
        )
        raise CodexError(_friendly_codex_error(stdout, stderr, code))

    async def probe(self) -> None:
        if self._probed:
            return
        cmd = _find_codex_command()
        try:
            help_out, help_err, help_code = await self._run(
                [*cmd, "--help"], timeout=30
            )
        except FileNotFoundError as exc:
            raise CodexError(
                "未找到 Codex CLI。请先安装并确保终端里执行 `codex --help` 可用。"
            ) from exc
        except asyncio.TimeoutError as exc:
            raise CodexError("探测 Codex CLI 超时，请检查本机 Codex 是否正常。") from exc
        if help_code != 0 and "Usage:" not in help_out and "Usage:" not in help_err:
            raise CodexError("Codex CLI 无法运行。请在终端执行 `codex --help` 检查安装。")

        try:
            exec_out, exec_err, _ = await self._run([*cmd, "exec", "--help"], timeout=30)
        except asyncio.TimeoutError as exc:
            raise CodexError("探测 `codex exec` 超时，请检查本机 Codex 是否正常。") from exc
        exec_text = f"{exec_out}\n{exec_err}"
        if "Usage:" not in exec_text and "exec" not in exec_text.lower():
            raise CodexError("当前 Codex CLI 不支持 `codex exec`，请升级后再试。")

        self._cmd = cmd
        self._probed = True
        logger.info("Codex CLI ready: %s", " ".join(cmd))

    async def _run(
        self,
        args: list[str],
        timeout: int,
        stdin: str | None = None,
    ) -> tuple[str, str, int]:
        kwargs: dict = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(self.workdir if self.workdir.exists() else Path.cwd()),
            "env": _subprocess_env(self.secrets),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)
        try:
            raw_in = stdin.encode("utf-8") if stdin is not None else None
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=raw_in),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await _kill_process(proc)
            raise
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        return stdout, stderr, proc.returncode or 0


def _exec_args(cmd: list[str], workdir: Path, out_path: Path, model: str | None) -> list[str]:
    args = [
        *cmd,
        "exec",
    ]
    if model:
        args += ["-m", model]
    args += [
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-s",
        "read-only",
        "-c",
        "approval_policy=never",
        # config.toml may set reasoning to "none"; gpt-6-astra rejects that.
        "-c",
        'model_reasoning_effort="low"',
        "-C",
        str(workdir),
        "-o",
        str(out_path),
        "-",
    ]
    return args


def _friendly_codex_error(stdout: str, stderr: str, code: int) -> str:
    text = f"{stderr}\n{stdout}"
    lowered = text.lower()
    if "unauthorized" in lowered or "invalid api key" in lowered or "authentication" in lowered:
        return "Codex 鉴权失败，请检查本机 Codex 登录或 tokenx24 密钥。"
    if "timed out waiting for the token" in lowered or "auth command" in lowered and "timeout" in lowered:
        return "Codex 获取密钥超时，请稍后重试。"
    if "unknown model" in lowered or "unsupported model" in lowered or "model not found" in lowered:
        return "Codex 模型不可用，请检查本机 Codex 的模型配置。"
    if "reasoning" in lowered and ("invalid" in lowered or "unsupported" in lowered):
        return "Codex 推理参数不被当前模型支持，请稍后重试。"
    detail = _first_useful_line(text)
    if code != 0 and detail:
        return f"Codex 执行失败。{detail}"
    if detail:
        return f"Codex 没有返回内容。{detail}"
    if code != 0:
        return "Codex 执行失败。请确认本机已登录，或在终端检查 `codex` 是否正常。"
    return "Codex 没有返回内容，请换个问法再试。"


def _first_useful_line(text: str) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith(("usage:", "error:")):
            line = line.split(":", 1)[-1].strip() or line
        if len(line) < 8:
            continue
        return line[:180]
    return ""


def _clip(text: str, limit: int = 400) -> str:
    return " ".join((text or "").split())[:limit]


def _find_codex_command() -> list[str]:
    if os.name == "nt":
        cmd_path = shutil.which("codex.cmd")
        if cmd_path:
            js_path = (
                Path(cmd_path).parent
                / "node_modules"
                / "@openai"
                / "codex"
                / "bin"
                / "codex.js"
            )
            node = shutil.which("node")
            if js_path.is_file() and node:
                return [node, str(js_path)]
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", cmd_path]
        exe = shutil.which("codex.exe")
        if exe:
            return [exe]
        found = shutil.which("codex")
        if found:
            return [found]
    else:
        found = shutil.which("codex")
        if found:
            return [found]
    raise CodexError(
        "未找到 Codex CLI。请先安装并登录（终端执行 `codex --help` 应能成功）。"
    )


def _subprocess_env(secrets: tuple[str, ...]) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "DEEPSEEK_API_KEY",
        "TELEGRAM_OWNER_ID",
    ):
        env.pop(key, None)
    env["NO_COLOR"] = "1"
    # Avoid leaking bot secrets if a caller exported them under other names.
    drop_values = {s for s in secrets if s}
    if drop_values:
        for key, value in list(env.items()):
            if value in drop_values:
                env.pop(key, None)
    return env


def _read_optional(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _remove_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove temp file %s", path)


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.communicate(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
