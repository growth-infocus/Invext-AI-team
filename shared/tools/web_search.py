import httpx
from shared.core.config import settings
from shared.core.tool_registry import ToolRegistry, make_schema
SCHEMA = make_schema("web_search","Search the web for current information.",{"query":{"type":"string","description":"Search query"}})
async def _execute(args):
    q = args.get("query","")
    if not settings.serper_api_key: return f"[web_search] No SERPER_API_KEY. Query: {q}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://google.serper.dev/search",headers={"X-API-KEY":settings.serper_api_key},json={"q":q,"num":5})
        r.raise_for_status(); data = r.json()
    results = data.get("organic",[])
    if not results: return "No results."
    return "\n\n".join(f"{i+1}. {r['title']}\n   {r.get('snippet','')}\n   {r['link']}" for i,r in enumerate(results[:5]))
def register(): ToolRegistry.register("web_search", SCHEMA, _execute)
