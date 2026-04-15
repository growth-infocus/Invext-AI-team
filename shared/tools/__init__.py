from shared.tools import (
    web_search, web_browse, file_ops, code_sandbox,
    email_send, task_tools, ticket_tool, devops_tools, plan_tools,
)


def register_all():
    web_search.register()
    web_browse.register()
    file_ops.register()
    code_sandbox.register()
    email_send.register()
    task_tools.register()
    ticket_tool.register()
    devops_tools.register()
    plan_tools.register()
