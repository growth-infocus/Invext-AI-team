import asyncio, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from shared.core.config import settings
from shared.core.tool_registry import ToolRegistry, make_schema
SCHEMA = make_schema("send_email","Send an email notification.",{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},required=["subject","body"])
def _send(to,subject,body):
    msg=MIMEMultipart(); msg["From"]=settings.smtp_user; msg["To"]=to; msg["Subject"]=subject
    msg.attach(MIMEText(body,"plain"))
    with smtplib.SMTP(settings.smtp_host,settings.smtp_port) as s:
        s.starttls(); s.login(settings.smtp_user,settings.smtp_password); s.sendmail(settings.smtp_user,[to],msg.as_string())
async def _execute(args):
    to=args.get("to") or settings.notify_email; subject=args.get("subject",""); body=args.get("body","")
    if not all([settings.smtp_user,settings.smtp_password,to]):
        return f"[send_email] No config. Would send: To={to} Subject={subject}\n{body[:200]}"
    try:
        await asyncio.to_thread(_send,to,subject,body); return f"Sent to {to} ✅"
    except Exception as e: return f"[send_email] {e}"
def register(): ToolRegistry.register("send_email", SCHEMA, _execute)
