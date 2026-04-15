"""
shared/core/database.py — Shared Postgres store.
All services read/write the same DB; schema is the single source of truth.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from shared.core.config import settings

SLA_HOURS = {"P1": 2, "P2": 8, "P3": 24, "P4": 72}

TICKET_PREFIXES = {
    "manager": "MGR", "developer": "DEV", "devops": "OPS",
    "qa": "QA", "support": "SUP", "docs": "DOC",
    "design": "DES", "ux": "UX", "ui_test": "UIT",
    "api_test": "APT", "qa_auto": "AUTO", "security": "SEC",
}


def _conn():
    return psycopg2.connect(settings.database_url)


def init_schema():
    """Create / migrate tables on first run."""
    with _conn() as conn, conn.cursor() as cur:

        # ── Core ticket counter ────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_counter (
            prefix TEXT PRIMARY KEY, counter INT NOT NULL DEFAULT 0
        );
        """)

        # ── Main tasks / tickets table ─────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id                  TEXT PRIMARY KEY,
            ticket_id           TEXT UNIQUE NOT NULL,
            title               TEXT NOT NULL,
            description         TEXT,
            acceptance_criteria TEXT,
            assigned_to         TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'pending',
            priority            TEXT NOT NULL DEFAULT 'P3',
            ticket_type         TEXT NOT NULL DEFAULT 'Task',
            source              TEXT NOT NULL DEFAULT 'agent',
            reporter_email      TEXT,
            labels              TEXT,
            result              TEXT,
            parent_task_id      TEXT,
            sprint_id           TEXT,
            sla_due_at          TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at        TIMESTAMPTZ,
            created_by          TEXT DEFAULT 'human'
        );
        """)
        # Backfill new columns if upgrading existing DB
        for col, defn in [
            ("ticket_type",    "TEXT NOT NULL DEFAULT 'Task'"),
            ("source",         "TEXT NOT NULL DEFAULT 'agent'"),
            ("reporter_email", "TEXT"),
        ]:
            try:
                cur.execute(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {col} {defn};")
            except Exception:
                pass

        # ── Ticket comments ────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id          SERIAL PRIMARY KEY,
            task_id     TEXT NOT NULL,
            ticket_id   TEXT NOT NULL,
            author      TEXT NOT NULL,
            body        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_comments_task ON ticket_comments(task_id);
        """)

        # ── Linked tickets ─────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_links (
            id           SERIAL PRIMARY KEY,
            from_ticket  TEXT NOT NULL,
            to_ticket    TEXT NOT NULL,
            link_type    TEXT NOT NULL DEFAULT 'relates_to',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        # ── Task activity log ──────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS task_logs (
            id          SERIAL PRIMARY KEY,
            task_id     TEXT NOT NULL,
            ticket_id   TEXT NOT NULL,
            agent       TEXT NOT NULL,
            event_type  TEXT NOT NULL DEFAULT 'comment',
            message     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_logs_task ON task_logs(task_id);
        """)

        # ── Sprints ────────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sprints (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            goal       TEXT,
            start_date DATE NOT NULL,
            end_date   DATE NOT NULL,
            status     TEXT NOT NULL DEFAULT 'active'
        );
        """)

        # ── Standups ───────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS standup_log (
            id         SERIAL PRIMARY KEY,
            date       DATE NOT NULL,
            agent      TEXT NOT NULL,
            yesterday  TEXT, today TEXT, blockers TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_plans (
            id         TEXT PRIMARY KEY,
            date       DATE NOT NULL,
            agent      TEXT NOT NULL,
            plan       TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        # ── DevOps: deployments ────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id            TEXT PRIMARY KEY,
            service_name  TEXT NOT NULL,
            version       TEXT NOT NULL,
            environment   TEXT NOT NULL DEFAULT 'staging',
            status        TEXT NOT NULL DEFAULT 'pending',
            triggered_by  TEXT NOT NULL DEFAULT 'devops',
            commit_sha    TEXT,
            branch        TEXT,
            pipeline_url  TEXT,
            notes         TEXT,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at   TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_deploy_env ON deployments(environment, status);
        """)

        # ── DevOps: environments ───────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS environments (
            name        TEXT PRIMARY KEY,
            url         TEXT,
            status      TEXT NOT NULL DEFAULT 'unknown',
            last_deploy TEXT,
            health      JSONB,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        INSERT INTO environments (name, url, status) VALUES
            ('development', 'http://localhost:8000', 'unknown'),
            ('staging',     '', 'unknown'),
            ('production',  '', 'unknown')
        ON CONFLICT (name) DO NOTHING;
        """)

        # ── DevOps: pipeline runs ──────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id           TEXT PRIMARY KEY,
            pipeline     TEXT NOT NULL,
            branch       TEXT NOT NULL DEFAULT 'main',
            status       TEXT NOT NULL DEFAULT 'pending',
            steps        JSONB,
            triggered_by TEXT NOT NULL DEFAULT 'devops',
            started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at  TIMESTAMPTZ
        );
        """)

    print("✓ DB schema ready (v2 — tickets + DevOps)")


# ─────────────────────────────────────────────────────────────────────────────
# Ticket ID generation
# ─────────────────────────────────────────────────────────────────────────────

def _next_ticket(role: str) -> str:
    prefix = TICKET_PREFIXES.get(role, "TKT")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ticket_counter (prefix, counter) VALUES (%s, 1)
               ON CONFLICT (prefix) DO UPDATE SET counter = ticket_counter.counter + 1
               RETURNING counter""",
            (prefix,),
        )
        n = cur.fetchone()[0]
    return f"{prefix}-{n:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# Tasks / Tickets CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_task(
    title: str,
    description: str,
    assigned_to: str,
    priority: str = "P3",
    ticket_type: str = "Task",
    source: str = "agent",
    reporter_email: str = "",
    parent_task_id: str = None,
    sprint_id: str = None,
    acceptance_criteria: str = None,
    labels: str = None,
    created_by: str = "manager",
) -> dict:
    now = datetime.now(timezone.utc)
    # Product-sourced support tickets are always P1
    if source == "product" and ticket_type == "Support":
        priority = "P1"
    task = {
        "id":                   str(uuid.uuid4()),
        "ticket_id":            _next_ticket(assigned_to),
        "title":                title,
        "description":          description,
        "acceptance_criteria":  acceptance_criteria or "",
        "assigned_to":          assigned_to,
        "status":               "pending",
        "priority":             priority,
        "ticket_type":          ticket_type,
        "source":               source,
        "reporter_email":       reporter_email or "",
        "result":               None,
        "parent_task_id":       parent_task_id,
        "sprint_id":            sprint_id,
        "labels":               labels or "",
        "sla_due_at":           now + timedelta(hours=SLA_HOURS.get(priority, 24)),
        "created_at":           now,
        "updated_at":           now,
        "completed_at":         None,
        "created_by":           created_by,
    }
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tasks (
                id, ticket_id, title, description, acceptance_criteria,
                assigned_to, status, priority, ticket_type, source, reporter_email,
                result, parent_task_id, sprint_id, labels,
                sla_due_at, created_at, updated_at, created_by
            ) VALUES (
                %(id)s, %(ticket_id)s, %(title)s, %(description)s, %(acceptance_criteria)s,
                %(assigned_to)s, %(status)s, %(priority)s, %(ticket_type)s, %(source)s,
                %(reporter_email)s, %(result)s, %(parent_task_id)s, %(sprint_id)s,
                %(labels)s, %(sla_due_at)s, %(created_at)s, %(updated_at)s, %(created_by)s
            )""", task)
    log_event(task["id"], task["ticket_id"], created_by, "created",
              f"🎫 {task['ticket_id']} [{priority}] created — {title}")
    task["sla_due_at"] = task["sla_due_at"].isoformat()
    task["created_at"] = task["created_at"].isoformat()
    task["updated_at"] = task["updated_at"].isoformat()
    return task


def get_task(task_id: str) -> Optional[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tasks WHERE id=%s OR ticket_id=%s", (task_id, task_id))
            row = cur.fetchone()
    return dict(row) if row else None


def get_tasks(
    assigned_to=None, status=None, priority=None,
    ticket_type=None, source=None, sprint_id=None, limit=50
) -> list[dict]:
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if assigned_to:  query += " AND assigned_to=%s";  params.append(assigned_to)
    if status:       query += " AND status=%s";        params.append(status)
    if priority:     query += " AND priority=%s";      params.append(priority)
    if ticket_type:  query += " AND ticket_type=%s";   params.append(ticket_type)
    if source:       query += " AND source=%s";        params.append(source)
    if sprint_id:    query += " AND sprint_id=%s";     params.append(sprint_id)
    query += " ORDER BY priority ASC, created_at DESC LIMIT %s"
    params.append(limit)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]


def update_task(task_id: str, status=None, result=None, assigned_to=None,
                priority=None, sprint_id=None):
    task = get_task(task_id)
    if not task:
        return
    now = datetime.now(timezone.utc)
    with _conn() as conn, conn.cursor() as cur:
        if status:
            cur.execute("UPDATE tasks SET status=%s, updated_at=%s WHERE id=%s",
                        (status, now, task["id"]))
            if status in ("done", "cancelled"):
                cur.execute("UPDATE tasks SET completed_at=%s WHERE id=%s", (now, task["id"]))
            log_event(task["id"], task["ticket_id"], "system", "status_change",
                      f"Status → {status}")
        if result:
            cur.execute("UPDATE tasks SET result=%s, updated_at=%s WHERE id=%s",
                        (result, now, task["id"]))
        if assigned_to:
            cur.execute("UPDATE tasks SET assigned_to=%s, updated_at=%s WHERE id=%s",
                        (assigned_to, now, task["id"]))
        if priority:
            cur.execute("UPDATE tasks SET priority=%s, updated_at=%s WHERE id=%s",
                        (priority, now, task["id"]))
            log_event(task["id"], task["ticket_id"], "system", "priority_change",
                      f"Priority → {priority}")
        if sprint_id:
            cur.execute("UPDATE tasks SET sprint_id=%s, updated_at=%s WHERE id=%s",
                        (sprint_id, now, task["id"]))


def add_comment(task_id: str, author: str, body: str) -> dict:
    task = get_task(task_id)
    if not task:
        return {}
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO ticket_comments (task_id, ticket_id, author, body) "
            "VALUES (%s,%s,%s,%s) RETURNING *",
            (task["id"], task["ticket_id"], author, body),
        )
        row = dict(cur.fetchone())
    return row


def get_comments(task_id: str) -> list[dict]:
    task = get_task(task_id)
    if not task:
        return []
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ticket_comments WHERE task_id=%s ORDER BY created_at",
                        (task["id"],))
            return [dict(r) for r in cur.fetchall()]


def link_tickets(from_ticket: str, to_ticket: str, link_type: str = "relates_to"):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ticket_links (from_ticket, to_ticket, link_type) VALUES (%s,%s,%s)",
            (from_ticket, to_ticket, link_type),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def log_event(task_id, ticket_id, agent, event_type, message):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO task_logs (task_id,ticket_id,agent,event_type,message) "
            "VALUES (%s,%s,%s,%s,%s)",
            (task_id, ticket_id, agent, event_type, message),
        )


def get_logs(task_id: str) -> list[dict]:
    task = get_task(task_id)
    if not task:
        return []
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM task_logs WHERE task_id=%s ORDER BY created_at",
                        (task["id"],))
            return [dict(r) for r in cur.fetchall()]


def get_sla_breached() -> list[dict]:
    now = datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM tasks WHERE sla_due_at < %s "
                "AND status NOT IN ('done','cancelled','failed')",
                (now,),
            )
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Standups / Plans
# ─────────────────────────────────────────────────────────────────────────────

def save_standup(agent, yesterday, today, blockers):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO standup_log (date,agent,yesterday,today,blockers) "
            "VALUES (CURRENT_DATE,%s,%s,%s,%s)",
            (agent, yesterday, today, blockers),
        )


def save_daily_plan(agent, plan):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO daily_plans (id,date,agent,plan)
               VALUES (%s,CURRENT_DATE,%s,%s)
               ON CONFLICT (id) DO UPDATE SET plan=EXCLUDED.plan""",
            (f"{agent}_{datetime.now().date()}", agent, plan),
        )


def get_standup(date=None) -> list[dict]:
    d = date or datetime.now().date().isoformat()
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM standup_log WHERE date=%s ORDER BY agent", (d,))
            return [dict(r) for r in cur.fetchall()]


def get_active_sprint() -> Optional[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM sprints WHERE status='active' ORDER BY start_date DESC LIMIT 1"
            )
            row = cur.fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# DevOps: Deployments & Environments
# ─────────────────────────────────────────────────────────────────────────────

def create_deployment(service_name: str, version: str, environment: str = "staging",
                      triggered_by: str = "devops", commit_sha: str = "",
                      branch: str = "main", notes: str = "") -> dict:
    row = {
        "id":           str(uuid.uuid4()),
        "service_name": service_name,
        "version":      version,
        "environment":  environment,
        "status":       "pending",
        "triggered_by": triggered_by,
        "commit_sha":   commit_sha,
        "branch":       branch,
        "notes":        notes,
        "started_at":   datetime.now(timezone.utc),
    }
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO deployments (id,service_name,version,environment,status,
            triggered_by,commit_sha,branch,notes,started_at)
            VALUES (%(id)s,%(service_name)s,%(version)s,%(environment)s,%(status)s,
            %(triggered_by)s,%(commit_sha)s,%(branch)s,%(notes)s,%(started_at)s)
        """, row)
    row["started_at"] = row["started_at"].isoformat()
    return row


def get_deployments(environment: str = None, status: str = None, limit: int = 20) -> list[dict]:
    q = "SELECT * FROM deployments WHERE 1=1"
    p = []
    if environment: q += " AND environment=%s"; p.append(environment)
    if status:      q += " AND status=%s";      p.append(status)
    q += " ORDER BY started_at DESC LIMIT %s"; p.append(limit)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(q, p)
            return [dict(r) for r in cur.fetchall()]


def update_deployment(deploy_id: str, status: str, notes: str = ""):
    now = datetime.now(timezone.utc)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE deployments SET status=%s, finished_at=%s WHERE id=%s",
            (status, now if status in ("success", "failed", "rolled_back") else None, deploy_id),
        )


def get_environments() -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM environments ORDER BY name")
            return [dict(r) for r in cur.fetchall()]


def update_environment(name: str, status: str, url: str = None, last_deploy: str = None):
    now = datetime.now(timezone.utc)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE environments SET status=%s, updated_at=%s "
            + (", url=%s" if url else "")
            + (", last_deploy=%s" if last_deploy else "")
            + " WHERE name=%s",
            [status, now] + ([url] if url else []) + ([last_deploy] if last_deploy else []) + [name],
        )


def create_pipeline_run(pipeline: str, branch: str = "main",
                        triggered_by: str = "devops", steps: list = None) -> dict:
    row = {
        "id":           str(uuid.uuid4()),
        "pipeline":     pipeline,
        "branch":       branch,
        "status":       "running",
        "steps":        psycopg2.extras.Json(steps or []),
        "triggered_by": triggered_by,
        "started_at":   datetime.now(timezone.utc),
    }
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO pipeline_runs (id,pipeline,branch,status,steps,triggered_by,started_at)
            VALUES (%(id)s,%(pipeline)s,%(branch)s,%(status)s,%(steps)s,%(triggered_by)s,%(started_at)s)
        """, row)
    row["started_at"] = row["started_at"].isoformat()
    row["steps"] = steps or []
    return row


def get_pipeline_runs(pipeline: str = None, limit: int = 10) -> list[dict]:
    q = "SELECT * FROM pipeline_runs WHERE 1=1"
    p = []
    if pipeline: q += " AND pipeline=%s"; p.append(pipeline)
    q += " ORDER BY started_at DESC LIMIT %s"; p.append(limit)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(q, p)
            return [dict(r) for r in cur.fetchall()]
