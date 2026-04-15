"""shared/core/tool_registry.py — Global tool registry (same interface as monolith)"""
from __future__ import annotations
from typing import Callable, Awaitable


class ToolRegistry:
    _schemas:   dict[str, dict] = {}
    _executors: dict[str, Callable] = {}

    @classmethod
    def register(cls, name, schema, executor):
        cls._schemas[name] = schema
        cls._executors[name] = executor

    @classmethod
    def get_schemas(cls, *names) -> list[dict]:
        return [cls._schemas[n] for n in names if n in cls._schemas]

    @classmethod
    async def execute(cls, tool_name, args) -> str:
        if tool_name not in cls._executors:
            return f"[ToolRegistry] Unknown tool: {tool_name}"
        try:
            return str(await cls._executors[tool_name](args))
        except Exception as e:
            return f"[ToolRegistry] {tool_name} error: {e}"

    @classmethod
    def available_tools(cls) -> list[str]:
        return list(cls._schemas.keys())


def make_schema(name, description, properties, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required or list(properties.keys())}}}
