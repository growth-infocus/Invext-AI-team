from shared.core.tool_registry import ToolRegistry, make_schema
from shared.core.database import create_task, get_tasks, get_task, update_task
ALL_ROLES = ["developer","devops","qa","support","docs","manager","design","ux","ui_test","api_test","qa_auto","security"]
CREATE_S = make_schema("create_task","Create and assign a task to any team agent.",{"title":{"type":"string"},"description":{"type":"string"},"assigned_to":{"type":"string","enum":ALL_ROLES},"priority":{"type":"string","enum":["P1","P2","P3","P4"]}},required=["title","description","assigned_to"])
GET_S    = make_schema("get_tasks","Get task list.",{"assigned_to":{"type":"string"},"status":{"type":"string"},"limit":{"type":"integer"}},required=[])
UPDATE_S = make_schema("update_task","Update a task.",{"task_id":{"type":"string"},"status":{"type":"string"},"result":{"type":"string"}},required=["task_id"])
async def _create(args):
    t=create_task(title=args["title"],description=args["description"],assigned_to=args["assigned_to"],priority=args.get("priority","P3"),created_by="agent")
    return f"Created {t['ticket_id']} → {args['assigned_to']}"
async def _get(args):
    tasks=get_tasks(assigned_to=args.get("assigned_to"),status=args.get("status"),limit=int(args.get("limit",20)))
    if not tasks: return "No tasks."
    return "\n".join(f"[{t['priority']}] {t['ticket_id']} {t['title']} | {t['status']} | {t['assigned_to']}" for t in tasks)
async def _update(args):
    update_task(task_id=args["task_id"],status=args.get("status"),result=args.get("result")); return f"Updated {args['task_id'][:8]}"
def register():
    ToolRegistry.register("create_task",CREATE_S,_create)
    ToolRegistry.register("get_tasks",GET_S,_get)
    ToolRegistry.register("update_task",UPDATE_S,_update)
