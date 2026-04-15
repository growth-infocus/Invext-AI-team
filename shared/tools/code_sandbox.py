import asyncio, sys
from shared.core.tool_registry import ToolRegistry, make_schema
SCHEMA = make_schema("code_run","Run Python code in a subprocess.",{"code":{"type":"string"},"timeout":{"type":"integer","description":"Max seconds (default 15)"}},required=["code"])
async def _execute(args):
    code=args.get("code",""); timeout=int(args.get("timeout",15))
    try:
        proc=await asyncio.create_subprocess_exec(sys.executable,"-c",code,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(proc.communicate(),timeout=timeout)
        parts=[]
        if out.strip(): parts.append(f"STDOUT:\n{out.decode().strip()}")
        if err.strip(): parts.append(f"STDERR:\n{err.decode().strip()}")
        if proc.returncode!=0: parts.append(f"Exit: {proc.returncode}")
        return "\n".join(parts) or "(no output)"
    except asyncio.TimeoutError: return f"[code_run] Timeout after {timeout}s"
    except Exception as e: return f"[code_run] {e}"
def register(): ToolRegistry.register("code_run", SCHEMA, _execute)
