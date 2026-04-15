"""
services/manager/scheduler.py — APScheduler for autonomous 24/7 operation.

Jobs:
  09:00 daily        → Standup: each agent reports Yesterday/Today/Blockers
  Every 30 min       → Delegation loop: assign pending tasks to agents
  Every hour         → SLA check: alert on overdue P1/P2 tickets
  17:00 daily        → Daily report compiled and sent to Teams + email
  Monday 09:00       → Weekly report sent to Teams + email
  Every 5 min        → Health check: verify all agent services alive
"""
from __future__ import annotations

import asyncio
import httpx
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from shared.core.config import settings
from shared.core.database import get_tasks, update_task

log = logging.getLogger("scheduler")

AGENT_SERVICES = {
    "manager":   "http://manager:8001",
    "developer": "http://developer:8002",
    "devops":    "http://devops:8003",
    "qa":        "http://qa:8004",
    "support":   "http://support:8005",
    "docs":      "http://docs:8006",
    "design":    "http://design:8007",
    "ux":        "http://ux:8008",
    "ui_test":   "http://ui_test:8009",
    "api_test":  "http://api_test:8010",
    "qa_auto":   "http://qa_auto:8011",
    "security":  "http://security:8012",
}


async def _ask_agent(role: str, question: str, timeout: int = 60) -> str:
    """Ask a specific agent a question, return its answer."""
    url = AGENT_SERVICES.get(role, "") + "/ask"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json={"question": question})
            data = r.json()
            return data.get("answer", "") or str(data)
    except Exception as e:
        return f"[{role} unavailable: {e}]"


async def _post_to_teams(message: str) -> None:
    """Post a message to Microsoft Teams via Incoming Webhook."""
    webhook_url = settings.teams_webhook_url
    if not webhook_url:
        log.warning("TEAMS_WEBHOOK_URL not configured — skipping Teams notification")
        return
    try:
        # Microsoft Teams Adaptive Card format
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "0076D7",
            "summary": message[:100],
            "sections": [{
                "activityTitle": "AI Agent Team",
                "activityText": message,
                "markdown": True
            }]
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(webhook_url, json=payload)
            if r.status_code not in (200, 202):
                log.warning(f"Teams webhook returned {r.status_code}")
    except Exception as e:
        log.error(f"Teams notification failed: {e}")


async def _send_email(subject: str, body: str) -> None:
    """Send email notification."""
    if not settings.notify_email or not settings.smtp_user:
        log.debug("Email not configured — skipping email notification")
        return
    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_user
        msg["To"] = settings.notify_email
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )
        log.info(f"Email sent: {subject}")
    except Exception as e:
        log.error(f"Email failed: {e}")


# ── Scheduled Jobs ─────────────────────────────────────────────────────────────

async def job_daily_standup() -> None:
    """
    09:00 daily — Every agent reports their standup.
    Format: Yesterday | Today | Blockers
    Posts to Teams and emails summary.
    """
    log.info("📅 Running 9am standup")
    today = datetime.utcnow().strftime("%A %d %B %Y")
    all_tasks = get_tasks(limit=500)

    standups = {}
    roles = [r for r in AGENT_SERVICES if r != "manager"]

    async def get_standup(role: str) -> tuple[str, str]:
        done_yesterday = [
            t for t in all_tasks
            if t.get("assigned_to") == role
            and t.get("status") == "done"
            and t.get("completed_at")
            and (datetime.utcnow() - datetime.fromisoformat(str(t["completed_at"]).replace("Z", "+00:00").replace("+00:00", ""))).days < 1
        ]
        in_progress = [t for t in all_tasks if t.get("assigned_to") == role and t.get("status") == "in_progress"]
        pending = [t for t in all_tasks if t.get("assigned_to") == role and t.get("status") == "pending"]
        blocked = [t for t in all_tasks if t.get("assigned_to") == role and t.get("status") == "blocked"]

        yesterday_text = ", ".join(t["ticket_id"] + " " + t["title"][:40] for t in done_yesterday) or "No completions yesterday"
        today_text = ", ".join(t["ticket_id"] + " " + t["title"][:40] for t in in_progress[:3]) or "Picking up next pending tasks"
        blockers_text = ", ".join(t["ticket_id"] + " " + t["title"][:40] for t in blocked) or "None"

        answer = await _ask_agent(role,
            f"Write a 3-line standup for today {today}:\n"
            f"Yesterday completed: {yesterday_text}\n"
            f"Today working on: {today_text}\n"
            f"Blockers: {blockers_text}\n"
            f"Format EXACTLY as:\n**Yesterday:** ...\n**Today:** ...\n**Blockers:** ..."
        )
        return role, answer

    results = await asyncio.gather(*[get_standup(r) for r in roles], return_exceptions=True)
    for item in results:
        if isinstance(item, tuple):
            role, answer = item
            standups[role] = answer

    # Format Teams message
    lines = [f"## 🌅 Daily Standup — {today}\n"]
    role_emojis = {
        "developer": "🧑‍💻", "devops": "⚙️", "qa": "🧪",
        "support": "🎧", "docs": "📝", "design": "🎨",
        "ux": "🗺️", "ui_test": "🖥️", "api_test": "🔌",
        "qa_auto": "🤖", "security": "🔒"
    }
    for role, standup in standups.items():
        emoji = role_emojis.get(role, "👤")
        lines.append(f"### {emoji} {role.replace('_', ' ').title()}")
        lines.append(standup or "_No update_")
        lines.append("")

    full_standup = "\n".join(lines)
    log.info(f"Standup generated for {len(standups)} agents")

    await asyncio.gather(
        _post_to_teams(full_standup),
        _send_email(f"🌅 Daily Standup — {today}", full_standup),
        return_exceptions=True
    )


async def job_delegation_loop() -> None:
    """Every 30 min — Manager picks up pending tasks and delegates."""
    log.info("🔄 Delegation loop running")
    pending = get_tasks(status="pending", limit=20)
    if not pending:
        log.debug("No pending tasks to delegate")
        return
    for task in pending:
        role = task.get("assigned_to")
        if not role or role not in AGENT_SERVICES:
            continue
        log.info(f"  → Delegating {task['ticket_id']} to {role}")
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    AGENT_SERVICES[role] + "/ask",
                    json={"question": f"Please start working on task {task['ticket_id']}: {task['title']}"}
                )
        except Exception as e:
            log.warning(f"  Failed to notify {role}: {e}")


async def job_sla_check() -> None:
    """Every hour — Alert on SLA-breached tasks."""
    all_tasks = get_tasks(status="in_progress", limit=100)
    sla_hours = {"P1": 1, "P2": 4, "P3": 24, "P4": 72}
    breached = []
    now = datetime.utcnow()

    for task in all_tasks:
        priority = task.get("priority", "P3")
        created_raw = task.get("created_at")
        if not created_raw:
            continue
        try:
            created = datetime.fromisoformat(str(created_raw).replace("Z", "").split("+")[0])
            age_hours = (now - created).total_seconds() / 3600
            if age_hours > sla_hours.get(priority, 24):
                breached.append(task)
        except Exception:
            continue

    if breached:
        msg = f"⚠️ **SLA Breach Alert** — {len(breached)} overdue tasks:\n"
        for t in breached:
            msg += f"- [{t['priority']}] {t['ticket_id']} → {t['assigned_to']}: {t['title'][:60]}\n"
        log.warning(msg)
        await _post_to_teams(msg)


async def job_daily_report() -> None:
    """17:00 daily — Compile and send daily report."""
    log.info("📧 Building daily report")
    today = datetime.utcnow().strftime("%A %d %B %Y")
    all_tasks = get_tasks(limit=500)

    done_today = [t for t in all_tasks if t.get("status") == "done"]
    in_progress = [t for t in all_tasks if t.get("status") == "in_progress"]
    failed = [t for t in all_tasks if t.get("status") == "failed"]
    blocked = [t for t in all_tasks if t.get("status") == "blocked"]
    pending = [t for t in all_tasks if t.get("status") == "pending"]

    lines = [
        f"## 📊 Daily Report — {today}",
        "",
        f"| Status | Count |",
        f"|--------|-------|",
        f"| ✅ Done | {len(done_today)} |",
        f"| 🔵 In Progress | {len(in_progress)} |",
        f"| ❌ Failed | {len(failed)} |",
        f"| 🚫 Blocked | {len(blocked)} |",
        f"| 📋 Pending | {len(pending)} |",
        "",
    ]
    if done_today:
        lines.append("### ✅ Completed Today")
        for t in done_today[:10]:
            lines.append(f"- **{t['ticket_id']}** [{t['assigned_to']}] {t['title'][:60]}")
    if blocked:
        lines.append("\n### 🚫 Blocked — Needs Attention")
        for t in blocked:
            lines.append(f"- **{t['ticket_id']}** [{t['priority']}] {t['title'][:60]} → {t['assigned_to']}")

    report = "\n".join(lines)
    await asyncio.gather(
        _post_to_teams(report),
        _send_email(f"📊 Daily Report — {today}", report),
        return_exceptions=True
    )


async def job_weekly_report() -> None:
    """Monday 09:00 — Weekly sprint recap."""
    log.info("📈 Building weekly report")
    week = datetime.utcnow().strftime("W%W %Y")
    all_tasks = get_tasks(limit=1000)

    by_agent: dict[str, dict] = {}
    for t in all_tasks:
        role = t.get("assigned_to", "unknown")
        if role not in by_agent:
            by_agent[role] = {"done": 0, "failed": 0, "in_progress": 0, "pending": 0}
        status = t.get("status", "pending")
        if status in by_agent[role]:
            by_agent[role][status] += 1

    lines = [f"## 📈 Weekly Report — {week}", "", "| Engineer | Done | In Progress | Failed | Pending |",
             "|----------|------|-------------|--------|---------|"]
    for role, stats in sorted(by_agent.items()):
        lines.append(f"| {role} | {stats['done']} | {stats['in_progress']} | {stats['failed']} | {stats['pending']} |")

    report = "\n".join(lines)
    await asyncio.gather(
        _post_to_teams(report),
        _send_email(f"📈 Weekly Report — {week}", report),
        return_exceptions=True
    )


IDLE_LEARNING_TASKS: dict[str, list[str]] = {
    "developer": [
        "Review the codebase for any TODO/FIXME comments and create tickets for the important ones.",
        "Audit dependencies for outdated packages and propose upgrade candidates.",
        "Identify any functions longer than 50 lines that could be refactored for clarity.",
        "Write or improve unit tests for the lowest-covered modules.",
        "Review recent merged code for potential performance bottlenecks.",
    ],
    "devops": [
        "Review all Docker images for unnecessary layers and propose optimisations.",
        "Audit environment variable usage — identify any secrets that should move to a vault.",
        "Check log retention policies and alert thresholds are correctly configured.",
        "Verify all services have health-check endpoints and restart policies.",
        "Review CI pipeline run times and identify the slowest steps.",
    ],
    "qa": [
        "Review open bugs older than 7 days and update their priority/status.",
        "Identify test cases that have been flaky recently and investigate root causes.",
        "Audit the regression suite for duplicate or redundant test cases.",
        "Propose new edge-case tests for the most-changed modules this week.",
        "Verify that all P1 bugs have been closed or have active assignees.",
    ],
    "security": [
        "Run a dependency vulnerability scan and report any CVEs above CVSS 7.0.",
        "Review authentication flows for any missing rate-limiting or brute-force protections.",
        "Check all API endpoints for missing authorisation middleware.",
        "Audit logging to ensure no sensitive data (passwords, tokens) is logged.",
        "Review CORS and CSP headers for the current production configuration.",
    ],
    "docs": [
        "Identify API endpoints that have no documentation and draft stubs.",
        "Review existing docs for outdated screenshots or references to renamed features.",
        "Create a glossary entry for any new technical terms introduced this sprint.",
        "Improve the README onboarding section based on common setup questions.",
        "Write a runbook for the most recent production incident.",
    ],
    "support": [
        "Compile a FAQ from the top 10 most repeated customer questions this week.",
        "Review all P1 support tickets closed this week and extract common root causes.",
        "Draft canned responses for the 5 most common issue categories.",
        "Identify any product pain points that have come up 3+ times and create a feature request.",
        "Update the support knowledge base with solutions from recently closed tickets.",
    ],
    "design": [
        "Audit the design system for any components that lack dark-mode variants.",
        "Review all current screens for inconsistent spacing or typography.",
        "Create design tokens documentation for spacing, colour, and type scales.",
        "Identify UX improvements from the top 5 recent support complaints.",
        "Explore accessibility improvements — check colour contrast ratios across key screens.",
    ],
    "ux": [
        "Analyse the most-used user flows and identify friction points.",
        "Map the current onboarding journey and flag drop-off risks.",
        "Create a user journey map for the top 3 user personas.",
        "Review error messages across the app for clarity and actionability.",
        "Propose A/B test hypotheses for the feature with the lowest engagement.",
    ],
    "ui_test": [
        "Audit existing Playwright tests for any that rely on fragile selectors and refactor them.",
        "Write tests for any new UI components added in the last sprint.",
        "Run a full accessibility audit with axe-core and report violations.",
        "Check all form validation states (empty, invalid, server error) have test coverage.",
        "Verify responsive breakpoints on the 3 most-used pages.",
    ],
    "api_test": [
        "Audit API test coverage — identify endpoints with no automated tests.",
        "Run contract tests against the latest API schema and report any drift.",
        "Add negative test cases (invalid auth, missing fields, wrong types) for untested endpoints.",
        "Review all test data fixtures for stale or environment-specific values.",
        "Load-test the top 5 most frequently called endpoints and report baseline metrics.",
    ],
    "qa_auto": [
        "Review the CI test pipeline for any jobs that could run in parallel to reduce total time.",
        "Identify flaky tests in the last 20 CI runs and file tickets.",
        "Update test environment setup scripts to remove manual steps.",
        "Create a test coverage report and highlight modules below 60%.",
        "Propose automation for the top 3 manual test cases still in the regression pack.",
    ],
}


async def job_idle_work() -> None:
    """
    Every 20 min — Give learning / maintenance tasks to idle agents.
    An agent is idle if it has no in_progress tasks.
    """
    import random
    log.info("💤 Checking for idle agents")
    all_tasks = get_tasks(status="in_progress", limit=200)
    busy_roles = {t.get("assigned_to") for t in all_tasks if t.get("assigned_to")}

    roles = [r for r in AGENT_SERVICES if r != "manager"]
    idle_roles = [r for r in roles if r not in busy_roles]

    if not idle_roles:
        log.debug("All agents are busy — no idle work needed")
        return

    log.info(f"  Idle agents: {idle_roles}")

    async def assign_idle_task(role: str) -> None:
        tasks_for_role = IDLE_LEARNING_TASKS.get(role, [
            "Review your recent work, identify any improvements you can make, and document your findings."
        ])
        task = random.choice(tasks_for_role)
        prompt = (
            f"You currently have no active tasks assigned. Use this idle time productively.\n\n"
            f"**Idle Learning Task:** {task}\n\n"
            f"Work through this task thoroughly. Record any findings, tickets, or improvements "
            f"you identify. When done, summarise what you accomplished."
        )
        log.info(f"  → Assigning idle task to {role}: {task[:60]}…")
        await _ask_agent(role, prompt, timeout=120)

    await asyncio.gather(*[assign_idle_task(r) for r in idle_roles], return_exceptions=True)
    log.info(f"  Idle tasks dispatched to {len(idle_roles)} agents")


async def job_health_check() -> None:
    """Every 5 min — Verify all agent services are reachable."""
    down = []
    async with httpx.AsyncClient(timeout=4) as c:
        for role, url in AGENT_SERVICES.items():
            try:
                r = await c.get(url + "/health")
                if r.status_code >= 400:
                    down.append(f"{role} (HTTP {r.status_code})")
            except Exception as e:
                down.append(f"{role} (unreachable)")
    if down:
        msg = f"🔴 **Health Alert** — {len(down)} services down: {', '.join(down)}"
        log.error(msg)
        await _post_to_teams(msg)
    else:
        log.debug("✅ All services healthy")


# ── Scheduler factory ──────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the APScheduler instance."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        job_daily_standup,
        CronTrigger(hour=9, minute=0),
        id="daily_standup",
        name="9am Daily Standup → Teams",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_delegation_loop,
        IntervalTrigger(minutes=30),
        id="delegation_loop",
        name="Task Delegation Loop",
        replace_existing=True,
    )
    scheduler.add_job(
        job_sla_check,
        IntervalTrigger(hours=1),
        id="sla_check",
        name="SLA Breach Check",
        replace_existing=True,
    )
    scheduler.add_job(
        job_daily_report,
        CronTrigger(hour=17, minute=0),
        id="daily_report",
        name="5pm Daily Report → Teams + Email",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_weekly_report,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_report",
        name="Monday Weekly Report → Teams + Email",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_health_check,
        IntervalTrigger(minutes=5),
        id="health_check",
        name="5-min Health Check",
        replace_existing=True,
    )
    scheduler.add_job(
        job_idle_work,
        IntervalTrigger(minutes=20),
        id="idle_work",
        name="Idle Agent Learning Tasks",
        replace_existing=True,
    )

    return scheduler
