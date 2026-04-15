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

        return f"""You are the most experienced Chief Technology Officer and Engineering Manager alive.
You have 30+ years delivering mission-critical software. Your plans are legendary for their precision,
completeness, and realism. Real engineers will execute this plan starting tomorrow. Today: {today}

════════════════════════════════════════════════════════════════════
MISSION
════════════════════════════════════════════════════════════════════
Produce a COMPLETE, PROFESSIONAL project execution plan. This is not an outline —
it is a real delivery plan with real dates, specific tasks, named owners, and verifiable
acceptance criteria. Day 1 = tomorrow ({today}).

════════════════════════════════════════════════════════════════════
YOUR TEAM (use these role names VERBATIM when assigning tasks)
════════════════════════════════════════════════════════════════════
  developer   → all backend/frontend code, architecture, bug fixes, refactoring, migrations
  devops      → infrastructure, Docker, CI/CD pipelines, GitHub Actions, deployments, SRE
  qa          → manual test strategy, bug validation, regression sign-off, exploratory testing
  support     → user-facing issues, customer communication, SLA incident response
  docs        → README, API docs, runbooks, changelogs, inline code documentation
  design      → UI design, Figma, design system, visual consistency, style guide
  ux          → user flows, UX audits, usability research, wireframes, journey mapping
  ui_test     → UI/visual regression tests, accessibility (a11y) audits, screenshot diffs
  api_test    → API contract tests, endpoint validation, Postman/Newman collections
  qa_auto     → full test automation suite, coverage reporting, CI/CD test pipeline integration
  security    → OWASP scanning, authentication audits, secret detection, CVE triage, pen-testing

════════════════════════════════════════════════════════════════════
MANDATORY PLAN STRUCTURE  (REJECT criteria if not met)
════════════════════════════════════════════════════════════════════
• MINIMUM 6 phases — plans with fewer phases will be REJECTED
• MINIMUM 3 tasks per phase — phases with fewer tasks will be REJECTED
• All phase start_day/end_day values must be sequential with NO gaps
• All values must sum to total_timeline_days
• Sprint capacity: {sprint_days} days per sprint, {capacity} capacity factor{security_note}{ux_note}

REQUIRED PHASES (adapt names/scope to the actual project goal, but keep all categories):
  Phase 1  | Discovery & Audit       | Days  1-5   | Reproduce issues, map codebase, categorise bugs
  Phase 2  | Security & Auth Fixes   | Days  6-12  | OWASP fixes, auth flows, input validation, secrets
  Phase 3  | Core Backend Fixes      | Days 13-25  | API bugs, DB errors, business logic, data integrity
  Phase 4  | CI/CD & DevOps          | Days 26-32  | Pipeline fixes, Docker, scripts, deployment config
  Phase 5  | Frontend & UX           | Days 33-40  | UI bugs, accessibility, design polish, UX flows
  Phase 6  | Test Automation         | Days 41-48  | Unit+integration+E2E tests, coverage > 70%
  Phase 7  | Documentation           | Days 49-52  | API docs, runbooks, CHANGELOG, inline comments
  Phase 8  | Staging & UAT           | Days 53-56  | Full staging deploy, UAT, stakeholder sign-off
  Phase 9  | Production Rollout      | Days 57-60  | Canary deploy, monitoring, rollback playbook

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

estimated_days:
  Be realistic. Quick config fix=0.5 days. Medium bug=1-2 days.
  Complex feature or security overhaul=3-5 days. Architectural change=5-8 days.

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
  "estimated_days": 2,
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
        Generates a ProjectPlan via LLM using submit_plan tool.
        Returns the saved plan dict (status='pending_approval').
        No tickets are created here.
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

        # Step 3: build prompt — cap goal + scan to avoid overflowing output tokens
        GOAL_CAP = 8000   # chars; ~2000 tokens — enough for a full audit summary
        SCAN_CAP = 3000
        goal_trimmed = goal[:GOAL_CAP] + (" …[truncated — more issues exist]" if len(goal) > GOAL_CAP else "")
        scan_trimmed = (scan_context[:SCAN_CAP] + " …[truncated]"
                        if len(scan_context) > SCAN_CAP else scan_context)

        user_content = f"PROJECT GOAL AND ISSUE LIST:\n{goal_trimmed}"
        if scan_trimmed:
            user_content += scan_trimmed
        if mem_context:
            user_content += f"\n\nRELEVANT MEMORY:\n{str(mem_context)[:1000]}"
        user_content += (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "NOW: Call submit_plan with the COMPLETE plan.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Requirements:\n"
            "• MINIMUM 6 phases (Discovery → Security → Backend → DevOps → Frontend → QA → Docs → UAT → Deploy)\n"
            "• MINIMUM 3 tasks per phase — each task needs a specific engineer, real deadline, and testable acceptance criteria\n"
            "• Every task description must tell the engineer EXACTLY what file/code to change and HOW\n"
            "• Phases must have sequential start_day/end_day with no gaps\n"
            "• At least 5 risks with concrete mitigations\n"
            "• Strict definition_of_done checklist\n\n"
            "Do NOT write prose. Only the submit_plan tool call counts."
        )

        messages = [
            {"role": "system", "content": self.planning_system_prompt},
            {"role": "user",   "content": user_content},
        ]

        # Step 4: call LLM — force it to call submit_plan (up to 2 attempts)
        plan_schemas = ToolRegistry.get_schemas(*self._plan_tools)
        plan_data    = None

        for attempt in range(2):
            response = await call_llm(
                messages=messages,
                provider=self.provider,
                model=self.tool_model or self.model,
                tools=plan_schemas,
                temperature=0.2,
                max_tokens=16000,
                tool_choice={"type": "function", "function": {"name": "submit_plan"}},
            )
            log.info(
                f"[manager] plan attempt {attempt+1}: "
                f"tool_calls={len(response.get('tool_calls') or [])}, "
                f"content_len={len(response.get('content') or '')}"
            )

            # Step 5: execute tool calls
            for tc in (response.get("tool_calls") or []):
                fn   = tc["function"]["name"]
                raw  = tc["function"].get("arguments", "{}") or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning(f"[manager] bad JSON in tool args (attempt {attempt+1}): {raw[:200]}")
                    continue
                log.info(f"[manager] plan tool call: {fn}")
                await ToolRegistry.execute(fn, args)

            plan_data = get_pending_plan()
            if plan_data:
                break

            # Retry — keep the FULL quality system prompt, just shorten the user message
            if attempt == 0:
                log.warning("[manager] submit_plan not called — retrying with direct user message")
                messages = [
                    {"role": "system", "content": self.planning_system_prompt},
                    {"role": "user", "content": (
                        f"PROJECT GOAL:\n{goal[:1500]}\n\n"
                        "CALL submit_plan NOW. Minimum 6 phases, minimum 3 tasks per phase. "
                        "Fill every required field: title, description, acceptance_criteria, "
                        "assigned_to, priority, estimated_days. "
                        "Do not write prose. Only the tool call counts."
                    )},
                ]

        # Step 6: retrieve the submitted plan
        if not plan_data:
            log.error("[manager] submit_plan was not called after 2 attempts")
            return {
                "error": "LLM did not call submit_plan after 2 attempts. Please try again.",
                "raw_response": (response.get("content") or "")[:800],
            }

        # Step 7: persist
        plan_id = new_plan_id()
        plan_data["status"]     = "pending_approval"
        plan_data["goal"]       = goal
        plan_data["source_url"] = project_source or ""
        save_plan(plan_id, plan_data)

        await self.memory.remember(
            f"Created plan '{plan_data.get('project_name')}' ({plan_id}): {goal[:100]}"
        )
        log.info(f"[manager] Plan saved: {plan_id}")
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
                    description = (
                        f"[Phase: {phase_name}]\n\n"
                        f"{task.get('description', '')}\n\n"
                        f"Phase objective: {phase.get('objective', '')}\n"
                        f"Timeline: Day {phase.get('start_day')}–{phase.get('end_day')}\n"
                        f"Estimated effort: {task.get('estimated_days', '?')} day(s)"
                    )
                    if task.get("depends_on"):
                        description += f"\n\nDepends on: {task['depends_on']}"

                    ticket = db_create_task(
                        title               = task.get("title", "Untitled"),
                        description         = description,
                        assigned_to         = task.get("assigned_to", "developer"),
                        priority            = task.get("priority", settings.planning_default_priority),
                        ticket_type         = task.get("ticket_type", "Task"),
                        acceptance_criteria = task.get("acceptance_criteria", ""),
                        labels              = f"plan:{plan_id},phase:{phase_name.lower().replace(' ','_')}",
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
