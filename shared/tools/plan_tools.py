"""
shared/tools/plan_tools.py — Planning tools for the Expert Manager.

Tools:
  submit_plan       — LLM calls this to submit a fully structured project plan (forces structured output)
  scan_project      — Ingests a project from a local path, GitHub URL, or production URL
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx

from shared.core.tool_registry import ToolRegistry, make_schema

log = logging.getLogger("plan_tools")

# ─── submit_plan ──────────────────────────────────────────────────────────────

_SUBMIT_PLAN_SCHEMA = make_schema(
    name="submit_plan",
    description=(
        "Submit a fully structured project plan. You MUST call this tool with a complete plan "
        "covering all phases, tasks, timelines, risks, and deliverables. "
        "Do not summarise — fill every field."
    ),
    properties={
        "project_name": {
            "type": "string",
            "description": "Short, descriptive project name (e.g. 'User Auth Revamp')"
        },
        "goal_summary": {
            "type": "string",
            "description": "1-3 sentence executive summary of what this project achieves"
        },
        "total_timeline_days": {
            "type": "integer",
            "description": "Total calendar days from kickoff to production-ready"
        },
        "phases": {
            "type": "array",
            "description": "Ordered delivery phases (Discovery → Design → Dev → QA → DevOps → Launch)",
            "items": {
                "type": "object",
                "properties": {
                    "name":          {"type": "string"},
                    "order":         {"type": "integer"},
                    "duration_days": {"type": "integer"},
                    "start_day":     {"type": "integer"},
                    "end_day":       {"type": "integer"},
                    "objective":     {"type": "string"},
                    "assigned_roles": {
                        "type": "array",
                        "items": {"type": "string",
                                  "enum": ["developer","devops","qa","support","docs",
                                           "design","ux","ui_test","api_test","qa_auto","security"]}
                    },
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title":               {"type": "string"},
                                "description":         {"type": "string"},
                                "acceptance_criteria": {"type": "string"},
                                "assigned_to":         {"type": "string"},
                                "priority":            {"type": "string", "enum": ["P1","P2","P3","P4"]},
                                "ticket_type":         {"type": "string", "enum": ["Task","Bug","Story","Epic","Spike"]},
                                "estimated_days":      {"type": "number"},
                                "depends_on":          {"type": "string", "description": "Title of blocking task (if any)"}
                            },
                            "required": ["title","description","acceptance_criteria","assigned_to","priority","estimated_days"]
                        }
                    }
                },
                "required": ["name","order","duration_days","start_day","end_day","objective","tasks"]
            }
        },
        "deliverables": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tangible outputs: 'Deployed API', 'Test report', 'Runbook', etc."
        },
        "testing_scope": {
            "type": "object",
            "description": "What testing will be done and by whom",
            "properties": {
                "unit_tests":        {"type": "string"},
                "integration_tests": {"type": "string"},
                "e2e_tests":         {"type": "string"},
                "security_scan":     {"type": "string"},
                "performance_test":  {"type": "string"},
                "uat":               {"type": "string"}
            }
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "risk":        {"type": "string"},
                    "probability": {"type": "string", "enum": ["Low","Medium","High"]},
                    "impact":      {"type": "string", "enum": ["Low","Medium","High"]},
                    "mitigation":  {"type": "string"}
                },
                "required": ["risk","probability","impact","mitigation"]
            }
        },
        "definition_of_done": {
            "type": "string",
            "description": "The exact criteria that must all be true before this project is 'done'"
        },
        "tech_stack_notes": {
            "type": "string",
            "description": "Any stack-specific constraints, upgrade needs, or architectural decisions"
        }
    },
    required=["project_name","goal_summary","total_timeline_days","phases",
               "deliverables","testing_scope","risks","definition_of_done"]
)


async def _submit_plan_executor(args: dict) -> str:
    """Stores the plan temporarily in a module-level dict keyed by a sentinel."""
    _pending_plans["latest"] = args
    task_count = sum(len(p.get("tasks", [])) for p in args.get("phases", []))
    phase_count = len(args.get("phases", []))
    return (
        f"Plan '{args.get('project_name')}' accepted. "
        f"{phase_count} phases, {task_count} tasks, "
        f"{args.get('total_timeline_days')} days total."
    )


# Module-level store so agent.py can read the plan after tool call
_pending_plans: dict = {}


def get_pending_plan() -> Optional[dict]:
    return _pending_plans.pop("latest", None)


# ─── scan_project ─────────────────────────────────────────────────────────────

_SCAN_PROJECT_SCHEMA = make_schema(
    name="scan_project",
    description=(
        "Scan and analyse a project to understand its tech stack, architecture, and current state. "
        "Accepts a local folder path (e.g. /app/sandbox/myproject), "
        "a GitHub URL (https://github.com/owner/repo), "
        "or a production URL (https://myapp.com). "
        "Returns a structured summary you should use to create your plan."
    ),
    properties={
        "source": {
            "type": "string",
            "description": "Local path, GitHub repo URL, or production URL"
        },
        "focus": {
            "type": "string",
            "description": "Optional focus area: 'security', 'performance', 'full' (default: full)",
            "enum": ["full", "security", "performance", "architecture"]
        }
    },
    required=["source"]
)


async def _scan_project_executor(args: dict) -> str:
    source: str = args.get("source", "").strip()
    focus: str  = args.get("focus", "full")

    if source.startswith("https://github.com") or source.startswith("http://github.com"):
        return await _scan_github(source, focus)
    elif source.startswith("http://") or source.startswith("https://"):
        return await _scan_production_url(source, focus)
    else:
        return await _scan_local_folder(source, focus)


# ── Local folder scanner ──────────────────────────────────────────────────────

_KEY_FILES = [
    "README.md", "README.rst", "README.txt",
    "package.json", "package-lock.json",
    "requirements.txt", "pyproject.toml", "setup.py", "Pipfile",
    "go.mod", "pom.xml", "build.gradle", "Cargo.toml",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".github/workflows",
    "Makefile",
    "openapi.yaml", "openapi.json", "swagger.yaml",
    ".env.example", ".env.sample",
]

_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb",
              ".rs", ".cs", ".cpp", ".c", ".php", ".swift", ".kt"}


async def _scan_local_folder(path: str, focus: str) -> str:
    base = Path(path)
    if not base.exists():
        return f"ERROR: Path '{path}' does not exist inside the container."

    result = {"source_type": "local", "path": path, "found": {}}

    # Read key files
    for fname in _KEY_FILES:
        fpath = base / fname
        if fpath.is_file():
            try:
                content = fpath.read_text(errors="replace")[:3000]
                result["found"][fname] = content
            except Exception:
                pass

    # File tree (2 levels deep)
    tree_lines = []
    try:
        for item in sorted(base.rglob("*")):
            rel = item.relative_to(base)
            depth = len(rel.parts)
            if depth > 3:
                continue
            if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")
                   for p in rel.parts):
                continue
            prefix = "  " * (depth - 1)
            tree_lines.append(f"{prefix}{'📁' if item.is_dir() else '📄'} {rel.parts[-1]}")
    except Exception as e:
        tree_lines = [f"(tree error: {e})"]

    result["file_tree"] = "\n".join(tree_lines[:150])

    # Count code files by language
    lang_count: dict[str, int] = {}
    try:
        for f in base.rglob("*"):
            if f.suffix in _CODE_EXTS:
                lang_count[f.suffix] = lang_count.get(f.suffix, 0) + 1
    except Exception:
        pass
    result["language_breakdown"] = lang_count

    return _format_scan_result(result)


# ── GitHub scanner ────────────────────────────────────────────────────────────

async def _scan_github(url: str, focus: str) -> str:
    # Parse owner/repo from URL
    m = re.search(r"github\.com/([^/]+)/([^/\s#?]+)", url)
    if not m:
        return f"ERROR: Could not parse GitHub URL: {url}"
    owner, repo = m.group(1), m.group(2).rstrip(".git")

    api = "https://api.github.com"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "InvextAI/1.0"}

    result = {"source_type": "github", "url": url, "owner": owner, "repo": repo, "found": {}}

    async with httpx.AsyncClient(timeout=30) as c:
        # Repo metadata
        try:
            r = await c.get(f"{api}/repos/{owner}/{repo}", headers=headers)
            if r.is_success:
                data = r.json()
                result["repo_info"] = {
                    "description":  data.get("description", ""),
                    "language":     data.get("language", ""),
                    "stars":        data.get("stargazers_count", 0),
                    "open_issues":  data.get("open_issues_count", 0),
                    "default_branch": data.get("default_branch", "main"),
                    "topics":       data.get("topics", []),
                    "license":      (data.get("license") or {}).get("name", ""),
                    "created_at":   data.get("created_at", ""),
                    "updated_at":   data.get("updated_at", ""),
                }
        except Exception as e:
            result["repo_info"] = {"error": str(e)}

        # README
        for readme in ("README.md", "readme.md", "README.rst"):
            try:
                r = await c.get(
                    f"{api}/repos/{owner}/{repo}/contents/{readme}", headers=headers
                )
                if r.is_success:
                    import base64
                    content = base64.b64decode(r.json()["content"]).decode(errors="replace")[:3000]
                    result["found"]["README"] = content
                    break
            except Exception:
                pass

        # Key config files
        for fname in ["package.json", "requirements.txt", "pyproject.toml", "go.mod",
                      "Dockerfile", "docker-compose.yml"]:
            try:
                r = await c.get(
                    f"{api}/repos/{owner}/{repo}/contents/{fname}", headers=headers
                )
                if r.is_success:
                    import base64
                    content = base64.b64decode(r.json()["content"]).decode(errors="replace")[:1500]
                    result["found"][fname] = content
            except Exception:
                pass

        # Directory tree (root level)
        try:
            r = await c.get(
                f"{api}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1", headers=headers
            )
            if r.is_success:
                tree = r.json().get("tree", [])
                paths = [t["path"] for t in tree if t["type"] == "blob"]
                # Show first 100 paths
                result["file_tree"] = "\n".join(paths[:100])
                # Language breakdown by extension
                lang_count: dict[str, int] = {}
                for p in paths:
                    ext = os.path.splitext(p)[1]
                    if ext in _CODE_EXTS:
                        lang_count[ext] = lang_count.get(ext, 0) + 1
                result["language_breakdown"] = lang_count
        except Exception:
            pass

        # Recent commits
        try:
            r = await c.get(
                f"{api}/repos/{owner}/{repo}/commits?per_page=5", headers=headers
            )
            if r.is_success:
                commits = r.json()
                result["recent_commits"] = [
                    {"sha": c_["sha"][:7],
                     "message": c_["commit"]["message"].split("\n")[0],
                     "author": c_["commit"]["author"]["name"],
                     "date": c_["commit"]["author"]["date"]}
                    for c_ in commits
                ]
        except Exception:
            pass

        # Open issues (first 5)
        try:
            r = await c.get(
                f"{api}/repos/{owner}/{repo}/issues?state=open&per_page=5", headers=headers
            )
            if r.is_success:
                result["open_issues"] = [
                    {"number": i["number"], "title": i["title"],
                     "labels": [l["name"] for l in i.get("labels", [])]}
                    for i in r.json()
                ]
        except Exception:
            pass

    return _format_scan_result(result)


# ── Production URL scanner ────────────────────────────────────────────────────

async def _scan_production_url(url: str, focus: str) -> str:
    result = {"source_type": "production_url", "url": url, "found": {}}

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        # Homepage
        try:
            r = await c.get(url)
            result["http_status"] = r.status_code
            result["response_headers"] = dict(r.headers)

            # Detect stack from headers
            server      = r.headers.get("server", "")
            powered_by  = r.headers.get("x-powered-by", "")
            content_type = r.headers.get("content-type", "")
            result["stack_hints"] = {
                "server":     server,
                "powered_by": powered_by,
                "content_type": content_type,
            }

            # Sniff HTML for framework hints
            html = r.text[:5000]
            hints = []
            for pattern, tech in [
                (r'react', "React"),
                (r'__next', "Next.js"),
                (r'nuxt', "Nuxt.js"),
                (r'ng-version|angular', "Angular"),
                (r'vue\.', "Vue.js"),
                (r'svelte', "Svelte"),
                (r'<meta.*django', "Django"),
                (r'laravel', "Laravel"),
                (r'rails', "Ruby on Rails"),
            ]:
                if re.search(pattern, html, re.IGNORECASE):
                    hints.append(tech)
            result["detected_frameworks"] = hints

            # Meta description / title
            title_m = re.search(r"<title[^>]*>([^<]{1,200})</title>", html, re.IGNORECASE)
            if title_m:
                result["page_title"] = title_m.group(1).strip()
            desc_m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,300})', html, re.IGNORECASE)
            if desc_m:
                result["meta_description"] = desc_m.group(1).strip()

        except Exception as e:
            result["error"] = str(e)

        # robots.txt
        try:
            robots_url = url.rstrip("/") + "/robots.txt"
            r = await c.get(robots_url)
            if r.is_success:
                result["found"]["robots.txt"] = r.text[:500]
        except Exception:
            pass

        # sitemap.xml (first 1000 chars to find sections)
        try:
            sitemap_url = url.rstrip("/") + "/sitemap.xml"
            r = await c.get(sitemap_url)
            if r.is_success:
                result["found"]["sitemap.xml"] = r.text[:1000]
        except Exception:
            pass

        # Common API discovery endpoints
        for api_path in ["/api", "/api/v1", "/api/v2", "/swagger", "/openapi.json",
                          "/docs", "/redoc", "/health", "/version"]:
            try:
                r = await c.get(url.rstrip("/") + api_path, timeout=5)
                if r.status_code < 400:
                    ct = r.headers.get("content-type", "")
                    result.setdefault("api_endpoints_found", []).append({
                        "path": api_path,
                        "status": r.status_code,
                        "content_type": ct,
                        "preview": r.text[:200] if "json" in ct else ""
                    })
            except Exception:
                pass

    return _format_scan_result(result)


# ── Formatter ─────────────────────────────────────────────────────────────────

def _format_scan_result(data: dict) -> str:
    parts = [f"=== PROJECT SCAN: {data.get('source_type','?').upper()} ==="]

    if data.get("source_type") == "github":
        info = data.get("repo_info", {})
        parts.append(f"Repo: {data.get('owner')}/{data.get('repo')}")
        parts.append(f"Description: {info.get('description','')}")
        parts.append(f"Primary language: {info.get('language','unknown')}")
        parts.append(f"Topics: {', '.join(info.get('topics', []))}")
        parts.append(f"Open issues: {info.get('open_issues', 0)}")
        parts.append(f"License: {info.get('license','')}")
        if data.get("recent_commits"):
            parts.append("\nRecent commits:")
            for c in data["recent_commits"]:
                parts.append(f"  [{c['sha']}] {c['message']} — {c['author']}")
        if data.get("open_issues"):
            parts.append("\nOpen issues:")
            for i in data["open_issues"]:
                parts.append(f"  #{i['number']}: {i['title']} [{','.join(i['labels'])}]")

    elif data.get("source_type") == "production_url":
        parts.append(f"URL: {data.get('url')}")
        parts.append(f"HTTP status: {data.get('http_status','?')}")
        parts.append(f"Page title: {data.get('page_title','')}")
        parts.append(f"Meta description: {data.get('meta_description','')}")
        hints = data.get("stack_hints", {})
        parts.append(f"Server: {hints.get('server','?')} | Powered by: {hints.get('powered_by','?')}")
        frameworks = data.get("detected_frameworks", [])
        if frameworks:
            parts.append(f"Detected frameworks: {', '.join(frameworks)}")
        if data.get("api_endpoints_found"):
            parts.append("\nAPI/docs endpoints found:")
            for ep in data["api_endpoints_found"]:
                parts.append(f"  {ep['path']} → HTTP {ep['status']} ({ep['content_type']})")
                if ep.get("preview"):
                    parts.append(f"    Preview: {ep['preview'][:150]}")

    else:
        parts.append(f"Path: {data.get('path','?')}")
        lb = data.get("language_breakdown", {})
        if lb:
            parts.append("Language breakdown: " + ", ".join(f"{k}: {v}" for k, v in sorted(lb.items(), key=lambda x: -x[1])))

    # File tree
    if data.get("file_tree"):
        parts.append(f"\nFile tree:\n{data['file_tree'][:2000]}")

    # Key files found
    for fname, content in data.get("found", {}).items():
        parts.append(f"\n--- {fname} ---\n{content[:2000]}")

    return "\n".join(parts)


# ─── submit_work_plan (used by every agent before starting a task) ─────────────

_SUBMIT_WORK_PLAN_SCHEMA = make_schema(
    name="submit_work_plan",
    description=(
        "Submit your work plan for a task before you start executing it. "
        "Describe your approach, the tools you will use, your deliverables, "
        "your estimated timeline, and any blockers or questions for the manager. "
        "This plan will be reviewed. Only start working after it is approved."
    ),
    properties={
        "task_summary": {
            "type": "string",
            "description": "One sentence: what you are about to do"
        },
        "approach": {
            "type": "string",
            "description": "Step-by-step description of HOW you will tackle this task (be specific)"
        },
        "tools_needed": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of tools you plan to use (e.g. code_run, web_search, create_task)"
        },
        "deliverables": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete outputs you will produce (e.g. 'working API endpoint', 'test report')"
        },
        "estimated_hours": {
            "type": "number",
            "description": "Realistic time estimate in hours"
        },
        "risks_or_blockers": {
            "type": "string",
            "description": "Any known risks, unknowns, or questions for the manager. Write 'None' if clear."
        },
        "questions_for_manager": {
            "type": "string",
            "description": (
                "Specific questions you need answered before starting, or resources you need. "
                "Write 'None' if you have everything you need."
            )
        }
    },
    required=["task_summary", "approach", "deliverables", "estimated_hours",
               "risks_or_blockers", "questions_for_manager"]
)

# Module-level store for pending work plans
_pending_work_plans: dict = {}


async def _submit_work_plan_executor(args: dict) -> str:
    _pending_work_plans["latest"] = args
    return (
        f"Work plan submitted: '{args.get('task_summary')}'. "
        f"Estimated {args.get('estimated_hours')}h. "
        f"Awaiting manager review."
    )


def get_pending_work_plan() -> Optional[dict]:
    return _pending_work_plans.pop("latest", None)


# ─── Registration ─────────────────────────────────────────────────────────────

def register():
    ToolRegistry.register("submit_plan",      _SUBMIT_PLAN_SCHEMA,       _submit_plan_executor)
    ToolRegistry.register("scan_project",     _SCAN_PROJECT_SCHEMA,      _scan_project_executor)
    ToolRegistry.register("submit_work_plan", _SUBMIT_WORK_PLAN_SCHEMA,  _submit_work_plan_executor)
