"""
shared/tools/devops_tools.py — DevOps pipeline, deployment, and environment tools.

These tools let the DevOps agent track and manage deployments, environments,
and CI/CD pipelines directly from its ReAct loop.
"""
from shared.core.tool_registry import ToolRegistry, make_schema
from shared.core.database import (
    create_deployment, get_deployments, update_deployment,
    get_environments, update_environment,
    create_pipeline_run, get_pipeline_runs,
)

# ── Schemas ───────────────────────────────────────────────────────────────────

DEPLOY_S = make_schema(
    "create_deployment",
    "Record a new deployment for a service to an environment.",
    {
        "service_name": {"type": "string", "description": "Name of the service being deployed"},
        "version":      {"type": "string", "description": "Version tag / semver (e.g. v1.2.3)"},
        "environment":  {"type": "string", "enum": ["development", "staging", "production"]},
        "branch":       {"type": "string", "description": "Git branch (default: main)"},
        "commit_sha":   {"type": "string", "description": "Git commit SHA (short or full)"},
        "notes":        {"type": "string", "description": "Release notes or change summary"},
    },
    required=["service_name", "version"],
)

GET_DEPLOYS_S = make_schema(
    "get_deployments",
    "List recent deployments, optionally filtered by environment or status.",
    {
        "environment": {"type": "string", "enum": ["development", "staging", "production"]},
        "status":      {"type": "string", "enum": ["pending", "running", "success", "failed", "rolled_back"]},
        "limit":       {"type": "integer"},
    },
    required=[],
)

UPDATE_DEPLOY_S = make_schema(
    "update_deployment",
    "Update a deployment status (mark it running, success, failed, or rolled_back).",
    {
        "deploy_id": {"type": "string"},
        "status":    {"type": "string", "enum": ["running", "success", "failed", "rolled_back"]},
        "notes":     {"type": "string"},
    },
    required=["deploy_id", "status"],
)

ENVS_S = make_schema(
    "get_environments",
    "List all deployment environments and their current status.",
    {},
    required=[],
)

UPDATE_ENV_S = make_schema(
    "update_environment",
    "Update environment status (healthy/degraded/down/unknown) and URL.",
    {
        "name":        {"type": "string", "enum": ["development", "staging", "production"]},
        "status":      {"type": "string", "enum": ["healthy", "degraded", "down", "unknown", "deploying"]},
        "url":         {"type": "string"},
        "last_deploy": {"type": "string", "description": "Version tag of last successful deploy"},
    },
    required=["name", "status"],
)

PIPELINE_S = make_schema(
    "run_pipeline",
    "Record a CI/CD pipeline run with its steps.",
    {
        "pipeline":     {"type": "string", "description": "Pipeline name (e.g. build-test-deploy)"},
        "branch":       {"type": "string"},
        "triggered_by": {"type": "string"},
        "steps":        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string"},
                    "status": {"type": "string"},
                }
            },
            "description": "List of pipeline steps with name and status",
        },
    },
    required=["pipeline"],
)

GET_PIPELINES_S = make_schema(
    "get_pipeline_runs",
    "Get recent CI/CD pipeline run history.",
    {
        "pipeline": {"type": "string"},
        "limit":    {"type": "integer"},
    },
    required=[],
)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def _create_deploy(args: dict) -> str:
    d = create_deployment(
        service_name=args["service_name"],
        version=args["version"],
        environment=args.get("environment", "staging"),
        branch=args.get("branch", "main"),
        commit_sha=args.get("commit_sha", ""),
        notes=args.get("notes", ""),
        triggered_by="devops",
    )
    return (
        f"🚀 Deployment started: {d['service_name']} v{d['version']} "
        f"→ {d['environment']} [{d['id'][:8]}]"
    )


async def _get_deploys(args: dict) -> str:
    deploys = get_deployments(
        environment=args.get("environment"),
        status=args.get("status"),
        limit=int(args.get("limit", 10)),
    )
    if not deploys:
        return "No deployments found."
    lines = []
    for d in deploys:
        finished = str(d.get("finished_at", ""))[:16] or "in progress"
        lines.append(
            f"[{d['status'].upper()}] {d['service_name']} v{d['version']} "
            f"→ {d['environment']} | started {str(d['started_at'])[:16]} | done {finished}"
        )
    return "\n".join(lines)


async def _update_deploy(args: dict) -> str:
    update_deployment(args["deploy_id"], args["status"], args.get("notes", ""))
    emoji = {"success": "✅", "failed": "❌", "rolled_back": "⏪", "running": "🔄"}.get(args["status"], "🔄")
    return f"{emoji} Deployment {args['deploy_id'][:8]} → {args['status']}"


async def _get_envs(args: dict) -> str:
    envs = get_environments()
    if not envs:
        return "No environments configured."
    lines = []
    status_emoji = {"healthy": "🟢", "degraded": "🟡", "down": "🔴", "unknown": "⚪", "deploying": "🔵"}
    for e in envs:
        emoji = status_emoji.get(e["status"], "⚪")
        url = f" | {e['url']}" if e.get("url") else ""
        deploy = f" | last: {e['last_deploy']}" if e.get("last_deploy") else ""
        lines.append(f"{emoji} {e['name'].upper()}{url}{deploy} [{e['status']}]")
    return "\n".join(lines)


async def _update_env(args: dict) -> str:
    update_environment(
        name=args["name"],
        status=args["status"],
        url=args.get("url"),
        last_deploy=args.get("last_deploy"),
    )
    return f"✅ {args['name'].upper()} environment → {args['status']}"


async def _run_pipeline(args: dict) -> str:
    run = create_pipeline_run(
        pipeline=args["pipeline"],
        branch=args.get("branch", "main"),
        triggered_by=args.get("triggered_by", "devops"),
        steps=args.get("steps", []),
    )
    return (
        f"⚙️ Pipeline '{run['pipeline']}' started on {run['branch']} "
        f"[run {run['id'][:8]}]"
    )


async def _get_pipelines(args: dict) -> str:
    runs = get_pipeline_runs(
        pipeline=args.get("pipeline"),
        limit=int(args.get("limit", 10)),
    )
    if not runs:
        return "No pipeline runs found."
    lines = []
    status_emoji = {"running": "🔄", "success": "✅", "failed": "❌", "pending": "⏳"}
    for r in runs:
        emoji = status_emoji.get(r["status"], "⏳")
        lines.append(
            f"{emoji} {r['pipeline']} | {r['branch']} | "
            f"{str(r['started_at'])[:16]} | {r['status']}"
        )
    return "\n".join(lines)


# ── Registration ──────────────────────────────────────────────────────────────

def register():
    ToolRegistry.register("create_deployment",  DEPLOY_S,         _create_deploy)
    ToolRegistry.register("get_deployments",     GET_DEPLOYS_S,    _get_deploys)
    ToolRegistry.register("update_deployment",   UPDATE_DEPLOY_S,  _update_deploy)
    ToolRegistry.register("get_environments",    ENVS_S,           _get_envs)
    ToolRegistry.register("update_environment",  UPDATE_ENV_S,     _update_env)
    ToolRegistry.register("run_pipeline",        PIPELINE_S,       _run_pipeline)
    ToolRegistry.register("get_pipeline_runs",   GET_PIPELINES_S,  _get_pipelines)
