"""
shared/tools/workspace_tools.py — File read/write/bash/git tools for agents
working on the mounted client workspace (GIFTX-TRADEPILOT or any mounted repo).

The workspace is mounted at /workspace inside every agent container.
Agents can read, write, run bash commands, and commit/push changes.
"""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
from shared.core.tool_registry import ToolRegistry, make_schema

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))


# ─── helpers ──────────────────────────────────────────────────────────────────

def _safe_path(rel: str) -> Path:
    """Resolve a relative path inside the workspace; block path traversal."""
    p = (WORKSPACE / rel).resolve()
    if not str(p).startswith(str(WORKSPACE.resolve())):
        raise PermissionError(f"Path outside workspace: {rel}")
    return p


async def _run_cmd(cmd: str, cwd: str | None = None, timeout: int = 60) -> str:
    """Run a shell command, return combined stdout+stderr."""
    work_dir = str(WORKSPACE / cwd) if cwd else str(WORKSPACE)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        parts = []
        if out.strip():
            parts.append(out.decode(errors="replace").strip())
        if err.strip():
            parts.append("STDERR: " + err.decode(errors="replace").strip())
        if proc.returncode not in (0, None):
            parts.append(f"Exit code: {proc.returncode}")
        return "\n".join(parts) or "(no output)"
    except asyncio.TimeoutError:
        return f"[bash] Timeout after {timeout}s"
    except Exception as exc:
        return f"[bash] Error: {exc}"


# ─── workspace_read ────────────────────────────────────────────────────────────

_READ_SCHEMA = make_schema(
    "workspace_read",
    "Read any file in the client workspace (GIFTX-TRADEPILOT). "
    "Path is relative to workspace root.",
    {"path": {"type": "string", "description": "Relative file path, e.g. services/invext-ai-service/src/main.py"}},
)

async def _workspace_read(args: dict) -> str:
    try:
        p = _safe_path(args["path"])
        if not p.exists():
            return f"File not found: {args['path']}"
        if p.stat().st_size > 200_000:
            return f"File too large ({p.stat().st_size} bytes) — use workspace_bash to grep/head it"
        return p.read_text(errors="replace")
    except Exception as exc:
        return f"[workspace_read] {exc}"


# ─── workspace_write ───────────────────────────────────────────────────────────

_WRITE_SCHEMA = make_schema(
    "workspace_write",
    "Write (or overwrite) a file in the client workspace. "
    "Creates parent directories as needed.",
    {
        "path":    {"type": "string",  "description": "Relative file path"},
        "content": {"type": "string",  "description": "Full new file content"},
    },
    required=["path", "content"],
)

async def _workspace_write(args: dict) -> str:
    try:
        p = _safe_path(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"])
        return f"Written {len(args['content'])} chars to {args['path']}"
    except Exception as exc:
        return f"[workspace_write] {exc}"


# ─── workspace_patch ───────────────────────────────────────────────────────────

_PATCH_SCHEMA = make_schema(
    "workspace_patch",
    "Replace an exact string in a workspace file (targeted edit, no full rewrite needed). "
    "Fails if old_text is not found exactly once.",
    {
        "path":     {"type": "string", "description": "Relative file path"},
        "old_text": {"type": "string", "description": "The exact text to replace"},
        "new_text": {"type": "string", "description": "Replacement text"},
    },
    required=["path", "old_text", "new_text"],
)

async def _workspace_patch(args: dict) -> str:
    try:
        p       = _safe_path(args["path"])
        content = p.read_text(errors="replace")
        old     = args["old_text"]
        count   = content.count(old)
        if count == 0:
            return f"[workspace_patch] old_text not found in {args['path']}"
        if count > 1:
            return f"[workspace_patch] old_text found {count} times — make it more specific"
        p.write_text(content.replace(old, args["new_text"], 1))
        return f"Patched {args['path']} — replaced 1 occurrence"
    except Exception as exc:
        return f"[workspace_patch] {exc}"


# ─── workspace_list ────────────────────────────────────────────────────────────

_LIST_SCHEMA = make_schema(
    "workspace_list",
    "List files in the workspace (or a subfolder). Returns relative paths.",
    {
        "path":    {"type": "string", "description": "Subfolder to list (default: root)"},
        "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py' (default: **)"},
        "max":     {"type": "integer", "description": "Max results (default 100)"},
    },
    required=[],
)

async def _workspace_list(args: dict) -> str:
    try:
        base    = _safe_path(args.get("path", "")) if args.get("path") else WORKSPACE
        pattern = args.get("pattern", "**/*")
        limit   = int(args.get("max", 100))
        files   = [str(f.relative_to(WORKSPACE)) for f in base.glob(pattern) if f.is_file()]
        files   = files[:limit]
        return "\n".join(files) if files else "(no files found)"
    except Exception as exc:
        return f"[workspace_list] {exc}"


# ─── workspace_bash ────────────────────────────────────────────────────────────

_BASH_SCHEMA = make_schema(
    "workspace_bash",
    "Run a bash command inside the workspace directory. "
    "Use for grep, git diff, pip install, tests, builds, etc. "
    "Working directory is the workspace root unless cwd is specified.",
    {
        "cmd":     {"type": "string",  "description": "Shell command to run"},
        "cwd":     {"type": "string",  "description": "Sub-directory to run in (optional)"},
        "timeout": {"type": "integer", "description": "Max seconds (default 60, max 300)"},
    },
    required=["cmd"],
)

async def _workspace_bash(args: dict) -> str:
    timeout = min(int(args.get("timeout", 60)), 300)
    return await _run_cmd(args["cmd"], cwd=args.get("cwd"), timeout=timeout)


# ─── workspace_git ────────────────────────────────────────────────────────────

_GIT_SCHEMA = make_schema(
    "workspace_git",
    "Run a git command in the workspace. "
    "Use for status, diff, add, commit, push, log, etc.",
    {
        "args":    {"type": "string",  "description": "Git sub-command and args, e.g. 'status' or 'commit -m \"fix: bug\"'"},
        "timeout": {"type": "integer", "description": "Max seconds (default 30)"},
    },
    required=["args"],
)

async def _workspace_git(args: dict) -> str:
    timeout = min(int(args.get("timeout", 30)), 120)
    return await _run_cmd(f"git {args['args']}", timeout=timeout)


# ─── registration ─────────────────────────────────────────────────────────────

def register():
    ToolRegistry.register("workspace_read",  _READ_SCHEMA,  _workspace_read)
    ToolRegistry.register("workspace_write", _WRITE_SCHEMA, _workspace_write)
    ToolRegistry.register("workspace_patch", _PATCH_SCHEMA, _workspace_patch)
    ToolRegistry.register("workspace_list",  _LIST_SCHEMA,  _workspace_list)
    ToolRegistry.register("workspace_bash",  _BASH_SCHEMA,  _workspace_bash)
    ToolRegistry.register("workspace_git",   _GIT_SCHEMA,   _workspace_git)
