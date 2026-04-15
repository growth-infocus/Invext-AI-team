"""
shared/tools/ticket_tool.py — Custom Jira-like ticket management tool.

Agents use these tools to create rich tickets, add comments, link issues,
escalate priority, and manage sprints — all backed by Postgres.

MCP option: set JIRA_MCP_ENABLED=true in .env to route through Jira instead.
"""
from shared.core.tool_registry import ToolRegistry, make_schema
from shared.core.database import (
    create_task, get_task, get_tasks, update_task,
    add_comment, get_comments, link_tickets,
)
from shared.core.config import settings

# ── Schema definitions ────────────────────────────────────────────────────────

ALL_ROLES = [
    "developer", "devops", "qa", "support", "docs",
    "manager", "design", "ux", "ui_test", "api_test", "qa_auto", "security",
]

CREATE_TICKET_S = make_schema(
    "create_ticket",
    "Create a new ticket (Bug/Feature/Task/Support/Incident/Change). "
    "Product support tickets (source=product, ticket_type=Support) are auto-escalated to P1.",
    {
        "title":         {"type": "string", "description": "Short, actionable title"},
        "description":   {"type": "string", "description": "Full description with reproduction steps or requirements"},
        "assigned_to":   {"type": "string", "enum": ALL_ROLES},
        "ticket_type":   {"type": "string", "enum": ["Bug", "Feature", "Task", "Support", "Incident", "Change", "DevOps"]},
        "priority":      {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
        "source":        {"type": "string", "enum": ["agent", "product", "email", "whatsapp", "teams", "manual"]},
        "reporter_email":{"type": "string", "description": "Reporter's email if from a customer"},
        "labels":        {"type": "string", "description": "Comma-separated labels, e.g. 'auth,security,urgent'"},
        "acceptance":    {"type": "string", "description": "Acceptance criteria / definition of done"},
        "sprint_id":     {"type": "string", "description": "Sprint ID to assign to (optional)"},
        "parent_id":     {"type": "string", "description": "Parent ticket ID for sub-tasks (optional)"},
    },
    required=["title", "description", "assigned_to"],
)

GET_TICKET_S = make_schema(
    "get_ticket",
    "Get a single ticket by ID (e.g. DEV-001) with full details and activity log.",
    {"ticket_id": {"type": "string"}},
    required=["ticket_id"],
)

SEARCH_TICKETS_S = make_schema(
    "search_tickets",
    "Search / filter tickets by role, status, priority, type, or source.",
    {
        "assigned_to": {"type": "string", "enum": ALL_ROLES},
        "status":      {"type": "string", "enum": ["pending", "in_progress", "done", "failed", "blocked", "cancelled"]},
        "priority":    {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
        "ticket_type": {"type": "string", "enum": ["Bug", "Feature", "Task", "Support", "Incident", "Change", "DevOps"]},
        "source":      {"type": "string"},
        "limit":       {"type": "integer"},
    },
    required=[],
)

UPDATE_TICKET_S = make_schema(
    "update_ticket",
    "Update a ticket's status, priority, result, or assignment.",
    {
        "ticket_id":   {"type": "string", "description": "Ticket ID (e.g. DEV-001)"},
        "status":      {"type": "string", "enum": ["pending", "in_progress", "done", "failed", "blocked", "cancelled"]},
        "priority":    {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
        "result":      {"type": "string", "description": "What was done / output"},
        "assigned_to": {"type": "string", "enum": ALL_ROLES},
    },
    required=["ticket_id"],
)

COMMENT_S = make_schema(
    "comment_on_ticket",
    "Add a comment or investigation note to a ticket.",
    {
        "ticket_id": {"type": "string"},
        "body":      {"type": "string", "description": "Comment text"},
    },
    required=["ticket_id", "body"],
)

LINK_S = make_schema(
    "link_tickets",
    "Link two tickets together (relates_to, blocks, duplicates, is_child_of).",
    {
        "from_ticket": {"type": "string"},
        "to_ticket":   {"type": "string"},
        "link_type":   {"type": "string", "enum": ["relates_to", "blocks", "is_blocked_by", "duplicates", "is_child_of"]},
    },
    required=["from_ticket", "to_ticket"],
)

ESCALATE_S = make_schema(
    "escalate_ticket",
    "Escalate a ticket to P1 and post an alert. Use for urgent incidents.",
    {
        "ticket_id": {"type": "string"},
        "reason":    {"type": "string", "description": "Why this is being escalated"},
    },
    required=["ticket_id", "reason"],
)


# ── Handler implementations ───────────────────────────────────────────────────

async def _create_ticket(args: dict) -> str:
    # If Jira MCP is enabled, route through it instead
    if getattr(settings, "jira_mcp_enabled", False):
        return await _jira_mcp_create(args)

    ticket = create_task(
        title=args["title"],
        description=args["description"],
        assigned_to=args["assigned_to"],
        priority=args.get("priority", "P3"),
        ticket_type=args.get("ticket_type", "Task"),
        source=args.get("source", "agent"),
        reporter_email=args.get("reporter_email", ""),
        acceptance_criteria=args.get("acceptance", ""),
        labels=args.get("labels", ""),
        sprint_id=args.get("sprint_id"),
        parent_task_id=args.get("parent_id"),
        created_by="agent",
    )
    escalation = " ⚠️ AUTO-ESCALATED TO P1 (product source)" if (
        args.get("source") == "product" and ticket["priority"] == "P1"
    ) else ""
    return (
        f"✅ Created {ticket['ticket_id']} [{ticket['priority']}] "
        f"→ {ticket['assigned_to']} | {ticket['ticket_type']}{escalation}\n"
        f"SLA due: {ticket['sla_due_at']}"
    )


async def _get_ticket(args: dict) -> str:
    t = get_task(args["ticket_id"])
    if not t:
        return f"Ticket {args['ticket_id']} not found."
    comments = get_comments(t["id"])
    lines = [
        f"🎫 {t['ticket_id']} [{t['priority']}] {t['title']}",
        f"   Type: {t.get('ticket_type','Task')} | Status: {t['status']} | Assigned: {t['assigned_to']}",
        f"   Source: {t.get('source','agent')} | Reporter: {t.get('reporter_email','')}",
        f"   Created: {str(t['created_at'])[:16]} | SLA: {str(t.get('sla_due_at',''))[:16]}",
        f"   Description: {t['description'][:200]}",
    ]
    if comments:
        lines.append(f"   Comments ({len(comments)}):")
        for c in comments[-3:]:
            lines.append(f"     [{c['author']}] {c['body'][:100]}")
    return "\n".join(lines)


async def _search_tickets(args: dict) -> str:
    tickets = get_tasks(
        assigned_to=args.get("assigned_to"),
        status=args.get("status"),
        priority=args.get("priority"),
        ticket_type=args.get("ticket_type"),
        source=args.get("source"),
        limit=int(args.get("limit", 20)),
    )
    if not tickets:
        return "No tickets found."
    lines = []
    for t in tickets:
        lines.append(
            f"[{t['priority']}] {t['ticket_id']} | {t.get('ticket_type','Task')} | "
            f"{t['status']} | {t['assigned_to']} | {t['title'][:60]}"
        )
    return "\n".join(lines)


async def _update_ticket(args: dict) -> str:
    update_task(
        task_id=args["ticket_id"],
        status=args.get("status"),
        result=args.get("result"),
        assigned_to=args.get("assigned_to"),
        priority=args.get("priority"),
    )
    return f"✅ Updated {args['ticket_id']}"


async def _comment(args: dict) -> str:
    c = add_comment(args["ticket_id"], author="agent", body=args["body"])
    if not c:
        return f"Ticket {args['ticket_id']} not found."
    return f"💬 Comment added to {args['ticket_id']}"


async def _link(args: dict) -> str:
    link_tickets(args["from_ticket"], args["to_ticket"], args.get("link_type", "relates_to"))
    return f"🔗 {args['from_ticket']} → {args['to_ticket']} ({args.get('link_type','relates_to')})"


async def _escalate(args: dict) -> str:
    t = get_task(args["ticket_id"])
    if not t:
        return f"Ticket {args['ticket_id']} not found."
    update_task(task_id=args["ticket_id"], priority="P1")
    add_comment(args["ticket_id"], author="system",
                body=f"🚨 ESCALATED TO P1: {args['reason']}")
    # Notify Teams if webhook configured
    try:
        import httpx
        webhook = getattr(settings, "teams_webhook_url", "")
        if webhook:
            await httpx.AsyncClient().post(webhook, json={
                "@type": "MessageCard",
                "themeColor": "FF0000",
                "title": f"🚨 P1 ESCALATION: {t['ticket_id']}",
                "text": f"**{t['title']}**\n\nReason: {args['reason']}",
            })
    except Exception:
        pass
    return f"🚨 {args['ticket_id']} escalated to P1. Teams alert sent."


# ── Jira MCP stub (used when JIRA_MCP_ENABLED=true) ──────────────────────────

async def _jira_mcp_create(args: dict) -> str:
    """
    Route ticket creation through the Jira MCP server.
    To enable: set JIRA_MCP_ENABLED=true and JIRA_MCP_URL in .env
    The Jira MCP server must be running at JIRA_MCP_URL.
    See: https://github.com/sooperset/mcp-atlassian
    """
    import httpx
    url = getattr(settings, "jira_mcp_url", "")
    if not url:
        return "JIRA_MCP_URL not set. Falling back to internal ticket system."
    try:
        r = await httpx.AsyncClient(timeout=10).post(
            f"{url}/tools/create_issue",
            json={
                "project": getattr(settings, "jira_project_key", "PROD"),
                "summary": args["title"],
                "description": args["description"],
                "issuetype": {"name": args.get("ticket_type", "Task")},
                "priority": {"name": _jira_priority(args.get("priority", "P3"))},
                "labels": args.get("labels", "").split(","),
            },
        )
        data = r.json()
        return f"✅ Jira issue created: {data.get('key', 'unknown')}"
    except Exception as e:
        return f"Jira MCP call failed ({e}). Create ticket locally instead."


def _jira_priority(p: str) -> str:
    return {"P1": "Highest", "P2": "High", "P3": "Medium", "P4": "Low"}.get(p, "Medium")


# ── Registration ─────────────────────────────────────────────────────────────

def register():
    ToolRegistry.register("create_ticket",      CREATE_TICKET_S,  _create_ticket)
    ToolRegistry.register("get_ticket",          GET_TICKET_S,     _get_ticket)
    ToolRegistry.register("search_tickets",      SEARCH_TICKETS_S, _search_tickets)
    ToolRegistry.register("update_ticket",       UPDATE_TICKET_S,  _update_ticket)
    ToolRegistry.register("comment_on_ticket",   COMMENT_S,        _comment)
    ToolRegistry.register("link_tickets",        LINK_S,           _link)
    ToolRegistry.register("escalate_ticket",     ESCALATE_S,       _escalate)
