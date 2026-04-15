"""shared/core/config.py — All settings, loaded from .env"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Service identity ──────────────────────────────────────────────────────
    service_role: str = "unknown"
    service_port: int = 8000

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key:       str = ""
    openai_default_model: str = "gpt-4o-mini"

    # ── OpenRouter ────────────────────────────────────────────────────────────
    openrouter_api_key:           str = ""
    openrouter_base_url:          str = "https://openrouter.ai/api/v1"
    openrouter_default_model:     str = "meta-llama/llama-3.1-8b-instruct:free"
    openrouter_model:             str = ""   # legacy alias

    # ── Groq ──────────────────────────────────────────────────────────────────
    groq_api_key:         str = ""
    groq_default_model:   str = "llama-3.1-8b-instant"
    groq_tool_model:      str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_model:           str = ""   # legacy alias

    # ── Gemini ────────────────────────────────────────────────────────────────
    gemini_api_key:       str = ""
    gemini_default_model: str = "gemini-1.5-flash"
    gemini_model:         str = ""   # legacy alias

    # ── HuggingFace ───────────────────────────────────────────────────────────
    huggingface_api_key:  str = ""
    huggingface_model:    str = "mistralai/Mixtral-8x7B-Instruct-v0.1"

    # ── Per-role LLM routing ──────────────────────────────────────────────────
    manager_provider:    str = "openrouter"
    developer_provider:  str = "groq"
    devops_provider:     str = "groq"
    qa_provider:         str = "openrouter"
    support_provider:    str = "gemini"
    docs_provider:       str = "openrouter"
    design_provider:     str = "openrouter"
    ux_provider:         str = "openrouter"
    ui_test_provider:    str = "openrouter"
    api_test_provider:   str = "groq"
    qa_auto_provider:    str = "groq"
    security_provider:   str = "openrouter"

    # ── Messaging (Redis Streams) ─────────────────────────────────────────────
    redis_url: str = "redis://redis:6379"

    # ── Shared database (Postgres) ────────────────────────────────────────────
    database_url: str = "postgresql+psycopg2://agentuser:agentpass@postgres:5432/agentdb"

    # ── Email ─────────────────────────────────────────────────────────────────
    smtp_host:     str = "smtp.gmail.com"
    smtp_port:     int = 587
    smtp_user:     str = ""
    smtp_password: str = ""
    notify_email:  str = ""
    email_from:    str = ""
    email_to:      str = ""

    # ── Microsoft Teams ───────────────────────────────────────────────────────
    teams_webhook_url: str = ""
    meeting_channel:   str = ""

    # ── Web Search ────────────────────────────────────────────────────────────
    serper_api_key: str = ""

    # ── Neo4j / Graphiti ──────────────────────────────────────────────────────
    neo4j_uri:      str  = "bolt://neo4j:7687"
    neo4j_user:     str  = "neo4j"
    neo4j_password: str  = "password"
    use_graphiti_memory: bool = False

    # ── Jira MCP ──────────────────────────────────────────────────────────────
    jira_mcp_enabled:  bool = False
    jira_mcp_url:      str  = "http://localhost:8020"
    jira_project_key:  str  = "PROD"
    jira_base_url:     str  = ""
    jira_user_email:   str  = ""
    jira_api_token:    str  = ""

    # ── DevOps ────────────────────────────────────────────────────────────────
    deploy_notification_enabled: bool = True
    github_webhook_secret:       str  = ""
    github_repo:                 str  = ""

    # ── Paths ─────────────────────────────────────────────────────────────────
    reports_dir:          str = "/app/reports"
    obsidian_vault_path:  str = "./obsidian_vault"
    sandbox_dir:          str = "/app/sandbox"
    sandbox_timeout_seconds: int = 30

    # ── Scheduler ─────────────────────────────────────────────────────────────
    scheduler_enabled:           bool = True
    daily_plan_cron:             str  = "0 9 * * *"
    delegation_interval_minutes: int  = 30
    daily_report_cron:           str  = "0 17 * * *"
    weekly_report_cron:          str  = "0 8 * * 1"

    # ── Expert Planning System ────────────────────────────────────────────────
    # Phase 1 (manager): project plan requires human approval before tickets are created
    planning_require_approval:       bool  = True

    # Include a dedicated security review phase in every project plan
    planning_include_security_phase: bool  = True

    # Include UI/UX design phase before frontend development
    planning_include_ux_phase:       bool  = True

    # Length of a sprint in calendar days (used for timeline calculations)
    planning_sprint_days:            int   = 14

    # Default priority assigned to tasks created from plans
    planning_default_priority:       str   = "P2"

    # Fraction of capacity to allocate per sprint (0.7 = 70%; rest = meetings/reviews)
    planning_team_capacity_factor:   float = 0.7

    # Agent work-plan: each agent submits a work plan before executing a task
    # Manager must approve it (or auto-approve if False)
    agent_workplan_require_approval: bool  = True

    # Max steps in a single ReAct loop per agent
    agent_max_react_steps:           int   = 15

    # ── Security ─────────────────────────────────────────────────────────────
    api_secret_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
