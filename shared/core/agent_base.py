"""
shared/core/agent_base.py — BaseAgent for all microservices.

Every agent service:
  1. Extends BaseAgent
  2. Subscribes to its Redis stream on startup
  3. Runs the ReAct loop per task
  4. Maintains per-role memory + skills
  5. Publishes results back to the results stream
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from shared.core.config import settings
from shared.core.llm import call_llm
from shared.core.tool_registry import ToolRegistry
from shared.core.database import (
    get_task, update_task, log_event, save_daily_plan, save_standup
)
from shared.core.messaging import bus
from shared.core.memory import AgentMemory, ROLE_SKILLS

log = logging.getLogger("agent")


def _get_memory_backend(role: str):
    """Factory function to select memory backend based on settings."""
    if settings.use_graphiti_memory:
        from shared.core.graphiti_memory import GraphitiMemory
        return GraphitiMemory(role)
    else:
        return AgentMemory(role)


class BaseAgent(ABC):
    """
    Independent-service base agent.

    To create a new agent service:
      class SecurityAgent(BaseAgent):
          role = "security"
          provider = "openrouter"
          required_tools = ["web_search", "code_run"]

          @property
          def system_prompt(self) -> str:
              return "You are a security engineer..."
    """

    role: str = "base"
    provider: str = "openrouter"
    model: Optional[str] = None
    required_tools: list[str] = []

    def __init__(self):
        self.memory = _get_memory_backend(self.role)
        if settings.use_graphiti_memory:
            log.info(f"[{self.role}] Using Graphiti memory backend")
        else:
            log.info(f"[{self.role}] Using Redis memory backend")
        self._running = False

    # ── Subclass implements ───────────────────────────────────────────────────
    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    @property
    def tools(self) -> list[dict]:
        return ToolRegistry.get_schemas(*self.required_tools)

    # ── Startup ───────────────────────────────────────────────────────────────
    async def startup(self):
        """Call once on service startup."""
        await self.memory.connect()
        # Load role-specific built-in skills
        skills = ROLE_SKILLS.get(self.role, [])
        await self.memory.load_skills(skills)
        await bus.connect()
        log.info(f"[{self.role}] 🚀 Agent ready | skills={len(skills)} | tools={self.required_tools}")

    # ── Main service loop — listens for tasks forever ─────────────────────────
    async def listen(self):
        """Blocking: subscribe to Redis stream and process tasks as they arrive."""
        self._running = True
        log.info(f"[{self.role}] 👂 Listening for tasks…")
        async for msg_id, payload in bus.consume(self.role):
            task_id = payload.get("task_id")
            if not task_id:
                continue
            log.info(f"[{self.role}] 📥 Received task {payload.get('ticket_id','?')}")
            asyncio.create_task(self._handle_task(task_id))

    async def _handle_task(self, task_id: str):
        task = get_task(task_id)
        if not task:
            log.error(f"[{self.role}] Task {task_id} not found in DB")
            return

        update_task(task_id, status="in_progress")
        log_event(task_id, task["ticket_id"], self.role, "comment",
                  f"🟡 {self.role.title()} picked up task")

        try:
            result = await self.run(task_id)
        except Exception as e:
            result = f"FAILED: {e}"
            update_task(task_id, status="failed", result=result)
            log_event(task_id, task["ticket_id"], self.role, "status_change", f"❌ {e}")
            return

        update_task(task_id, status="done", result=result)

        # Save a memory of what was learned
        await self.memory.remember(
            f"Completed [{task['ticket_id']}] {task['title'][:80]} — {result[:150]}"
        )

        # Publish result back for Manager / dashboard
        await bus.publish_result({
            "task_id":    task_id,
            "ticket_id":  task["ticket_id"],
            "agent":      self.role,
            "status":     "done",
            "result":     result,
        })

    # ── ReAct execution loop ──────────────────────────────────────────────────
    async def run(self, task_id: str) -> str:
        task = get_task(task_id)
        if not task:
            return "Task not found."

        goal = f"Ticket: {task['ticket_id']}\nTitle: {task['title']}\n\n{task['description']}"
        if task.get("acceptance_criteria"):
            goal += f"\n\nAcceptance criteria:\n{task['acceptance_criteria']}"

        # Inject memory context
        mem_context = await self.memory.recall(task["title"] + " " + task["description"])
        skills      = await self.memory.get_skills()
        skills_text = ("\n\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)) if skills else ""

        messages = [
            {"role": "system", "content": self.system_prompt + skills_text},
        ]
        if mem_context:
            messages.append({"role": "system", "content": mem_context})
        messages.append({"role": "user", "content": f"Your goal:\n{goal}"})

        return await self._react_loop(messages, task_id)

    async def _react_loop(self, messages: list[dict], task_id: str,
                          max_steps: int = 12) -> str:
        task = get_task(task_id)
        ticket_id = task["ticket_id"] if task else task_id

        for step in range(max_steps):
            log_event(task_id, ticket_id, self.role, "comment", f"⚙️ Step {step+1}")

            response = await call_llm(
                messages=messages,
                provider=self.provider,
                model=self.model,
                tools=self.tools or None,
                temperature=0.2,
            )
            content    = response.get("content") or ""
            tool_calls = response.get("tool_calls")
            messages.append({"role": "assistant", "content": content})

            if not tool_calls:
                return content or "(task complete, no output)"

            for tc in tool_calls:
                fn   = tc["function"]["name"]
                args = json.loads(tc["function"].get("arguments", "{}") or "{}")
                log_event(task_id, ticket_id, self.role, "comment", f"🔧 {fn}({args})")
                result = await ToolRegistry.execute(fn, args)
                messages.append({
                    "role":        "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name":         fn,
                    "content":      str(result),
                })

        return next((m["content"] for m in reversed(messages)
                     if m["role"] == "assistant" and m.get("content")), "Max steps reached.")

    # ── Direct Q&A ────────────────────────────────────────────────────────────
    async def ask(self, question: str) -> str:
        mem = await self.memory.recall(question)
        skills = await self.memory.get_skills()
        sys = self.system_prompt
        if skills:
            sys += "\n\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)
        msgs = [{"role": "system", "content": sys}]
        if mem:
            msgs.append({"role": "system", "content": mem})
        msgs.append({"role": "user", "content": question})
        r = await call_llm(messages=msgs, provider=self.provider, model=self.model)
        return r.get("content", "")

    # ── Daily plan ────────────────────────────────────────────────────────────
    async def generate_daily_plan(self, pending_tasks: list[dict]) -> str:
        tasks_text = "\n".join(
            f"[{t['priority']}] {t['ticket_id']} {t['title']}: {t['description'][:100]}"
            for t in pending_tasks
        ) or "No pending tasks."
        mem = await self.memory.recall("daily work priorities")
        plan = await self.ask(
            f"Today: {datetime.utcnow().date()}\nOpen tasks:\n{tasks_text}"
            f"\n{'Context: ' + mem if mem else ''}"
            f"\nWrite a short, realistic daily work plan as a {self.role}."
        )
        save_daily_plan(self.role, plan)

        done_yday = []  # Would query for yesterday's completions in real use
        save_standup(self.role, "See previous day's results", plan[:300], "None")
        await self.memory.remember(
            f"Daily plan {datetime.utcnow().date()}: {plan[:200]}"
        )
        return plan
