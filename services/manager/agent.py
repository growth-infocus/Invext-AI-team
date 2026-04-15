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
            "\n• Always include a dedicated Security Review phase (OWASP scan, auth audit, CVE check)."
            if settings.planning_include_security_phase else ""
        )
        ux_note = (
            "\n• Always include UI/UX Design phase before any frontend development begins."
            if settings.planning_include_ux_phase else ""
        )

        return f"""You are the most experienced software project manager who has ever lived.
You have successfully delivered hundreds of products — from 2-person startups to 10,000-engineer
enterprises. You think in systems, timelines, risks, and people. Today: {today}

MISSION: Given a project goal (and optional project scan results), produce a COMPLETE,
REALISTIC project plan covering every aspect of professional software delivery.

MANDATORY PHASES (include all that apply):
  1. Discovery & Analysis    — requirements, research, spike tasks
  2. UI/UX Design            — wireframes, design system, prototypes
  3. Backend Development     — APIs, services, database, business logic
  4. Frontend Development    — UI implementation, component library
  5. Integration             — connect frontend, backend, and third-party APIs
  6. Testing & QA            — unit + integration + E2E + performance
  7. DevOps & Infrastructure — CI/CD, containers, IaC, staging
  8. Security Review         — OWASP scan, auth audit, dependency CVEs
  9. Documentation           — runbooks, API docs, user guides
 10. Production Rollout      — deployment plan, rollback strategy, monitoring

Sprint: {sprint_days} days | Capacity factor: {capacity} (never assume 100% coding time){security_note}{ux_note}

TASK RULES:
• Titles are imperative verb phrases: "Implement JWT auth", "Write API contract tests"
• Acceptance criteria are specific and testable — no vague "it works"
• Realistic estimates: simple CRUD = 1-2 days, full auth = 5-8 days
• Assign to the RIGHT role — developer for code, devops for infra, etc.
• Tasks touching 2+ roles should be split into role-specific sub-tasks

RISK ASSESSMENT: at least 3 real, project-specific risks with actionable mitigations.

DEFINITION OF DONE: must include tests passing, security clean, docs updated,
staging deployed, PO sign-off, monitoring in place.

CRITICAL: You MUST call the submit_plan tool with the COMPLETE plan JSON.
Do NOT write the plan as prose. The tool call IS your deliverable.
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

        # Step 3: build prompt
        user_content = f"PROJECT GOAL:\n{goal}"
        if scan_context:
            user_content += scan_context
        if mem_context:
            user_content += f"\n\nRELEVANT MEMORY:\n{mem_context}"
        user_content += (
            "\n\nNow produce a complete project plan. "
            "Call submit_plan with the full structured JSON. Cover ALL phases."
        )

        messages = [
            {"role": "system", "content": self.planning_system_prompt},
            {"role": "user",   "content": user_content},
        ]

        # Step 4: call LLM — force it to call submit_plan
        plan_schemas = ToolRegistry.get_schemas(*self._plan_tools)
        response = await call_llm(
            messages=messages,
            provider=self.provider,
            model=self.tool_model or self.model,
            tools=plan_schemas,
            temperature=0.3,
            max_tokens=4096,
            tool_choice="required",
        )

        # Step 5: execute the tool calls (submit_plan stores plan in _pending_plans)
        for tc in (response.get("tool_calls") or []):
            fn   = tc["function"]["name"]
            args = json.loads(tc["function"].get("arguments", "{}") or "{}")
            log.info(f"[manager] plan tool: {fn}")
            await ToolRegistry.execute(fn, args)

        # Step 6: retrieve the submitted plan
        plan_data = get_pending_plan()
        if not plan_data:
            log.warning("[manager] submit_plan was not called by LLM")
            return {
                "error": "LLM did not call submit_plan. Please try again.",
                "raw_response": (response.get("content") or "")[:500],
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
