"""
shared/core/agent_base.py — BaseAgent for all microservices.

Task flow per agent:
  1. Receive task from Redis stream
  2. Create a work plan (submit_work_plan tool)
  3. Store work plan in Redis → POST to manager stream for review
  4. If AGENT_WORKPLAN_REQUIRE_APPROVAL=true: wait (poll) until manager approves/rejects
     - Approved  → run task
     - Rejected  → log rejection + mark ticket blocked
     - Timeout   → auto-proceed after WORKPLAN_APPROVAL_TIMEOUT_MINUTES
  5. Run ReAct loop
  6. Publish result to manager stream
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import redis as _sync_redis

from shared.core.config import settings
from shared.core.llm import call_llm
from shared.core.tool_registry import ToolRegistry
from shared.core.database import (
    get_task, update_task, log_event, save_daily_plan, save_standup
)
from shared.core.messaging import bus
from shared.core.memory import AgentMemory, ROLE_SKILLS

log = logging.getLogger("agent")

# How long (seconds) an agent waits for manager approval before auto-proceeding
WORKPLAN_APPROVAL_TIMEOUT = 10 * 60   # 10 minutes
WORKPLAN_POLL_INTERVAL    = 15        # check every 15 seconds


def _redis_sync() -> _sync_redis.Redis:
    return _sync_redis.from_url(settings.redis_url, decode_responses=True)


def _get_memory_backend(role: str):
    if settings.use_graphiti_memory:
        from shared.core.graphiti_memory import GraphitiMemory
        return GraphitiMemory(role)
    return AgentMemory(role)


# ── Work-plan Redis helpers ───────────────────────────────────────────────────

def _workplan_key(role: str, task_id: str) -> str:
    return f"workplan:{role}:{task_id}"


def store_workplan(role: str, task_id: str, ticket_id: str, plan: dict):
    r = _redis_sync()
    data = {
        **plan,
        "task_id":   task_id,
        "ticket_id": ticket_id,
        "agent":     role,
        "status":    "pending_manager_review",
        "created_at": datetime.utcnow().isoformat(),
        "feedback":  "",
    }
    r.setex(_workplan_key(role, task_id), 60 * 60 * 24 * 7, json.dumps(data))


def get_workplan(role: str, task_id: str) -> Optional[dict]:
    r = _redis_sync()
    raw = r.get(_workplan_key(role, task_id))
    return json.loads(raw) if raw else None


def set_workplan_status(role: str, task_id: str, status: str, feedback: str = ""):
    """Called by manager to approve/reject an agent's work plan."""
    r = _redis_sync()
    raw = r.get(_workplan_key(role, task_id))
    if not raw:
        return False
    data = json.loads(raw)
    data["status"]       = status
    data["feedback"]     = feedback
    data["reviewed_at"]  = datetime.utcnow().isoformat()
    r.setex(_workplan_key(role, task_id), 60 * 60 * 24 * 7, json.dumps(data))
    return True


def list_pending_workplans() -> list[dict]:
    """Return all work plans awaiting manager review."""
    r = _redis_sync()
    keys = r.keys("workplan:*")
    result = []
    for key in keys:
        raw = r.get(key)
        if raw:
            try:
                d = json.loads(raw)
                if d.get("status") == "pending_manager_review":
                    result.append(d)
            except Exception:
                pass
    return sorted(result, key=lambda x: x.get("created_at", ""))


def list_all_workplans(status_filter: Optional[str] = None) -> list[dict]:
    """Return all work plans, optionally filtered by status."""
    r = _redis_sync()
    keys = r.keys("workplan:*")
    result = []
    for key in keys:
        raw = r.get(key)
        if raw:
            try:
                d = json.loads(raw)
                if not status_filter or d.get("status") == status_filter:
                    result.append(d)
            except Exception:
                pass
    return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)


# ── BaseAgent ─────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    role: str = "base"
    provider: str = "openrouter"
    model: Optional[str] = None
    tool_model: Optional[str] = None
    required_tools: list[str] = [
        "workspace_read", "workspace_write", "workspace_patch",
        "workspace_list", "workspace_bash", "workspace_git",
    ]

    def __init__(self):
        self.memory = _get_memory_backend(self.role)
        self._running = False
        env_provider = getattr(settings, f"{self.role}_provider", None)
        if env_provider:
            self.provider = env_provider
        if self.provider == "groq" and not self.tool_model:
            self.tool_model = settings.groq_tool_model
        if self.provider == "openai" and not self.tool_model:
            self.tool_model = settings.openai_default_model

    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    @property
    def workspace_context(self) -> str:
        """Appended to every agent's system prompt so they know how to use workspace tools."""
        import os
        workspace = os.environ.get("WORKSPACE_DIR", "/workspace")
        return f"""

=== WORKSPACE ACCESS ===
The client codebase is mounted at {workspace}.
You have direct access to it via these tools:
  workspace_read  path             — read any file (relative to {workspace})
  workspace_write path content     — write/overwrite a file
  workspace_patch path old new     — targeted string replacement (preferred for edits)
  workspace_list  path pattern     — list files
  workspace_bash  cmd              — run any shell command (grep, pytest, pip, etc.)
  workspace_git   args             — run git commands (status, diff, add, commit, push)

ACCOUNTABILITY WORKFLOW — you MUST follow this for every fix:
  1. REPRODUCE  — use workspace_bash to confirm the bug exists (grep, read, run tests)
  2. CONFIRM    — use workspace_read to read the exact file and understand root cause
  3. FIX        — use workspace_patch or workspace_write to apply the fix
  4. VERIFY     — use workspace_bash to run affected tests or re-check the condition
  5. COMMIT     — use workspace_git to stage and commit: git add <file> && git commit -m "fix: ..."
  6. REPORT     — include evidence at every step in your task result

Never skip steps. Each step must have evidence.
"""

    @property
    def tools(self) -> list[dict]:
        return ToolRegistry.get_schemas(*self.required_tools)

    # ── Startup ───────────────────────────────────────────────────────────────

    def worker_count(self) -> int:
        """Return the configured number of parallel task workers for this role."""
        role_key = f"{self.role}_workers"
        count = getattr(settings, role_key, None)
        if count is not None:
            return int(count)
        return int(settings.agent_worker_concurrency)

    async def startup(self):
        await self.memory.connect()
        skills = ROLE_SKILLS.get(self.role, [])
        await self.memory.load_skills(skills)
        await bus.connect()
        n = self.worker_count()
        log.info(f"[{self.role}] Agent ready | workers={n} | tools={self.required_tools}")

    def spawn_workers(self) -> list:
        """
        Create asyncio tasks for all parallel workers.
        Call this instead of asyncio.create_task(agent.listen()) in main.py.
        Returns the list of tasks (for shutdown handling if needed).
        """
        n = self.worker_count()
        tasks = [asyncio.create_task(self.listen(worker_index=i)) for i in range(n)]
        log.info(f"[{self.role}] Spawned {n} worker(s)")
        return tasks

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def listen(self, worker_index: int = 0):
        """
        Main consumer loop. worker_index makes the consumer name unique so
        multiple instances of the same agent (same container OR separate containers)
        can all pull from the same Redis Stream consumer group without conflicts.
        """
        import socket
        hostname  = socket.gethostname()   # unique per Docker container
        consumer  = f"{self.role}-{hostname}-w{worker_index}"
        self._running = True
        log.info(f"[{self.role}] Worker {consumer} listening…")
        async for msg_id, payload in bus.consume(self.role, consumer=consumer):
            task_id = payload.get("task_id")
            if not task_id:
                continue
            log.info(f"[{self.role}/{consumer}] Received task {payload.get('ticket_id','?')}")
            asyncio.create_task(self._handle_task(task_id))

    async def _handle_task(self, task_id: str):
        task = get_task(task_id)
        if not task:
            log.error(f"[{self.role}] Task {task_id} not found")
            return

        update_task(task_id, status="planning")
        log_event(task_id, task["ticket_id"], self.role, "comment",
                  f"{self.role.title()} received task — creating work plan")

        try:
            # ── Steps 1–2: plan → submit → await approval (with revision loop) ─
            work_plan = await self._plan_approval_loop(task)
            if work_plan is None:
                # Permanently rejected after max revisions
                return

            # ── Step 3: execute ───────────────────────────────────────────────
            update_task(task_id, status="in_progress")
            log_event(task_id, task["ticket_id"], self.role, "comment",
                      "Work plan approved — starting execution")

            questions = work_plan.get("questions_for_manager", "None")
            if questions and questions.lower() not in ("none", "n/a", ""):
                await self._ask_manager(task_id, task["ticket_id"], questions)

            result = await self.run(task_id)

        except Exception as e:
            result = f"FAILED: {e}"
            update_task(task_id, status="failed", result=result)
            log_event(task_id, task["ticket_id"], self.role, "status_change", f"Error: {e}")
            return

        update_task(task_id, status="done", result=result)
        await self.memory.remember(
            f"Completed [{task['ticket_id']}] {task['title'][:80]} — {result[:150]}"
        )
        await bus.publish_result({
            "task_id":   task_id,
            "ticket_id": task["ticket_id"],
            "agent":     self.role,
            "status":    "done",
            "result":    result,
        })

    # ── Plan → approval loop (with revision on rejection) ────────────────────

    MAX_PLAN_REVISIONS = 3

    async def _plan_approval_loop(self, task: dict) -> Optional[dict]:
        """
        Submits a work plan to the manager and loops on rejection:
          1. Create work plan
          2. Store + notify manager
          3. Wait for manager decision
             - approved  → return the work plan
             - rejected  → revise plan using feedback → go to 2
             - timeout   → auto-proceed (return plan as-is)
          4. After MAX_PLAN_REVISIONS rejections → block ticket and return None
        """
        task_id   = task["id"]
        ticket_id = task["ticket_id"]

        work_plan      = await self._create_work_plan(task)
        prior_feedback = None   # feedback from previous rejection round

        for revision in range(self.MAX_PLAN_REVISIONS + 1):
            attempt_label = "initial plan" if revision == 0 else f"revision #{revision}"

            store_workplan(self.role, task_id, ticket_id, work_plan)
            log_event(task_id, ticket_id, self.role, "comment",
                      f"Work plan submitted ({attempt_label}).\n"
                      f"Approach: {work_plan.get('approach','')[:200]}\n"
                      f"Deliverables: {', '.join(work_plan.get('deliverables', []))}\n"
                      f"Estimate: {work_plan.get('estimated_hours','?')}h\n"
                      f"Questions: {work_plan.get('questions_for_manager','None')}")

            await self._notify_manager_of_plan(task, work_plan, revision=revision)

            if not settings.agent_workplan_require_approval:
                return work_plan

            approved, feedback = await self._await_manager_approval(task_id, ticket_id)

            if approved:
                if feedback:
                    log_event(task_id, ticket_id, self.role, "comment",
                              f"Manager approved with notes: {feedback}")
                return work_plan

            # ── Rejected ──────────────────────────────────────────────────────
            log_event(task_id, ticket_id, self.role, "comment",
                      f"Plan rejected by manager (attempt {revision + 1}/{self.MAX_PLAN_REVISIONS}). "
                      f"Feedback: {feedback}")

            if revision >= self.MAX_PLAN_REVISIONS:
                # Exhausted all revision attempts
                update_task(task_id, status="blocked",
                            result=f"Work plan rejected {self.MAX_PLAN_REVISIONS + 1} times. "
                                   f"Last feedback: {feedback}")
                log_event(task_id, ticket_id, self.role, "status_change",
                          f"Blocked after {self.MAX_PLAN_REVISIONS + 1} rejected work plans. "
                          f"Manager escalation required.")
                return None

            # Revise the plan based on manager's feedback
            log_event(task_id, ticket_id, self.role, "comment",
                      "Revising work plan based on manager feedback…")
            work_plan = await self._revise_work_plan(task, work_plan, feedback)
            prior_feedback = feedback

        return None  # should not reach here

    # ── Work plan creation ────────────────────────────────────────────────────

    async def _create_work_plan(self, task: dict) -> dict:
        from shared.tools.plan_tools import get_pending_work_plan

        work_plan_schema = ToolRegistry.get_schemas("submit_work_plan")
        if not work_plan_schema:
            return self._default_work_plan(task)

        mem_context = await self.memory.recall(task["title"] + " " + task.get("description", ""))
        skills      = await self.memory.get_skills()
        skills_text = ("\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)) if skills else ""

        messages = [
            {"role": "system", "content": (
                f"{self.system_prompt}{self.workspace_context}{skills_text}\n\n"
                "You have just been assigned a task. Before starting, submit a detailed work plan "
                "to your manager for approval. Call submit_work_plan with your plan now.\n"
                "Be specific: exact steps, tools, deliverables, hours, and any questions/blockers."
            )},
            {"role": "user", "content": (
                f"Task: [{task['ticket_id']}] {task['title']}\n"
                f"Description: {task.get('description','')}\n"
                f"Acceptance criteria: {task.get('acceptance_criteria','')}\n"
                f"Priority: {task.get('priority','P3')}"
                + (f"\n\nRelevant memory:\n{mem_context}" if mem_context else "")
            )},
        ]

        response = await call_llm(
            messages=messages,
            provider=self.provider,
            model=self.tool_model or self.model,
            tools=work_plan_schema,
            temperature=0.2,
            max_tokens=1500,
            tool_choice="required",
        )
        for tc in (response.get("tool_calls") or []):
            args = json.loads(tc["function"].get("arguments", "{}") or "{}")
            await ToolRegistry.execute(tc["function"]["name"], args)

        return get_pending_work_plan() or self._default_work_plan(task)

    def _default_work_plan(self, task: dict) -> dict:
        return {
            "task_summary":          task["title"],
            "approach":              "Standard approach for this task type",
            "tools_needed":          self.required_tools[:5],
            "deliverables":          ["Task completed as specified"],
            "estimated_hours":       4,
            "risks_or_blockers":     "None identified",
            "questions_for_manager": "None",
        }

    # ── Revise work plan based on manager feedback ────────────────────────────

    async def _revise_work_plan(self, task: dict, previous_plan: dict, feedback: str) -> dict:
        """
        Ask the LLM to produce a new work plan that addresses the manager's feedback.
        The previous plan and the feedback are both injected into context.
        """
        from shared.tools.plan_tools import get_pending_work_plan

        work_plan_schema = ToolRegistry.get_schemas("submit_work_plan")
        if not work_plan_schema:
            return self._default_work_plan(task)

        skills      = await self.memory.get_skills()
        skills_text = ("\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)) if skills else ""

        previous_summary = (
            f"Your previous plan:\n"
            f"  Approach: {previous_plan.get('approach','')}\n"
            f"  Deliverables: {', '.join(previous_plan.get('deliverables', []))}\n"
            f"  Estimate: {previous_plan.get('estimated_hours','?')}h\n"
            f"  Questions: {previous_plan.get('questions_for_manager','None')}"
        )

        messages = [
            {"role": "system", "content": (
                f"{self.system_prompt}{self.workspace_context}{skills_text}\n\n"
                "Your manager has reviewed your work plan and rejected it. "
                "Read the feedback carefully, address every point, and submit a revised work plan. "
                "Call submit_work_plan with the updated plan."
            )},
            {"role": "user", "content": (
                f"Task: [{task['ticket_id']}] {task['title']}\n"
                f"Description: {task.get('description','')}\n"
                f"Acceptance criteria: {task.get('acceptance_criteria','')}\n\n"
                f"{previous_summary}\n\n"
                f"MANAGER FEEDBACK (you MUST address all of this):\n{feedback}\n\n"
                "Revise your plan to address the feedback. Be specific about what you changed and why."
            )},
        ]

        response = await call_llm(
            messages=messages,
            provider=self.provider,
            model=self.tool_model or self.model,
            tools=work_plan_schema,
            temperature=0.2,
            max_tokens=1500,
            tool_choice="required",
        )
        for tc in (response.get("tool_calls") or []):
            args = json.loads(tc["function"].get("arguments", "{}") or "{}")
            await ToolRegistry.execute(tc["function"]["name"], args)

        revised = get_pending_work_plan()
        if not revised:
            # Fallback: patch the previous plan with a note
            revised = dict(previous_plan)
            revised["approach"] = f"[REVISED per feedback: {feedback[:200]}]\n\n" + previous_plan.get("approach", "")
        return revised

    # ── Notify manager of pending work plan ───────────────────────────────────

    async def _notify_manager_of_plan(self, task: dict, work_plan: dict, revision: int = 0):
        label = "initial plan" if revision == 0 else f"revision #{revision}"
        try:
            await bus.publish("manager", {
                "type":         "workplan_pending_review",
                "from_role":    self.role,
                "task_id":      task["id"],
                "ticket_id":    task["ticket_id"],
                "plan_summary": work_plan.get("task_summary", task["title"]),
                "questions":    work_plan.get("questions_for_manager", "None"),
                "revision":     revision,
                "label":        label,
            })
        except Exception as e:
            log.warning(f"[{self.role}] Could not notify manager: {e}")

    # ── Wait for manager approval (polls Redis) ───────────────────────────────

    async def _await_manager_approval(
        self, task_id: str, ticket_id: str
    ) -> tuple[bool, str]:
        """
        Poll Redis until the manager approves/rejects this work plan.
        Returns (approved: bool, feedback: str).
        Auto-proceeds (approved=True) after WORKPLAN_APPROVAL_TIMEOUT seconds.
        """
        elapsed  = 0
        attempts = 0

        log.info(f"[{self.role}] Waiting for manager approval on task {task_id}…")

        while elapsed < WORKPLAN_APPROVAL_TIMEOUT:
            await asyncio.sleep(WORKPLAN_POLL_INTERVAL)
            elapsed  += WORKPLAN_POLL_INTERVAL
            attempts += 1

            plan = get_workplan(self.role, task_id)
            if not plan:
                # Plan was deleted — auto-proceed
                break

            status   = plan.get("status", "pending_manager_review")
            feedback = plan.get("feedback", "")

            if status == "approved":
                log.info(f"[{self.role}] Work plan approved by manager")
                return True, feedback

            if status == "rejected":
                log.warning(f"[{self.role}] Work plan rejected: {feedback}")
                return False, feedback

            # Still pending — log a heartbeat every 2 minutes
            if attempts % 8 == 0:
                log_event(task_id, ticket_id, self.role, "comment",
                          f"Still waiting for manager approval ({elapsed//60}m elapsed)")

        # Timeout — auto-proceed and log
        log.info(f"[{self.role}] Work plan approval timeout — auto-proceeding")
        log_event(task_id, ticket_id, self.role, "comment",
                  f"No manager response after {WORKPLAN_APPROVAL_TIMEOUT//60}m — auto-proceeding")
        return True, ""

    # ── Ask manager ───────────────────────────────────────────────────────────

    async def _ask_manager(self, task_id: str, ticket_id: str, questions: str):
        log_event(task_id, ticket_id, self.role, "comment",
                  f"[QUESTION FOR MANAGER] {questions}")
        try:
            await bus.publish("manager", {
                "type":      "agent_question",
                "from_role": self.role,
                "task_id":   task_id,
                "ticket_id": ticket_id,
                "question":  questions,
            })
        except Exception as e:
            log.warning(f"[{self.role}] Could not publish question to manager: {e}")

    # ── ReAct loop ────────────────────────────────────────────────────────────

    async def run(self, task_id: str) -> str:
        task = get_task(task_id)
        if not task:
            return "Task not found."
        goal = f"Ticket: {task['ticket_id']}\nTitle: {task['title']}\n\n{task['description']}"
        if task.get("acceptance_criteria"):
            goal += f"\n\nAcceptance criteria:\n{task['acceptance_criteria']}"
        mem_context = await self.memory.recall(task["title"] + " " + task.get("description", ""))
        skills      = await self.memory.get_skills()
        skills_text = ("\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)) if skills else ""
        messages = [{"role": "system", "content": self.system_prompt + self.workspace_context + skills_text}]
        if mem_context:
            messages.append({"role": "system", "content": mem_context})
        messages.append({"role": "user", "content": f"Your goal:\n{goal}"})
        return await self._react_loop(messages, task_id)

    async def _react_loop(self, messages: list[dict], task_id: Optional[str] = None,
                          max_steps: Optional[int] = None) -> str:
        if max_steps is None:
            max_steps = settings.agent_max_react_steps
        task      = get_task(task_id) if task_id else None
        ticket_id = task["ticket_id"] if task else (task_id or "adhoc")

        def _log(msg):
            if task_id:
                log_event(task_id, ticket_id, self.role, "comment", msg)

        for step in range(max_steps):
            _log(f"Step {step+1}")
            tc_mode  = "required" if (step == 0 and self.tools) else "auto"
            response = await call_llm(
                messages=messages,
                provider=self.provider,
                model=self.tool_model or self.model,
                tools=self.tools or None,
                temperature=0.2,
                tool_choice=tc_mode,
            )
            content    = response.get("content") or ""
            tool_calls = response.get("tool_calls")
            asst_msg   = {"role": "assistant", "content": content}
            if tool_calls:
                asst_msg["tool_calls"] = tool_calls
            messages.append(asst_msg)
            if not tool_calls:
                return content or "(task complete, no output)"
            for tc in tool_calls:
                fn   = tc["function"]["name"]
                args = json.loads(tc["function"].get("arguments", "{}") or "{}")
                _log(f"Tool: {fn}")
                result = await ToolRegistry.execute(fn, args)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name":         fn,
                    "content":      str(result),
                })
        return next((m["content"] for m in reversed(messages)
                     if m["role"] == "assistant" and m.get("content")), "Max steps reached.")

    # ── Direct Q&A ────────────────────────────────────────────────────────────

    async def ask(self, question: str) -> str:
        mem    = await self.memory.recall(question)
        skills = await self.memory.get_skills()
        sys    = self.system_prompt
        if skills:
            sys += "\nYour skills:\n" + "\n".join(f"• {s}" for s in skills)
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
        mem  = await self.memory.recall("daily work priorities")
        plan = await self.ask(
            f"Today: {datetime.utcnow().date()}\nOpen tasks:\n{tasks_text}"
            f"\n{'Context: ' + mem if mem else ''}"
            f"\nWrite a short, realistic daily work plan as a {self.role}."
        )
        save_daily_plan(self.role, plan)
        save_standup(self.role, "See previous day's results", plan[:300], "None")
        await self.memory.remember(f"Daily plan {datetime.utcnow().date()}: {plan[:200]}")
        return plan
