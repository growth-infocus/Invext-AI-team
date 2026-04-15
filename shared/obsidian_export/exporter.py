"""
shared/obsidian_export/exporter.py

Converts agent memories into Obsidian-compatible markdown notes.
Supports both Redis-backed AgentMemory and Graphiti-backed GraphitiMemory.

Each agent's memories are organized into a separate Obsidian vault folder.
Facts become notes with wiki-links for entity connections.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import redis.asyncio as aioredis

from shared.core.config import settings

log = logging.getLogger("obsidian_export")


def _sanitize_filename(s: str) -> str:
    """Make a string safe to use as a file/folder name."""
    s = re.sub(r"[^\w\s\-]", "", s)
    return s.strip()[:80] or "untitled"


def _extract_entities(fact: str) -> list[str]:
    """
    Naive entity extraction: capitalised words become Obsidian wiki-links.
    A real implementation would use spaCy/NER.
    """
    words = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", fact)
    return list(dict.fromkeys(words))  # deduplicate, preserve order


def _fact_to_markdown(fact_text: str, uuid: Optional[str] = None) -> str:
    """Convert a single fact → markdown note content."""
    created = datetime.now(tz=timezone.utc).isoformat()
    uuid = uuid or f"fact_{datetime.now().timestamp()}"
    entities = _extract_entities(fact_text)

    # Replace entity mentions with wiki-links
    linked_text = fact_text
    for entity in entities:
        linked_text = linked_text.replace(entity, f"[[{entity}]]")

    lines = [
        "---",
        f'uuid: "{uuid}"',
        f'created: "{created}"',
        f'tags: [memory, agent]',
        "---",
        "",
        f"# {_sanitize_filename(fact_text[:60])}",
        "",
        linked_text,
        "",
    ]
    if entities:
        lines += ["## Related Entities", ""] + [f"- [[{e}]]" for e in entities] + [""]

    return "\n".join(lines)


async def export_from_redis_memory(
    role: str,
    redis_url: Optional[str] = None,
    vault_path: Optional[str] = None,
) -> int:
    """
    Export memories from Redis-backed AgentMemory.
    Reads from memory:ltm:{role} (sorted set) and memory:skills:{role} (list).
    Returns the number of files written.
    """
    redis_url = redis_url or settings.redis_url
    if not redis_url:
        log.error("No Redis URL configured; cannot export memories")
        return 0

    vault_path = vault_path or settings.__dict__.get("obsidian_vault_path", "")
    if not vault_path:
        vault_path = str(
            Path(__file__).parent.parent.parent / "obsidian_vault" / role
        )

    try:
        r = await aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)

        # Read LTM (long-term memory)
        ltm_key = f"memory:ltm:{role}"
        entries = await r.zrevrange(ltm_key, 0, 99, withscores=False)
        facts = []
        for entry in entries:
            try:
                data = json.loads(entry)
                facts.append(data["fact"])
            except (json.JSONDecodeError, KeyError):
                # Fallback: treat entry as plain fact
                facts.append(entry)

        await r.close()
    except Exception as e:
        log.error(f"[{role}] Failed to read from Redis: {e}")
        return 0

    # Write to Obsidian vault
    folder = Path(vault_path)
    folder.mkdir(parents=True, exist_ok=True)

    written = 0
    for idx, fact in enumerate(facts):
        if not fact or not fact.strip():
            continue
        content = _fact_to_markdown(fact, uuid=f"{role}_fact_{idx}")
        fname = _sanitize_filename(f"{idx:03d}_{fact[:50]}") + ".md"
        try:
            (folder / fname).write_text(content, encoding="utf-8")
            written += 1
        except Exception as e:
            log.error(f"[{role}] Failed to write {fname}: {e}")

    # Create index note
    _write_index_note(folder, role, facts)

    log.info(f"[{role}] Exported {written} memories to {vault_path}")
    return written


async def export_from_graphiti_memory(
    role: str,
    graphiti_obj=None,
    vault_path: Optional[str] = None,
) -> int:
    """
    Export memories from Graphiti-backed GraphitiMemory.
    If graphiti_obj is None, tries to connect; if connection fails, returns 0.
    Returns the number of files written.
    """
    try:
        from graphiti_core import Graphiti
    except ImportError:
        log.warning("Graphiti not installed; skipping Graphiti export")
        return 0

    if graphiti_obj is None:
        try:
            neo4j_uri = settings.__dict__.get("neo4j_uri", "bolt://neo4j:7687")
            graphiti_obj = Graphiti(graphiti_host=neo4j_uri, user_id=role)
        except Exception as e:
            log.error(f"[{role}] Failed to connect to Graphiti: {e}")
            return 0

    vault_path = vault_path or settings.__dict__.get("obsidian_vault_path", "")
    if not vault_path:
        vault_path = str(
            Path(__file__).parent.parent.parent / "obsidian_vault" / role
        )

    try:
        # Search for all episodes (empty query returns recent/all)
        results = graphiti_obj.search(user_id=role, query="", top_k=100)
        facts = [r.get("body", "") for r in results if r.get("body")]
    except Exception as e:
        log.error(f"[{role}] Failed to search Graphiti: {e}")
        return 0

    # Write to Obsidian vault
    folder = Path(vault_path)
    folder.mkdir(parents=True, exist_ok=True)

    written = 0
    for idx, fact in enumerate(facts):
        if not fact or not fact.strip():
            continue
        content = _fact_to_markdown(fact, uuid=f"{role}_graphiti_{idx}")
        fname = _sanitize_filename(f"{idx:03d}_{fact[:50]}") + ".md"
        try:
            (folder / fname).write_text(content, encoding="utf-8")
            written += 1
        except Exception as e:
            log.error(f"[{role}] Failed to write {fname}: {e}")

    # Create index note
    _write_index_note(folder, role, facts)

    log.info(f"[{role}] Exported {written} Graphiti memories to {vault_path}")
    return written


def _write_index_note(folder: Path, role: str, facts: list[str]):
    """Write an index/summary note for the agent's memories."""
    index_lines = [
        "---",
        "tags: [memory-index]",
        "---",
        "",
        f"# {role.capitalize()} — Memory Index",
        f"> Exported: {datetime.now().isoformat()}",
        f"> Total memories: {len(facts)}",
        "",
        "## Recent Memories",
        "",
    ]
    for idx, fact in enumerate(facts[:20]):  # Show first 20 in index
        snippet = fact[:80].replace("\n", " ")
        sanitized = _sanitize_filename(f"{idx:03d}_{fact[:50]}")
        index_lines.append(f"- [[{sanitized}]] — {snippet}")

    if len(facts) > 20:
        index_lines.append("")
        index_lines.append(f"... and {len(facts) - 20} more")

    try:
        (folder / "_index.md").write_text("\n".join(index_lines), encoding="utf-8")
    except Exception as e:
        log.error(f"[{role}] Failed to write index: {e}")


async def export_agent_memories(
    role: str,
    memory_type: str = "redis",
    vault_path: Optional[str] = None,
) -> dict:
    """
    Export an agent's memories to Obsidian vault.

    Args:
        role: Agent role (manager, developer, etc.)
        memory_type: "redis" or "graphiti"
        vault_path: Optional custom vault path

    Returns:
        dict with "role", "written", "path", "status"
    """
    vault_path = vault_path or settings.__dict__.get("obsidian_vault_path", "")
    if not vault_path:
        vault_path = str(Path(__file__).parent.parent.parent / "obsidian_vault" / role)

    try:
        if memory_type == "redis":
            written = await export_from_redis_memory(role, vault_path=vault_path)
        elif memory_type == "graphiti":
            written = await export_from_graphiti_memory(role, vault_path=vault_path)
        else:
            return {
                "role": role,
                "written": 0,
                "path": vault_path,
                "status": f"Unknown memory_type: {memory_type}",
            }

        return {
            "role": role,
            "written": written,
            "path": vault_path,
            "status": "ok",
        }
    except Exception as e:
        log.error(f"[{role}] Export failed: {e}")
        return {
            "role": role,
            "written": 0,
            "path": vault_path,
            "status": f"error: {e}",
        }
