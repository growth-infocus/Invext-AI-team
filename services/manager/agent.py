"""Manager AI — independent microservice agent (orchestrator)"""
from __future__ import annotations
from datetime import datetime
from shared.core.agent_base import BaseAgent
from shared.core.database import get_tasks


class ManagerAgent(BaseAgent):
    role     = "manager"
    provider = "openrouter"
    required_tools = [
        "create_ticket", "search_tickets", "update_ticket", "comment_on_ticket",
        "escalate_ticket", "link_tickets",
        "create_task", "get_tasks", "update_task",
        "web_search", "send_email",
    ]

    @property
    def system_prompt(self) -> str:
        return f"""You are the IT Product Manager and team orchestrator. Today: {datetime.utcnow().date()}

Your team (assign tasks to the right role):
  developer   → code, architecture, bug fixes, features
  devops      → infrastructure, deployments, CI/CD, monitoring
  qa          → test plans, bug validation, release sign-off
  support     → user issues, customer-facing problems
  docs        → documentation, runbooks, API specs
  design      → UI design audits, design system, visual consistency, CSS fixes
  ux          → user flow mapping, UX audits, usability issues
  ui_test     → UI testing, visual regression, accessibility testing
  api_test    → API endpoint testing, contract testing, response validation
  qa_auto     → full test suite automation, coverage, CI/CD test pipeline
  security    → security audits, OWASP scanning, vulnerability fixes

Your responsibilities:
1. Break every goal into 2–5 specific, actionable tasks
2. Assign each task using create_task with clear title + description + acceptance criteria
3. Use RICE prioritisation: Reach × Impact × Confidence ÷ Effort
4. Always check existing tasks before creating duplicates
5. Monitor progress, unblock stuck tasks, escalate to human when needed
6. Send status summaries via send_email when important milestones complete

Priority rules: P1=critical/security, P2=blocking user work, P3=normal, P4=nice-to-have"""

    async def plan_and_delegate(self, goal: str) -> str:
        mem = await self.memory.recall(goal)
        response = await self.ask(
            (f"Human goal: {goal}\n\n"
             f"{'Context from memory: ' + mem if mem else ''}\n"
             f"1. Analyse the goal carefully.\n"
             f"2. Break into 2-5 tasks using create_task for each.\n"
             f"3. Return a concise plan summary.")
        )
        await self.memory.remember(f"Goal received: {goal[:120]}")
        return response

    async def get_team_status(self) -> str:
        all_t = get_tasks(limit=500)
        counts = {s: sum(1 for t in all_t if t["status"] == s)
                  for s in ("pending", "in_progress", "done", "failed", "blocked")}
        lines = [
            f"📊 Team Status — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
            f"  🟡 Pending:     {counts['pending']}",
            f"  🔵 In progress: {counts['in_progress']}",
            f"  ✅ Done:        {counts['done']}",
            f"  ❌ Failed:      {counts['failed']}",
            f"  🚫 Blocked:     {counts['blocked']}",
        ]
        active = [t for t in all_t if t["status"] == "in_progress"]
        if active:
            lines.append("\nActive now:")
            for t in active:
                lines.append(f"  [{t['assigned_to']}] {t['ticket_id']} {t['title']}")
        return "\n".join(lines)
