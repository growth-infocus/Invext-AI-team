"""shared/core/config.py — All settings, loaded from .env"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Service identity (set per-container via docker-compose env) ──────────
    service_role: str = "unknown"      # manager | developer | devops | qa | support | docs
    service_port: int = 8000

    # ── LLM providers ────────────────────────────────────────────────────────
    openrouter_api_key:    str = ""
    openrouter_base_url:   str = "https://openrouter.ai/api/v1"
    openrouter_default_model: str = "meta-llama/llama-3.2-3b-instruct:free"

    groq_api_key:          str = ""
    groq_default_model:    str = "llama-3.2-3b-preview"

    gemini_api_key:        str = ""
    gemini_default_model:  str = "gemini-1.5-flash"

    # Per-role LLM routing (set in docker-compose or .env)
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

    # ── Microsoft Teams Integration ────────────────────────────────────────────
    teams_webhook_url: str = ""    # Incoming Webhook URL
    meeting_channel:   str = ""    # Teams channel name for meeting notifications

    # ── Web Search ────────────────────────────────────────────────────────────
    serper_api_key: str = ""

    # ── Neo4j / Graphiti (optional) ──────────────────────────────────────────
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    use_graphiti_memory: bool = False

    # ── Jira MCP Integration (optional) ──────────────────────────────────────
    jira_mcp_enabled:  bool = False
    jira_mcp_url:      str  = "http://localhost:8020"
    jira_project_key:  str  = "PROD"
    jira_base_url:     str  = ""
    jira_user_email:   str  = ""
    jira_api_token:    str  = ""

    # ── DevOps ────────────────────────────────────────────────────────────────
    deploy_notification_enabled: bool = True
    github_webhook_secret: str = ""
    github_repo: str = ""

    # ── Paths ─────────────────────────────────────────────────────────────────
    reports_dir: str = "/app/reports"
    obsidian_vault_path: str = "./obsidian_vault"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
