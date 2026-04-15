"""
shared/core/plan_store.py — Redis persistence for ProjectPlans.

Plans live at:  plan:{plan_id}   (Redis Hash, JSON-encoded fields)
Index:          plans:index      (Redis Sorted Set, scored by creation timestamp)
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Optional

import redis

from shared.core.config import settings

_redis: Optional[redis.Redis] = None


def _r() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# ─── CRUD ─────────────────────────────────────────────────────────────────────

def new_plan_id() -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"plan_{stamp}_{short}"


def save_plan(plan_id: str, plan: dict) -> None:
    """Persist a plan dict to Redis. Overwrites if already exists."""
    r = _r()
    plan["plan_id"]    = plan_id
    plan["updated_at"] = datetime.utcnow().isoformat()
    if "created_at" not in plan:
        plan["created_at"] = plan["updated_at"]
    # Store as a single JSON blob in a hash field
    r.hset(f"plan:{plan_id}", "data", json.dumps(plan))
    # Add to sorted-set index scored by creation time
    r.zadd("plans:index", {plan_id: time.time()})
    # Expire after 30 days
    r.expire(f"plan:{plan_id}", 60 * 60 * 24 * 30)


def load_plan(plan_id: str) -> Optional[dict]:
    """Load a plan by ID. Returns None if not found."""
    r = _r()
    raw = r.hget(f"plan:{plan_id}", "data")
    if not raw:
        return None
    return json.loads(raw)


def update_plan_status(plan_id: str, status: str) -> bool:
    """Update only the status field of an existing plan."""
    plan = load_plan(plan_id)
    if not plan:
        return False
    plan["status"] = status
    save_plan(plan_id, plan)
    return True


def list_plans(limit: int = 20, status_filter: Optional[str] = None) -> list[dict]:
    """Return the N most recent plans (newest first), optionally filtered by status."""
    r = _r()
    # Get IDs newest-first from sorted set
    ids = r.zrevrange("plans:index", 0, limit * 3)  # over-fetch for filtering
    plans = []
    for pid in ids:
        p = load_plan(pid)
        if p is None:
            continue
        if status_filter and p.get("status") != status_filter:
            continue
        plans.append(p)
        if len(plans) >= limit:
            break
    return plans


def delete_plan(plan_id: str) -> bool:
    r = _r()
    deleted = r.delete(f"plan:{plan_id}")
    r.zrem("plans:index", plan_id)
    return bool(deleted)


def plan_summary(plan: dict) -> dict:
    """Return a lightweight summary dict safe to return in list endpoints."""
    phase_count = len(plan.get("phases", []))
    task_count  = sum(len(ph.get("tasks", [])) for ph in plan.get("phases", []))
    return {
        "plan_id":             plan.get("plan_id"),
        "project_name":        plan.get("project_name"),
        "goal_summary":        plan.get("goal_summary", "")[:120],
        "status":              plan.get("status"),
        "total_timeline_days": plan.get("total_timeline_days"),
        "phase_count":         phase_count,
        "task_count":          task_count,
        "created_at":          plan.get("created_at"),
        "updated_at":          plan.get("updated_at"),
    }
