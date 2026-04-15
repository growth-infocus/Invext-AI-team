import re, httpx
from shared.core.tool_registry import ToolRegistry, make_schema
SCHEMA = make_schema("web_browse","Fetch and read a webpage.",{"url":{"type":"string","description":"URL"},"max_chars":{"type":"integer","description":"Max chars (default 3000)"}},required=["url"])
def _clean(html):
    html = re.sub(r"<script[^>]*>.*?</script>","",html,flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>","",html,flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r"<[^>]+>"," ",html); return re.sub(r"\s+"," ",html).strip()
async def _execute(args):
    url=args.get("url",""); max_c=int(args.get("max_chars",3000))
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=True) as c:
            r=await c.get(url,headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status()
        t=_clean(r.text); return t[:max_c]+("…" if len(t)>max_c else "")
    except Exception as e: return f"[web_browse] {e}"
def register(): ToolRegistry.register("web_browse", SCHEMA, _execute)
