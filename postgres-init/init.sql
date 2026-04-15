-- ============================================================
-- AI Agent Microservices — Postgres Schema Initialisation
-- Runs automatically on first container start
-- ============================================================

-- Ticket ID counter per role (for JIRA-style IDs)
CREATE TABLE IF NOT EXISTS ticket_counter (
    prefix  TEXT PRIMARY KEY,
    counter INT  NOT NULL DEFAULT 0
);

-- Tasks / Tickets table
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT         PRIMARY KEY,
    ticket_id           TEXT         UNIQUE NOT NULL,
    title               TEXT         NOT NULL,
    description         TEXT,
    acceptance_criteria TEXT,
    assigned_to         TEXT         NOT NULL,
    status              TEXT         NOT NULL DEFAULT 'pending',
    priority            TEXT         NOT NULL DEFAULT 'P3',
    ticket_type         TEXT         NOT NULL DEFAULT 'Task',
    source              TEXT         NOT NULL DEFAULT 'agent',
    reporter_email      TEXT,
    labels              TEXT,
    result              TEXT,
    parent_task_id      TEXT,
    sprint_id           TEXT,
    sla_due_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    created_by          TEXT         DEFAULT 'manager'
);

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_status ON tasks (assigned_to, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks (priority, created_at);

-- Ticket comments
CREATE TABLE IF NOT EXISTS ticket_comments (
    id         SERIAL      PRIMARY KEY,
    task_id    TEXT        NOT NULL,
    ticket_id  TEXT        NOT NULL,
    author     TEXT        NOT NULL,
    body       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON ticket_comments(task_id);

-- Linked tickets
CREATE TABLE IF NOT EXISTS ticket_links (
    id          SERIAL      PRIMARY KEY,
    from_ticket TEXT        NOT NULL,
    to_ticket   TEXT        NOT NULL,
    link_type   TEXT        NOT NULL DEFAULT 'relates_to',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Task activity log
CREATE TABLE IF NOT EXISTS task_logs (
    id         SERIAL      PRIMARY KEY,
    task_id    TEXT        NOT NULL,
    ticket_id  TEXT        NOT NULL,
    agent      TEXT        NOT NULL,
    event_type TEXT        NOT NULL DEFAULT 'comment',
    message    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_logs_task ON task_logs(task_id);

-- Sprints
CREATE TABLE IF NOT EXISTS sprints (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    goal       TEXT,
    start_date DATE NOT NULL,
    end_date   DATE NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active'
);

-- Standups
CREATE TABLE IF NOT EXISTS standup_log (
    id         SERIAL      PRIMARY KEY,
    date       DATE        NOT NULL,
    agent      TEXT        NOT NULL,
    yesterday  TEXT,
    today      TEXT,
    blockers   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id         TEXT        PRIMARY KEY,
    date       DATE        NOT NULL,
    agent      TEXT        NOT NULL,
    plan       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- DevOps: deployments
CREATE TABLE IF NOT EXISTS deployments (
    id            TEXT        PRIMARY KEY,
    service_name  TEXT        NOT NULL,
    version       TEXT        NOT NULL,
    environment   TEXT        NOT NULL DEFAULT 'staging',
    status        TEXT        NOT NULL DEFAULT 'pending',
    triggered_by  TEXT        NOT NULL DEFAULT 'devops',
    commit_sha    TEXT,
    branch        TEXT,
    pipeline_url  TEXT,
    notes         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deploy_env ON deployments(environment, status);

-- DevOps: environments
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

-- DevOps: pipeline runs
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           TEXT        PRIMARY KEY,
    pipeline     TEXT        NOT NULL,
    branch       TEXT        NOT NULL DEFAULT 'main',
    status       TEXT        NOT NULL DEFAULT 'pending',
    steps        JSONB,
    triggered_by TEXT        NOT NULL DEFAULT 'devops',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at  TIMESTAMPTZ
);

-- Grant all to agentuser
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO agentuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO agentuser;
