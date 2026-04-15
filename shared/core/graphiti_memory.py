"""
shared/core/graphiti_memory.py — Per-agent persistent memory using Graphiti + Neo4j.

Drop-in replacement for AgentMemory that uses Graphiti (graph-structured memory)
with Neo4j as the backend. Facts are stored as episodes in the graph.
For skills and context, uses local dict fallback (Graphiti doesn't have native KV).

If Graphiti is unavailable, gracefully falls back to Redis-based AgentMemory.
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from pathlib import Path

try:
    from graphiti_core import Graphiti
    HAS_GRAPHITI = True
except ImportError:
    HAS_GRAPHITI = False

from shared.core.config import settings
from shared.core.memory import AgentMemory

log = logging.getLogger("memory_graphiti")


class GraphitiMemory:
    """
    Per-agent memory using Graphiti + Neo4j for graph-structured storage.
    Implements the same interface as AgentMemory for drop-in replacement.

    Usage:
        mem = GraphitiMemory("developer")
        await mem.connect()
        context = await mem.recall(query="python async patterns")
        await mem.remember("Use asyncio.gather for parallel tool calls")
    """

    def __init__(
        self,
        agent_role: str,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
    ):
        self.role = agent_role
        self.neo4j_uri = neo4j_uri or settings.__dict__.get(
            "neo4j_uri", "bolt://neo4j:7687"
        )
        self.neo4j_user = neo4j_user or settings.__dict__.get("neo4j_user", "neo4j")
        self.neo4j_password = neo4j_password or settings.__dict__.get(
            "neo4j_password", "password"
        )

        self._graphiti: Graphiti | None = None
        self._fallback_memory: AgentMemory | None = None

        # Local dicts for skills and context (Graphiti doesn't have native KV)
        self._skills: list[str] = []
        self._context: dict[str, str] = {}

    async def connect(self):
        """Initialize Graphiti connection, with fallback to Redis if unavailable."""
        if not HAS_GRAPHITI:
            log.warning(
                f"[{self.role}] Graphiti not installed; falling back to Redis AgentMemory"
            )
            self._fallback_memory = AgentMemory(self.role)
            await self._fallback_memory.connect()
            return

        try:
            # Initialize Graphiti with Neo4j backend
            self._graphiti = Graphiti(
                graphiti_host=self.neo4j_uri,
                user_id=self.role,  # Partition by role
            )
            log.info(f"[{self.role}] Connected to Graphiti @ {self.neo4j_uri}")
        except Exception as e:
            log.error(
                f"[{self.role}] Failed to connect to Graphiti: {e}. "
                "Falling back to Redis AgentMemory"
            )
            self._fallback_memory = AgentMemory(self.role)
            await self._fallback_memory.connect()

    @property
    def _is_available(self) -> bool:
        """Check if Graphiti is available and connected."""
        return self._graphiti is not None

    # ── Long-term memory: important learnings ─────────────────────────────────

    async def remember(self, fact: str, importance: float = 1.0):
        """Save an important fact to long-term memory (as a Graphiti episode)."""
        # Fallback check
        if hasattr(self, '_fallback_memory') and self._fallback_memory:
            return await self._fallback_memory.remember(fact, importance)

        if not self._is_available:
            return

        try:
            # Store as an episode in Graphiti
            self._graphiti.add_episode(
                user_id=self.role,
                episode_body=fact,
                metadata={"importance": importance},
            )
            log.debug(f"[{self.role}] 🧠 Remembered: {fact[:80]}")
        except Exception as e:
            log.error(f"[{self.role}] Failed to remember fact: {e}")
            if self._fallback_memory:
                await self._fallback_memory.remember(fact, importance)

    async def recall(self, query: str = "", limit: int = 10) -> str:
        """
        Retrieve relevant memories from Graphiti or fallback.
        If query provided, search by text; otherwise return top memories.
        """
        # Fallback check
        if hasattr(self, '_fallback_memory') and self._fallback_memory:
            return await self._fallback_memory.recall(query, limit)

        if not self._is_available:
            return ""

        try:
            if query:
                # Search for episodes matching the query
                results = self._graphiti.search(
                    user_id=self.role,
                    query=query,
                    top_k=limit,
                )
                if not results:
                    return ""

                # Extract episode bodies from results
                facts = [r.get("body", "") for r in results if r.get("body")]
            else:
                # Get all episodes for this role (no search query)
                # Note: Graphiti API may vary; fallback handles gracefully
                facts = []
                try:
                    episodes = self._graphiti.search(
                        user_id=self.role,
                        query="",
                        top_k=limit,
                    )
                    if episodes:
                        facts = [e.get("body", "") for e in episodes if e.get("body")]
                except Exception:
                    pass

            if not facts:
                return ""
            return "Relevant memories:\n" + "\n".join(f"• {f}" for f in facts[:limit])
        except Exception as e:
            log.error(f"[{self.role}] Failed to recall: {e}")
            if self._fallback_memory:
                return await self._fallback_memory.recall(query, limit)
            return ""

    # ── Working memory: current session context ───────────────────────────────

    async def set_context(self, key: str, value: str, ttl_seconds: int = 604800):
        """Store a context value (local dict; TTL not enforced in Graphiti fallback)."""
        self._context[key] = value
        if self._fallback_memory:
            await self._fallback_memory.set_context(key, value, ttl_seconds)

    async def get_context(self, key: str) -> Optional[str]:
        """Retrieve a context value."""
        if key in self._context:
            return self._context[key]
        if self._fallback_memory:
            return await self._fallback_memory.get_context(key)
        return None

    async def get_all_context(self) -> dict:
        """Get all context values."""
        if self._fallback_memory:
            return await self._fallback_memory.get_all_context()
        return self._context.copy()

    # ── Skills store: agent's curated skill set ───────────────────────────────

    async def load_skills(self, skills: list[str]):
        """Load the agent's built-in skills into memory on startup."""
        if not skills:
            return
        if not self._skills:  # Only load once
            self._skills = skills.copy()
            log.info(f"[{self.role}] 📚 {len(skills)} skills loaded")
        if self._fallback_memory:
            await self._fallback_memory.load_skills(skills)

    async def get_skills(self) -> list[str]:
        """Get the agent's skills."""
        return self._skills.copy()

    async def add_skill(self, skill: str):
        """Agent can learn a new skill during operation."""
        if skill not in self._skills:
            self._skills.append(skill)
            log.info(f"[{self.role}] ✨ New skill acquired: {skill[:60]}")
        if self._fallback_memory:
            await self._fallback_memory.add_skill(skill)

    # ── Summary ───────────────────────────────────────────────────────────────

    async def summary(self) -> dict:
        """Get a summary of the agent's memory state."""
        # Fallback check
        if hasattr(self, '_fallback_memory') and self._fallback_memory:
            return await self._fallback_memory.summary()

        if not self._is_available:
            return {
                "role": self.role,
                "ltm_memories": 0,
                "context_keys": 0,
                "skills_count": 0,
                "skills": [],
            }

        try:
            # Try to get episode count from Graphiti
            # Note: Graphiti API may not expose direct count; estimate from search
            ltm_count = 0
            try:
                sample = self._graphiti.search(user_id=self.role, query="", top_k=1000)
                ltm_count = len(sample) if sample else 0
            except Exception:
                pass

            return {
                "role": self.role,
                "ltm_memories": ltm_count,
                "context_keys": len(self._context),
                "skills_count": len(self._skills),
                "skills": self._skills.copy(),
            }
        except Exception as e:
            log.error(f"[{self.role}] Failed to generate summary: {e}")
            if self._fallback_memory:
                return await self._fallback_memory.summary()
            return {
                "role": self.role,
                "ltm_memories": 0,
                "context_keys": 0,
                "skills_count": 0,
                "skills": [],
            }
