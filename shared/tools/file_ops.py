from pathlib import Path
from shared.core.config import settings
from shared.core.tool_registry import ToolRegistry, make_schema
_SB = Path(settings.reports_dir).resolve()
READ_SCHEMA  = make_schema("file_read","Read a file from reports/.",{"path":{"type":"string","description":"Relative path"}})
WRITE_SCHEMA = make_schema("file_write","Write to reports/.",{"path":{"type":"string"},"content":{"type":"string"},"append":{"type":"boolean","description":"Append instead of overwrite"}},required=["path","content"])
LIST_SCHEMA  = make_schema("file_list","List files in reports/.",{"subfolder":{"type":"string","description":"Subfolder (optional)"}},required=[])
def _safe(rel):
    t=(_SB/rel).resolve()
    if not str(t).startswith(str(_SB)): raise PermissionError(f"Outside sandbox: {rel}")
    return t
async def _read(args):
    try:
        p=_safe(args["path"]); return p.read_text() if p.exists() else f"Not found: {args['path']}"
    except Exception as e: return f"[file_read] {e}"
async def _write(args):
    try:
        p=_safe(args["path"]); p.parent.mkdir(parents=True,exist_ok=True)
        p.open("a" if args.get("append") else "w").write(args["content"])
        return f"Written {len(args['content'])} chars to {args['path']}"
    except Exception as e: return f"[file_write] {e}"
async def _list(args):
    try:
        base=_safe(args.get("subfolder","")) if args.get("subfolder") else _SB
        return "\n".join(str(f.relative_to(_SB)) for f in base.rglob("*") if f.is_file()) or "(empty)"
    except Exception as e: return f"[file_list] {e}"
def register():
    ToolRegistry.register("file_read",READ_SCHEMA,_read)
    ToolRegistry.register("file_write",WRITE_SCHEMA,_write)
    ToolRegistry.register("file_list",LIST_SCHEMA,_list)
