"""
services/manager/agent.py — Expert Manager Agent.

Two-phase workflow:
  Phase 1  POST /plan/create   → LLM scans project + calls submit_plan tool
                                  → plan stored in Redis with status=pending_approval
                                  → returned to user for review
  Phase 2  POST /plan/{id}/approve → creates tickets + dispatches to Redis streams
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from shared.core.agent_base import BaseAgent
from shared.core.config import settings
from shared.core.database import (
    get_tasks, create_task as db_create_task,
)
from shared.core.llm import call_llm
from shared.core.plan_store import (
    new_plan_id, save_plan, load_plan, update_plan_status,
)
from shared.tools.plan_tools import get_pending_plan

log = logging.getLogger("manager")


class ManagerAgent(BaseAgent):
    role     = "manager"
    provider = "openrouter"   # overridden by MANAGER_PROVIDER env var

    # Tools for the rapid /goal delegation flow
    required_tools = ["create_task", "get_tasks", "update_task",
                      "web_search", "send_email"]

    # Tools available during the planning loop
    _plan_tools = ["scan_project", "submit_plan"]

    # ── System prompts ────────────────────────────────────────────────────────

    @property
    def system_prompt(self) -> str:
        today = datetime.utcnow().date()
        return f"""You are the Chief Technology Manager of a software company. Today: {today}

Your team (use these role names verbatim when assigning tasks):
  developer   → all backend/frontend code, architecture, bug fixes, features
  devops      → infrastructure, Docker, CI/CD, deployments, monitoring, SRE
  qa          → test strategy, bug validation, release sign-off
  support     → user issues, customer-facing problems, SLA response
  docs        → documentation, runbooks, API specs, changelogs
  design      → UI design, design system, visual consistency
  ux          → user flows, UX audits, usability research
  ui_test     → UI/visual regression testing, accessibility
  api_test    → API contract testing, endpoint validation
  qa_auto     → full test automation, coverage, CI/CD test pipeline
  security    → security audits, OWASP, CVE scanning, pen-testing

Core principles:
• Every task must have SMART acceptance criteria
• Use RICE prioritisation: Reach x Impact x Confidence / Effort
• P1=critical/security, P2=blocks users, P3=normal, P4=nice-to-have
• Never create duplicate tickets — always check get_tasks first
• Sprint velocity = team_size x sprint_days x {settings.planning_team_capacity_factor}
"""

    @property
    def planning_system_prompt(self) -> str:
        today = datetime.utcnow().date()
        sprint_days = settings.planning_sprint_days
        capacity    = settings.planning_team_capacity_factor

        security_note = (
            "\n• A dedicated Security Review phase is MANDATORY (OWASP scan, auth audit, CVE remediation)."
            if settings.planning_include_security_phase else ""
        )
        ux_note = (
            "\n• A dedicated UI/UX Design phase is MANDATORY and must precede all frontend work."
            if settings.planning_include_ux_phase else ""
        )

        return f"""You are a battle-hardened Engineering Director with 25 years of experience leading
engineering teams through crises, product launches, and large-scale remediation efforts.
You've seen systems with 2,000+ bugs and you know exactly how to triage, plan, and execute.
When a report like this lands on your desk, you don't panic — you get organized, get specific,
and get your team moving with complete clarity on what to do, who owns it, and when it's due.
Today is {today}. This plan starts tomorrow.

Your voice as a manager is direct, authoritative, and specific. You speak to your team like a
real leader: "Security team — I need all OWASP A01-A10 findings triaged and the top 3 fixed
by end of Phase 2. No exceptions. I will be reviewing your work plan before you touch any code."

You know that 1,500+ issues fall into clusters, and clusters have clear owners:
• Security vulnerabilities → security agent owns them, P1, non-negotiable timeline
• CI/CD pipeline failures → devops agent owns them, they block everything else
• Auth/API bugs → developer agent, these block all user-facing features
• Test coverage gaps → qa_auto agent, these must be closed before any release
• Documentation debt → docs agent, runs in parallel with fixes
• UI/UX issues → design + ux + ui_test agents, parallel track

You plan in PHASES with REAL HOUR-BY-HOUR TIMELINES, specific owners, and measurable outcomes.
When multiple workers handle the same role in parallel, phase duration = total_task_hours / worker_count.
For example: developer has 3 workers → 4 tasks × 2h each = 8h total work → 8h/3 ≈ 2.7h phase duration.
You demand testable acceptance criteria — not "it works" but "running X test produces Y result".
You call out risks before they hit and you have mitigations ready.

CRITICAL MINDSET — THIS IS AN AI AGENT TEAM:
Your team consists of AI agents that work 24 hours a day, 7 days a week, simultaneously,
at machine speed. They do not sleep. They do not have meetings. They do not get tired.
Multiple agents run in PARALLEL on different tasks at the same time.

AI agent speed reference — derived from measured model throughput (tokens/sec):
  A task's estimated_hours = (react_iterations × tokens_per_iter / tok_per_sec) + tool_call_overhead

  Complexity guide based on LLM call count and token volume:
  - trivial  (2 iters,  ~1k  tokens): 0.1-0.2h  — single-line fix, config change
  - simple   (4 iters,  ~1.6k tokens): 0.2-0.5h  — small bug fix, 1 file change
  - medium   (7 iters,  ~2.6k tokens): 0.5-1.5h  — multi-file fix, auth middleware
  - complex  (12 iters, ~3.8k tokens): 1.5-3h    — full service integration, CI/CD overhaul
  - large    (20 iters, ~5k  tokens):  3-6h       — test suite for whole service

  These are calculated from real model performance metrics stored per agent.
  The actual throughput of each agent's model is tracked and updated live.

THEREFORE: timelines are in HOURS, not days.
  total_timeline_hours: 72-120 hours for a large remediation (not 60 days!)
  phase duration_hours: typically 4-16 hours per phase
  task estimated_hours: use the complexity guide above (0.1h to 6h range)
  start_hour / end_hour: cumulative hours from project kickoff (Hour 0)

════════════════════════════════════════════════════════════════════
MISSION
════════════════════════════════════════════════════════════════════
Produce a COMPLETE, PROFESSIONAL project execution plan. This is not an outline —
it is a real delivery plan with real dates, specific tasks, named owners, and verifiable
acceptance criteria. Day 1 = tomorrow ({today}).

════════════════════════════════════════════════════════════════════
YOUR TEAM — workers and capabilities
════════════════════════════════════════════════════════════════════
  Role        Workers  Domain
  developer   3        backend/frontend code, architecture, bug fixes, refactoring, migrations
  devops      2        infrastructure, Docker, CI/CD, GitHub Actions, deployments, SRE
  security    2        OWASP scanning, auth audits, secret detection, CVE triage, pen-testing
  qa_auto     2        full test automation, coverage reporting, CI/CD test pipeline integration
  docs        2        README, API docs, runbooks, changelogs, inline documentation
  qa          1        manual testing, bug validation, regression sign-off, exploratory testing
  support     1        user-facing issues, SLA response, customer communication
  design      1        UI design, Figma, design system, visual consistency
  ux          1        user flows, UX audits, wireframes, journey mapping
  ui_test     1        UI/visual regression, accessibility (a11y), screenshot diffs
  api_test    1        API contract tests, endpoint validation, Postman/Newman

PARALLELISM RULES:
  • developer (3 workers): 3 tasks run simultaneously → divide task count by 3 for phase duration
  • devops (2 workers): 2 tasks run simultaneously → divide by 2 for phase duration
  • security (2 workers): 2 tasks simultaneously → divide by 2
  • qa_auto (2 workers): 2 tasks simultaneously → divide by 2
  • docs (2 workers): 2 tasks simultaneously → divide by 2
  • All other roles: 1 task at a time

════════════════════════════════════════════════════════════════════
MANDATORY PLAN STRUCTURE  (REJECT criteria if not met)
════════════════════════════════════════════════════════════════════
• MINIMUM 6 phases — plans with fewer phases will be REJECTED
• MINIMUM 4 tasks per phase — phases with fewer tasks will be REJECTED
• All timings are in HOURS — use start_hour, end_hour, duration_hours, estimated_hours
• Phase start_hour/end_hour must be sequential with NO gaps
• Hours sum to total_timeline_hours (target: 72-120 hours for large remediations){security_note}{ux_note}

REQUIRED PHASES — reference timings (AI agents work in parallel within phases):
  Phase 1  | Discovery & Audit       | Hour  0-8    | All agents scan & reproduce their issues in parallel
  Phase 2  | Security & Auth Fixes   | Hour  8-24   | security + developer fix all 7 OWASP issues
  Phase 3  | Core Backend & Scripts  | Hour 24-48   | developer fixes Category C/D/E issues
  Phase 4  | CI/CD & DevOps          | Hour 24-40   | devops fixes all 25 Category B issues (runs parallel to Phase 3)
  Phase 5  | Frontend & UX           | Hour 40-56   | design + ux + ui_test improve UI/accessibility
  Phase 6  | Test Automation         | Hour 48-72   | qa_auto + api_test write and integrate all tests
  Phase 7  | Documentation           | Hour 56-64   | docs agent writes runbooks, API docs, CHANGELOG
  Phase 8  | Staging & UAT           | Hour 72-84   | devops deploys staging, qa + support run UAT
  Phase 9  | Production Rollout      | Hour 84-96   | devops canary deploy, monitoring, rollback playbook

════════════════════════════════════════════════════════════════════
TASK QUALITY REQUIREMENTS (every task MUST have ALL fields)
════════════════════════════════════════════════════════════════════
title:
  Imperative verb phrase, specific. NOT "Fix bugs". YES "Fix JWT token expiry in auth middleware".

description:
  A paragraph that tells the engineer EXACTLY what to do:
  - WHAT the problem/requirement is
  - WHERE it lives (file paths, service names, endpoint names)
  - HOW to fix or implement it, step by step (commands to run, code patterns to use)
  - WHAT to check before marking done

acceptance_criteria:
  A numbered checklist of SPECIFIC, TESTABLE conditions. NOT "it works". YES:
  "1. Running `pytest tests/auth/` passes 100%
   2. Expired tokens return HTTP 401 with body {{'error': 'token_expired'}}
   3. OWASP ZAP scan shows 0 A07 violations
   4. No JWT secrets appear in any log file"

assigned_to:
  Exactly ONE role from the team list. Match the work type — developer for code,
  security for OWASP/CVE, devops for infra, qa_auto for test automation.

priority:
  P1=security/data-loss/system-down, P2=blocks core user flows,
  P3=degraded experience/normal bug, P4=nice-to-have/polish

estimated_hours:
  AI agent speed: config fix=0.5h, 1-line bug=0.25h, auth fix=2-3h,
  CI/CD fix=2-4h, shared middleware=3-5h, full test suite=4-8h, docs=1-2h.

════════════════════════════════════════════════════════════════════
EXAMPLE OF A PERFECTLY FORMED TASK
════════════════════════════════════════════════════════════════════
{{
  "title": "Fix SQL injection vulnerability in /api/search endpoint",
  "description": "The /api/search endpoint in services/search/routes.py line 45 builds
    raw SQL using Python f-strings with unsanitised user input. This is a critical OWASP A03
    injection vulnerability. The security agent will: 1) Write a failing test using sqlmap-like
    payloads to confirm exploitability. 2) Replace f-string SQL with SQLAlchemy parameterized
    .filter() calls. 3) Add Pydantic input validation on all query parameters. 4) Re-run OWASP
    ZAP scan to confirm the finding is resolved. 5) Submit a PR with the fix and test.",
  "acceptance_criteria": "1. `pytest tests/security/test_search_injection.py` passes 100%\\n2. sqlmap probe against /api/search returns 0 injectable parameters\\n3. OWASP ZAP scan shows 0 A03 (Injection) findings for this endpoint\\n4. Code review approved by developer lead\\n5. Staging deploy shows no regressions in search functionality",
  "assigned_to": "security",
  "priority": "P1",
  "ticket_type": "Bug",
  "estimated_hours": 2,
  "depends_on": "Phase 1: Reproduce and document all OWASP findings"
}}

════════════════════════════════════════════════════════════════════
PHASE OBJECTIVE FIELD — HOW EMPLOYEES WILL DO THE WORK
════════════════════════════════════════════════════════════════════
Each phase's "objective" field MUST explain:
  • What each assigned role will DO in this phase (specific actions, not outcomes)
  • What tools, commands, and workflows they will use
  • How they will coordinate (who blocks whom, who reviews whom)
  • What they will report back to the manager at phase end

════════════════════════════════════════════════════════════════════
RISKS (minimum 5, project-specific)
════════════════════════════════════════════════════════════════════
Each risk must describe: what could go wrong, why it's likely, what the impact is,
and a CONCRETE mitigation action (not "monitor it", but "add automated alert for X").

════════════════════════════════════════════════════════════════════
DEFINITION OF DONE (strict checklist — all must be true)
════════════════════════════════════════════════════════════════════
Must include: all P1/P2 tickets closed and verified, test coverage ≥ 70%, OWASP scan clean,
staging environment deployed and stable, all docs updated, monitoring dashboards live,
explicit manager + stakeholder sign-off documented.

════════════════════════════════════════════════════════════════════
CRITICAL INSTRUCTION
════════════════════════════════════════════════════════════════════
You MUST call submit_plan with the COMPLETE structured JSON.
Do NOT write any prose. Do NOT explain the plan. The tool call IS your entire output.
A plan with < 6 phases OR < 3 tasks per phase will be automatically REJECTED.
"""

    # ── Phase 1: Generate a plan for review ───────────────────────────────────

    async def create_project_plan(
        self,
        goal: str,
        project_source: Optional[str] = None,
    ) -> dict:
        """
        Generates a ProjectPlan via LLM using a 2-turn strategy:
          Turn 1 — Free-text plan (no tool forcing) → model writes a full, rich plan in prose.
          Turn 2 — Structured conversion → model reformats its own prose into the submit_plan call.
        Returns the saved plan dict (status='pending_approval'). No tickets are created here.
        """
        from shared.core.tool_registry import ToolRegistry

        # Step 1: scan project source if provided
        scan_context = ""
        if project_source:
            log.info(f"[manager] Scanning: {project_source}")
            scan_result = await ToolRegistry.execute(
                "scan_project", {"source": project_source, "focus": "full"}
            )
            scan_context = f"\n\n=== PROJECT SCAN ===\n{scan_result}\n"

        # Step 2: memory context
        mem_context = await self.memory.recall(goal)

        # Step 3: build inputs — cap goal + scan
        GOAL_CAP = 8000
        SCAN_CAP = 2500
        goal_trimmed = goal[:GOAL_CAP] + (" …[truncated]" if len(goal) > GOAL_CAP else "")
        scan_trimmed = (scan_context[:SCAN_CAP] + " …[truncated]"
                        if len(scan_context) > SCAN_CAP else scan_context)

        base_context = f"PROJECT GOAL AND ISSUE LIST:\n{goal_trimmed}"
        if scan_trimmed:
            base_context += scan_trimmed
        if mem_context:
            base_context += f"\n\nRELEVANT MEMORY:\n{str(mem_context)[:800]}"

        # ── TURN 1: Free-text plan ────────────────────────────────────────────
        # Ask the model to write the complete plan in rich markdown — no tool forcing.
        # Models generate FAR more content in free text than in tool-call JSON.
        turn1_user = base_context + (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "You are the Engineering Director. 1,539 issues just landed on your desk.\n"
            "Write the FULL PROJECT PLAN in professional markdown.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            "IMPORTANT: This is an AI agent team — all timelines are in HOURS, not days.\n"
            "AI agents work 24/7 simultaneously. The entire remediation should take 72-120 hours.\n\n"

            "SECTION 1 — ISSUE TRIAGE & TEAM ASSIGNMENT MATRIX\n"
            "Before any phases, write a table showing exactly how you split the issues:\n"
            "| Category | Issue Count | Issues (by # or range) | Assigned Agent | Phase | Timeline (hours) |\n"
            "|----------|------------|------------------------|----------------|-------|------------------|\n"
            "Fill this with REAL issue numbers and counts from the audit above.\n"
            "Example rows:\n"
            "| Category A — Security | 7 | #1,#2,#3,#4,#5,#6,#7 | security + developer | Phase 2 | Hour 8-24 |\n"
            "| Category B — CI/CD | 25 | #8 through #32 | devops | Phase 4 | Hour 24-40 |\n"
            "| Category C — Scripts | 13 | #33 through #45 | developer | Phase 3 | Hour 24-48 |\n\n"

            "SECTION 2 — PER-AGENT WORKLOAD BREAKDOWN\n"
            "For each agent, write their full workload in hours:\n"
            "**security agent** — N issues, X hours total\n"
            "  - Issue #1: Rotate hardcoded API key — 2h (Hour 8-10)\n"
            "  - Issue #2: Enforce JWT auth — 3h (Hour 10-13)\n"
            "  [list all issues assigned to this agent with per-issue hour estimates]\n\n"
            "**devops agent** — N issues, X hours total\n"
            "  [same format]\n\n"
            "**developer agent** — N issues, X hours total\n"
            "  [same format]\n\n"
            "[Do this for every agent that has work assigned]\n\n"

            "SECTION 3 — PHASES (minimum 9, all required, timings in HOURS)\n"
            "For EACH phase:\n"
            "  ### Phase N: [Name] (Hour X–Y, duration Z hours)\n"
            "  **Why this phase exists:** [what would go wrong if skipped]\n"
            "  **Agents working in parallel:** [list with their specific role]\n"
            "  **Issues resolved in this phase:** #X, #Y, #Z (list actual issue numbers)\n"
            "  **Hour-by-hour work breakdown:**\n"
            "    Hour X-X+1: [agent] reads and reproduces [specific file/issue]\n"
            "    Hour X+1-X+3: [agent] implements fix for issue #N in [specific file:line]\n"
            "    Hour X+3-X+4: [agent] runs tests, commits, reports to manager\n"
            "  **Tasks (minimum 4 per phase):**\n"
            "  For EACH task:\n"
            "    #### Task: [Imperative verb phrase referencing the specific issue]\n"
            "    - Owner: [role]\n"
            "    - Issues covered: #X, #Y (from the audit list above)\n"
            "    - Priority: P1/P2/P3/P4\n"
            "    - Estimate: X hours (e.g. 2h, 0.5h, 4h)\n"
            "    - How to do it: Step-by-step — exact file paths (e.g. scripts/ingest_codebase.py:28),\n"
            "      exact commands (e.g. git filter-branch --tree-filter ...), exact code changes needed\n"
            "    - Acceptance criteria (numbered testable checklist, minimum 4 items):\n"
            "      1. [specific test command and expected output]\n"
            "      2. [specific scan result]\n"
            "      3. [specific behavior check]\n"
            "      4. [code review / CI check]\n\n"

            "REQUIRED PHASES (AI team, running 24/7 in parallel):\n"
            "  Phase 1: Discovery & Audit (Hour 0-8) — all agents scan and reproduce their issues in parallel\n"
            "  Phase 2: Security & Auth Fixes (Hour 8-24) — security + developer fix all 7 Category A issues\n"
            "  Phase 3: Core Backend & Scripts Fixes (Hour 24-48) — developer fixes Categories C/D/E\n"
            "  Phase 4: CI/CD & DevOps Fixes (Hour 24-40) — devops fixes all 25 Category B (parallel with Phase 3)\n"
            "  Phase 5: Frontend, UX & Design (Hour 40-56) — design + ux + ui_test, UI bugs and a11y\n"
            "  Phase 6: Test Automation & Coverage (Hour 48-72) — qa_auto + api_test, coverage ≥70%\n"
            "  Phase 7: Documentation (Hour 56-64) — docs agent, runbooks API docs CHANGELOG\n"
            "  Phase 8: Staging & UAT (Hour 72-84) — devops deploys, qa + support run UAT\n"
            "  Phase 9: Production Rollout (Hour 84-96) — canary deploy, monitoring, rollback playbook\n\n"

            "SECTION 4 — RISKS (minimum 5, each specific to THIS project)\n"
            "For each: what could go wrong, probability, impact, and a concrete action to prevent it.\n\n"

            "SECTION 5 — DELIVERABLES\n"
            "Every tangible output this project produces.\n\n"

            "SECTION 6 — DEFINITION OF DONE\n"
            "Strict numbered checklist. Every item must be specifically verifiable.\n\n"

            "DO NOT abbreviate. DO NOT use placeholders. Write the full plan with real issue numbers, "
            "real file paths, real commands, and real day-by-day work breakdown."
        )

        turn1_msgs = [
            {"role": "system", "content": self.planning_system_prompt},
            {"role": "user",   "content": turn1_user},
        ]

        # Use the most capable planning model — gpt-4o when provider is openai
        plan_model = (
            settings.openai_planning_model
            if self.provider == "openai"
            else (self.tool_model or self.model)
        )
        log.info(f"[manager] Turn 1: generating free-text plan (model={plan_model})...")
        prose_response = await call_llm(
            messages=turn1_msgs,
            provider=self.provider,
            model=plan_model,
            temperature=0.3,
            max_tokens=8000,
        )
        plan_prose = (prose_response.get("content") or "").strip()
        log.info(f"[manager] Turn 1 prose length: {len(plan_prose)} chars")

        if len(plan_prose) < 500:
            log.warning("[manager] Turn 1 produced very little prose — proceeding anyway")

        # ── TURN 2: Structured tool call (submit_plan) ────────────────────────
        # Feed the prose back and ask the model to convert it to the tool call.
        # The model can now "copy" from its own detailed plan — much higher quality.
        turn2_msgs = turn1_msgs + [
            {"role": "assistant", "content": plan_prose},
            {"role": "user", "content": (
                "Your plan above is exactly what I need. Now call submit_plan to encode it in JSON.\n\n"
                "CRITICAL RULES for the JSON:\n"
                "• ALL timings are in HOURS — use total_timeline_hours, duration_hours, "
                "start_hour, end_hour, estimated_hours (NOT days)\n"
                "• Include ALL 9 phases — do not drop any\n"
                "• MINIMUM 4 tasks per phase (you wrote 4+ in your prose — keep them all)\n"
                "• In each task's description field: include the EXACT issue numbers from the audit "
                "(e.g. 'Fixes audit issues #1, #2, #6'), the exact file paths, exact commands, "
                "and step-by-step how the AI agent will do the work\n"
                "• In each task's acceptance_criteria field: numbered list with at least 4 "
                "specific, testable items (test commands, expected outputs, scan results)\n"
                "• In each phase's objective field: state which issue numbers are resolved, "
                "which agents work in parallel, and what they report at phase end\n"
                "• Phases can run in parallel — start_hour of Phase 4 can equal start_hour of Phase 3\n"
                "• Do not simplify or abbreviate anything from your prose plan\n"
                "• Only the tool call counts — no prose"
            )},
        ]

        plan_schemas = ToolRegistry.get_schemas(*self._plan_tools)
        plan_data = None

        for attempt in range(2):
            response = await call_llm(
                messages=turn2_msgs,
                provider=self.provider,
                model=plan_model,
                tools=plan_schemas,
                temperature=0.1,
                max_tokens=16000,
                tool_choice={"type": "function", "function": {"name": "submit_plan"}},
            )
            log.info(
                f"[manager] Turn 2 attempt {attempt+1}: "
                f"tool_calls={len(response.get('tool_calls') or [])}, "
                f"content_len={len(response.get('content') or '')}"
            )

            for tc in (response.get("tool_calls") or []):
                fn  = tc["function"]["name"]
                raw = tc["function"].get("arguments", "{}") or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning(f"[manager] bad JSON in tool args (attempt {attempt+1}): {raw[:200]}")
                    continue
                log.info(f"[manager] plan tool call: {fn}")
                await ToolRegistry.execute(fn, args)

            plan_data = get_pending_plan()
            if plan_data:
                n_phases = len(plan_data.get("phases", []))
                log.info(f"[manager] plan submitted with {n_phases} phases")
                if n_phases >= 3:
                    break
                # If still too few phases, expand on retry
                log.warning(f"[manager] only {n_phases} phases — retrying with expansion prompt")
                plan_data = None

            if attempt == 0:
                # On retry: give model the prose again with stronger expansion instruction
                turn2_msgs.append({"role": "assistant", "content": response.get("content") or ""})
                turn2_msgs.append({"role": "user", "content": (
                    "Your JSON plan is incomplete — it has fewer than 6 phases. "
                    "Your prose plan above has 9 phases. Call submit_plan again with ALL 9 phases. "
                    "Include every single phase and every task. Do not truncate."
                )})

        # Step 6: retrieve the submitted plan
        if not plan_data:
            log.error("[manager] submit_plan was not called after all attempts")
            return {
                "error": "LLM did not produce a complete plan after all attempts. Please try again.",
                "plan_prose": plan_prose[:500],
            }

        # Step 7: persist
        plan_id = new_plan_id()
        plan_data["status"]     = "pending_approval"
        plan_data["goal"]       = goal
        plan_data["source_url"] = project_source or ""
        save_plan(plan_id, plan_data)

        n_phases = len(plan_data.get("phases", []))
        n_tasks  = sum(len(p.get("tasks", [])) for p in plan_data.get("phases", []))
        await self.memory.remember(
            f"Created plan '{plan_data.get('project_name')}' ({plan_id}): "
            f"{n_phases} phases, {n_tasks} tasks — {goal[:80]}"
        )
        log.info(f"[manager] Plan saved: {plan_id} ({n_phases} phases, {n_tasks} tasks)")
        return load_plan(plan_id)

    # ── Phase 2: Execute an approved plan → create tickets ───────────────────

    async def execute_plan(self, plan_id: str) -> dict:
        """
        Creates all tickets from an approved plan and dispatches to agent streams.
        """
        from shared.core.messaging import bus

        plan = load_plan(plan_id)
        if not plan:
            return {"error": f"Plan {plan_id} not found"}
        if plan.get("status") not in ("approved", "pending_approval"):
            return {"error": f"Cannot execute plan with status '{plan.get('status')}'"}

        update_plan_status(plan_id, "executing")
        created_tickets = []
        errors = []

        for phase in sorted(plan.get("phases", []), key=lambda p: p.get("order", 99)):
            phase_name = phase.get("name", "Phase")
            for task in phase.get("tasks", []):
                try:
                    # Support both hours-based (new) and days-based (legacy) timeline fields
                    start   = phase.get("start_hour",  phase.get("start_day",  "?"))
                    end     = phase.get("end_hour",    phase.get("end_day",    "?"))
                    unit    = "h" if "start_hour" in phase else "d"
                    effort  = task.get("estimated_hours", task.get("estimated_days", "?"))
                    e_unit  = "hour(s)" if "estimated_hours" in task else "day(s)"
                    description = (
                        f"[Phase: {phase_name}]\n\n"
                        f"{task.get('description', '')}\n\n"
                        f"Phase objective: {phase.get('objective', '')}\n"
                        f"Timeline: {unit}{start}–{unit}{end}\n"
                        f"Estimated effort: {effort} {e_unit}"
                    )
                    if task.get("depends_on"):
                        description += f"\n\nDepends on: {task['depends_on']}"

                    # Carry estimated_hours (new) or estimated_days (legacy) into labels
                    # so the progress monitor can compare actual vs estimated time
                    est_h = task.get("estimated_hours") or (
                        task.get("estimated_days", 0) * 8  # 8h/day as fallback
                    )
                    phase_slug = phase_name.lower().replace(" ", "_")
                    labels = (
                        f"plan:{plan_id},"
                        f"phase:{phase_slug},"
                        f"estimated_hours:{est_h}"
                    )
                    ticket = db_create_task(
                        title               = task.get("title", "Untitled"),
                        description         = description,
                        assigned_to         = task.get("assigned_to", "developer"),
                        priority            = task.get("priority", settings.planning_default_priority),
                        ticket_type         = task.get("ticket_type", "Task"),
                        acceptance_criteria = task.get("acceptance_criteria", ""),
                        labels              = labels,
                        source              = "manager_plan",
                        created_by          = "manager",
                    )
                    created_tickets.append({
                        "ticket_id":   ticket["ticket_id"],
                        "title":       task.get("title"),
                        "assigned_to": task.get("assigned_to"),
                        "phase":       phase_name,
                        "priority":    task.get("priority"),
                    })
                    await bus.publish(task.get("assigned_to", "developer"), {
                        "task_id":   ticket["id"],
                        "ticket_id": ticket["ticket_id"],
                        "plan_id":   plan_id,
                    })
                except Exception as e:
                    log.error(f"[manager] Task creation failed: {task.get('title')} — {e}")
                    errors.append({"task": task.get("title"), "error": str(e)})

        final_status = "executed_with_errors" if errors else "executed"
        update_plan_status(plan_id, final_status)
        await self.memory.remember(
            f"Executed plan {plan_id}: {len(created_tickets)} tickets created"
        )

        return {
            "plan_id":         plan_id,
            "project_name":    plan.get("project_name"),
            "tickets_created": len(created_tickets),
            "tickets":         created_tickets,
            "errors":          errors,
        }

    # ── Rapid /goal delegation (no approval step) ────────────────────────────

    async def plan_and_delegate(self, goal: str) -> str:
        """Ad-hoc goal → tickets without the full plan approval workflow."""
        mem = await self.memory.recall(goal)
        skills = await self.memory.get_skills()
        sys_prompt = self.system_prompt
        if skills:
            sys_prompt += "\n\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": (
                f"Human goal: {goal}\n\n"
                f"{'Context: ' + mem if mem else ''}\n"
                "Break into 2-5 tasks using create_task, then summarise."
            )},
        ]
        response = await self._react_loop(messages)
        await self.memory.remember(f"Goal received: {goal[:120]}")
        return response

    # ── Team status ───────────────────────────────────────────────────────────

    async def get_team_status(self) -> str:
        all_t = get_tasks(limit=500)
        counts = {s: sum(1 for t in all_t if t["status"] == s)
                  for s in ("pending", "in_progress", "done", "failed", "blocked")}
        lines = [
            f"Team Status — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
            f"  Pending:     {counts['pending']}",
            f"  In progress: {counts['in_progress']}",
            f"  Done:        {counts['done']}",
            f"  Failed:      {counts['failed']}",
            f"  Blocked:     {counts['blocked']}",
        ]
        active = [t for t in all_t if t["status"] == "in_progress"]
        if active:
            lines.append("\nActive now:")
            for t in active:
                lines.append(f"  [{t['assigned_to']}] {t['ticket_id']} {t['title']}")
        return "\n".join(lines)
