"""
shared/core/memory.py — Per-agent persistent memory using Redis.

Each agent has TWO memory tiers:
  1. WORKING MEMORY  — recent conversations, current task context (Redis Hash, TTL 7 days)
  2. LONG-TERM MEMORY— important facts, learnings, patterns (Redis Sorted Set, permanent)

On every task:
  • Agent READS relevant memories before starting (context injection)
  • Agent WRITES key learnings after finishing (knowledge retention)

Swap: replace Redis with Graphiti/Neo4j for graph-structured memory by
      implementing the same interface in graphiti_memory.py.
"""
from __future__ import annotations

import json
import time
import asyncio
import logging
from typing import Optional

import redis.asyncio as aioredis

from shared.core.config import settings

log = logging.getLogger("memory")


class AgentMemory:
    """
    Per-agent memory system.
    Usage:
        mem = AgentMemory("developer")
        await mem.connect()
        context = await mem.recall(query="python async patterns")
        await mem.remember("Use asyncio.gather for parallel tool calls — faster than sequential")
    """

    def __init__(self, agent_role: str, redis_url: str | None = None):
        self.role   = agent_role
        self._url   = redis_url or settings.redis_url
        self._r: aioredis.Redis | None = None

    async def connect(self):
        self._r = await aioredis.from_url(
            self._url, encoding="utf-8", decode_responses=True
        )

    # ── Long-term memory: important learnings ─────────────────────────────────
    @property
    def _ltm_key(self) -> str:
        return f"memory:ltm:{self.role}"

    @property
    def _working_key(self) -> str:
        return f"memory:working:{self.role}"

    async def remember(self, fact: str, importance: float = 1.0):
        """Save an important fact to long-term memory."""
        entry = json.dumps({"fact": fact, "ts": time.time()})
        await self._r.zadd(self._ltm_key, {entry: importance})
        # Keep top 200 memories
        await self._r.zremrangebyrank(self._ltm_key, 0, -201)
        log.debug(f"[{self.role}] 🧠 Remembered: {fact[:80]}")

    async def recall(self, query: str = "", limit: int = 10) -> str:
        """
        Retrieve relevant memories. Simple keyword match from top memories.
        Swap with embedding-based search for better recall.
        """
        # Get top-importance memories
        entries = await self._r.zrevrange(self._ltm_key, 0, 99, withscores=False)
        if not entries:
            return ""
        facts = [json.loads(e)["fact"] for e in entries]

        # Keyword filter if query provided
        if query:
            q_words = set(query.lower().split())
            scored = [(f, len(q_words & set(f.lower().split()))) for f in facts]
            scored.sort(key=lambda x: -x[1])
            relevant = [f for f, score in scored[:limit] if score > 0]
        else:
            relevant = facts[:limit]

        if not relevant:
            return ""
        return "Relevant memories:\n" + "\n".join(f"• {f}" for f in relevant)

    # ── Working memory: current session context ───────────────────────────────
    async def set_context(self, key: str, value: str, ttl_seconds: int = 604800):
        """Store a context value (TTL: 7 days by default)."""
        field = f"{key}"
        await self._r.hset(self._working_key, field, value)
        await self._r.expire(self._working_key, ttl_seconds)

    async def get_context(self, key: str) -> Optional[str]:
        return await self._r.hget(self._working_key, key)

    async def get_all_context(self) -> dict:
        return await self._r.hgetall(self._working_key) or {}

    # ── Skills store: agent's curated skill set ───────────────────────────────
    @property
    def _skills_key(self) -> str:
        return f"memory:skills:{self.role}"

    async def load_skills(self, skills: list[str]):
        """Load the agent's built-in skills into memory on startup."""
        if not skills:
            return
        existing = await self._r.llen(self._skills_key)
        if existing == 0:
            await self._r.rpush(self._skills_key, *skills)
            log.info(f"[{self.role}] 📚 {len(skills)} skills loaded")

    async def get_skills(self) -> list[str]:
        return await self._r.lrange(self._skills_key, 0, -1) or []

    async def add_skill(self, skill: str):
        """Agent can learn a new skill during operation."""
        await self._r.rpush(self._skills_key, skill)
        log.info(f"[{self.role}] ✨ New skill acquired: {skill[:60]}")

    # ── Summary ───────────────────────────────────────────────────────────────
    async def summary(self) -> dict:
        ltm_count  = await self._r.zcard(self._ltm_key)
        ctx_fields = len(await self.get_all_context())
        skills     = await self.get_skills()
        return {
            "role":         self.role,
            "ltm_memories": ltm_count,
            "context_keys": ctx_fields,
            "skills_count": len(skills),
            "skills":       skills,
        }


# ── Role-specific built-in skills ─────────────────────────────────────────────
ROLE_SKILLS: dict[str, list[str]] = {
    "manager": [
        "Break goals into SMART tasks with clear acceptance criteria",
        "Assign tasks by expertise: code→developer, infra→devops, testing→qa, docs→docs, users→support",
        "Escalate P1 issues immediately; never let them sit in backlog",
        "Daily standup: yesterday/today/blockers format",
        "Sprint planning: capacity = team_size × sprint_days × 0.7 (30% buffer)",
        "RICE prioritisation: Reach × Impact × Confidence ÷ Effort",
        "Always check task status before creating duplicates",
        "When a task is blocked, reassign or break it into unblocked subtasks",
    ],
    "developer": [
        "Write self-documenting code; names should reveal intent",
        "Single Responsibility Principle: one class/function does one thing",
        "Test-driven development: write tests before implementation when possible",
        "SOLID principles: SRP, OCP, LSP, ISP, DIP",
        "Async-first in Python: use asyncio.gather for parallel I/O",
        "Always validate inputs at service boundaries",
        "Use type hints for all function signatures",
        "Document complex algorithms with inline comments explaining WHY",
        "Search the web for the latest API docs before implementing",
        "Run code to verify it works before reporting done",
    ],
    "devops": [
        "Infrastructure as code: everything reproducible via scripts/configs",
        "12-factor app methodology for cloud-native services",
        "Zero-downtime deployments: blue-green or rolling updates",
        "Every runbook needs a rollback procedure",
        "Secrets management: never in code, always in env or vault",
        "Monitor the four golden signals: latency, traffic, errors, saturation",
        "Container best practices: non-root user, minimal base image, health checks",
        "CI/CD: lint → test → build → staging → prod with manual gate",
        "Always have a backup plan before executing destructive operations",
    ],
    "qa": [
        "Test pyramid: many unit tests, fewer integration, few e2e",
        "Every bug report needs: title, steps to reproduce, expected, actual, severity",
        "Boundary value analysis: test min, max, and edge cases",
        "Equivalence partitioning: group inputs that behave the same",
        "Regression suite: every bug fix gets a test to prevent reappearance",
        "P1 bugs: block the release; P2: fix in current sprint; P3: backlog",
        "Test coverage target: 80% for critical paths",
        "Always test the happy path AND error paths",
        "Performance testing: response time, throughput, resource usage",
    ],
    "support": [
        "Classify issues first: Bug | Feature | Question | Account",
        "Search knowledge base before escalating — 80% of issues are known",
        "Empathy first: acknowledge the user's frustration before solving",
        "Never promise a timeline you can't guarantee",
        "Bug reports need full context: steps, browser, version, logs",
        "Feature requests: capture the underlying need, not just the request",
        "SLA awareness: P1 = respond in 1h, P2 = 4h, P3 = 24h",
        "Follow up after resolution to confirm the issue is resolved",
    ],
    "docs": [
        "Write for the reader's context, not the author's knowledge",
        "Every API endpoint: purpose, parameters, request/response examples",
        "Every runbook: prerequisites, numbered steps, verification, rollback",
        "Architecture Decision Records: context, decision, consequences",
        "README structure: overview, quick start, full docs link",
        "Keep docs close to code — update docs in the same PR as code changes",
        "Use active voice and present tense in documentation",
        "Diagrams explain systems better than paragraphs — use Mermaid/ASCII",
        "Version your docs alongside your API versions",
    ],
    "design": [
        "Audit HTML/CSS for design consistency before suggesting changes",
        "Always check current design trends (Material Design 3, Apple HIG) before recommendations",
        "WCAG AA colour contrast minimum: 4.5:1 for normal text, 3:1 for large text",
        "Design tokens/CSS custom properties: always prefer variables over hardcoded values",
        "8px grid system: all spacing should be multiples of 8",
        "Typography scale: use a modular scale (1.25 or 1.333 ratio)",
        "File design-audit.md with Critical/Major/Minor severity ratings and file:line references",
        "Create tasks for ui_test agent to add visual regression tests after design changes",
    ],
    "ux": [
        "Map complete user journeys before identifying issues — understand the full flow first",
        "Nielsen's 10 heuristics: visibility, match real world, user control, consistency, error prevention, recognition, flexibility, minimal design, error recovery, help",
        "Always check for: missing loading states, empty states, error states in every flow",
        "File ux-audit.md and user-flows.md with Mermaid diagrams for every key flow",
        "Create tasks for developer for missing states, design agent for visual issues",
        "Mobile-first: always check if flows work on 375px width",
        "Cognitive load: if a user needs more than 3 steps for a common action, recommend simplification",
        "Jobs-to-be-Done: frame every issue as 'user cannot accomplish [job]' not just 'button is missing'",
    ],
    "ui_test": [
        "Write Playwright (Python) tests — always prefer data-testid selectors over CSS/XPath",
        "Test all three breakpoints: mobile (375px), tablet (768px), desktop (1440px)",
        "Every form must have tests for: valid submit, each field validation, server error handling",
        "Run tests immediately after writing them — never file 'tests written' without running",
        "File bugs with exact: page URL, steps, expected, actual, screenshot description, severity",
        "P1 bugs (broken nav, blank screen, data loss): send_email alert immediately",
        "Check accessibility in every test: keyboard tab order, ARIA labels, focus management",
        "Page Object Model pattern: one class per page, reusable across test files",
    ],
    "api_test": [
        "Discover ALL endpoints by reading route files before writing any tests",
        "Every endpoint needs: happy path, validation error, auth error, boundary value tests",
        "Validate complete response schema — not just status code, check every field type",
        "Use pytest parametrize for boundary value and equivalence partition tests",
        "File bugs with exact: method, URL, request payload, expected response, actual response",
        "Check for missing endpoints: if a resource has GET, check if POST/PUT/DELETE make sense",
        "Rate limit testing: rapid-fire 10 requests and verify 429 is returned",
        "Create api-test-suite/ folder — one pytest file per API resource/router",
    ],
    "qa_auto": [
        "Auto-detect project stack from requirements.txt/package.json before writing tests",
        "Test pyramid: 70% unit, 20% integration, 10% E2E — don't over-index on E2E",
        "Set up conftest.py with fixtures before writing individual tests",
        "Coverage target: 80% on critical business logic paths, 60% overall",
        "Write run_tests.sh that runs full suite with one command",
        "Identify untestable code and create developer tasks to refactor for testability",
        "Never use time.sleep() in tests — use wait conditions and retries",
        "Create qa-automation-report.md: suite structure, coverage, gaps, recommendations",
    ],
    "security": [
        "Scan in order: secrets first, then auth, then injection, then dependencies",
        "OWASP Top 10 check every audit — never skip any category",
        "Hardcoded secrets: scan for API keys, passwords, tokens in all .py, .js, .env, .yml files",
        "CORS check: allow_origins=['*'] is a Critical finding — always flag it",
        "Dependencies: search CVE database for every package in requirements.txt/package.json",
        "SQL injection: every raw query with user input is Critical until proven parameterised",
        "Critical/High findings: create P1 developer task AND send_email alert immediately",
        "File security-audit.md: finding, severity (Critical/High/Medium/Low), file:line, PoC, fix",
    ],
}
