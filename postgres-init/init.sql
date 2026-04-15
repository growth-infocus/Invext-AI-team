-- ============================================================
-- AI Agent Microservices — Postgres Schema Initialisation
-- Runs automatically on first container start
-- ============================================================

-- Tasks / Tickets table
CREATE TABLE IF NOT EXISTS tasks (
    id            SERIAL PRIMARY KEY,
    ticket_id     VARCHAR(20)  UNIQUE NOT NULL,   -- e.g. DEV-001
    title         TEXT         NOT NULL,
    description   TEXT         NOT NULL DEFAULT '',
    assigned_to   VARCHAR(50)  NOT NULL,           -- developer | devops | qa | support | docs
    created_by    VARCHAR(50)  NOT NULL DEFAULT 'manager',
    status        VARCHAR(20)  NOT NULL DEFAULT 'pending',
                               -- pending | in_progress | done | failed | blocked
    priority      VARCHAR(5)   NOT NULL DEFAULT 'P3',  -- P1-P4
    acceptance    TEXT         NOT NULL DEFAULT '',    -- acceptance criteria
    result        TEXT,                               -- agent output when done
    error_msg     TEXT,                               -- error if failed
    sprint        VARCHAR(20)  NOT NULL DEFAULT 'backlog',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    due_at        TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);

-- Index for fast agent queue queries
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_status
    ON tasks (assigned_to, status);

CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks (status);

CREATE INDEX IF NOT EXISTS idx_tasks_priority
    ON tasks (priority, created_at);

-- Ticket ID counter per role (for JIRA-style IDs)
CREATE TABLE IF NOT EXISTS ticket_counters (
    role    VARCHAR(20) PRIMARY KEY,
    counter INT         NOT NULL DEFAULT 0
);

-- Pre-seed counters for all roles
INSERT INTO ticket_counters (role, counter) VALUES
    ('DEV', 0),
    ('OPS', 0),
    ('QA',  0),
    ('SUP', 0),
    ('DOC', 0),
    ('MGR', 0),
    ('DES',  0),  -- Design
    ('UX',   0),  -- UX
    ('UIT',  0),  -- UI Test
    ('APT',  0),  -- API Test
    ('AUTO', 0),  -- QA Automation
    ('SEC',  0)   -- Security
ON CONFLICT (role) DO NOTHING;

-- Event log for audit trail
CREATE TABLE IF NOT EXISTS agent_events (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(50) NOT NULL,   -- task_created | task_started | task_done | message_sent
    agent_role  VARCHAR(50),
    task_id     VARCHAR(20),
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_agent_role
    ON agent_events (agent_role, created_at DESC);

-- Auto-update updated_at on tasks
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Grant all to agentuser (already owner, but explicit for safety)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO agentuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO agentuser;
