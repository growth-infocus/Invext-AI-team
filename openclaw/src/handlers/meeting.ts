import type { Env } from '../types'

/**
 * Meeting coordinator — when the team has a meeting and wants AI to join.
 *
 * Triggered by:
 *   POST /meeting/join         → AI joins the Teams channel with intro + standup
 *   POST /meeting/end          → AI posts meeting summary
 *   GET  /meeting/standup      → Get latest standup without joining
 */

export async function handleMeetingJoin(request: Request, env: Env): Promise<Response> {
  const body = await request.json().catch(() => ({})) as any
  const meetingName = body.meeting_name || 'Team Meeting'
  const requestedBy = body.requested_by || 'Team'

  // 1. Get standup from backend
  let standup = ''
  try {
    const r = await fetch(`${env.BACKEND_URL}/standup/now`)
    const data = await r.json() as any
    standup = data.message || 'Generating standup...'
  } catch {
    standup = 'Standup generation in progress...'
  }

  // 2. Get team status
  let status = ''
  try {
    const r = await fetch(`${env.BACKEND_URL}/status`)
    const data = await r.json() as any
    status = data.summary || ''
  } catch {
    status = 'Status unavailable'
  }

  // 3. Build Teams meeting intro message
  const intro = `## 👋 AI Team joining: ${meetingName}

**Requested by:** ${requestedBy}
**Time:** ${new Date().toUTCString()}

---

${status}

---

> **Standup is being posted shortly. Ask me anything during the meeting:**
> - \`@manager what is the sprint status?\`
> - \`@developer are there any blockers?\`
> - \`@security any critical issues?\`
> - Or just ask a plain question and the Manager will answer`

  // 4. Post to Teams
  await postToTeams(env, intro)

  return new Response(JSON.stringify({
    status: 'joined',
    message: `AI Team has joined ${meetingName} on Teams`,
    teams_posted: !!env.TEAMS_WEBHOOK_URL
  }), { headers: { 'Content-Type': 'application/json' } })
}

export async function handleMeetingEnd(request: Request, env: Env): Promise<Response> {
  const body = await request.json().catch(() => ({})) as any
  const meetingName = body.meeting_name || 'Team Meeting'

  // Get task summary for meeting wrap-up
  let summary = ''
  try {
    const r = await fetch(`${env.BACKEND_URL}/tasks?status=in_progress&limit=20`)
    const data = await r.json() as any
    const tasks = data.tasks || []
    const lines = tasks.slice(0, 10).map((t: any) => `- **${t.ticket_id}** [${t.assigned_to}] ${t.title?.slice(0, 60)}`)
    summary = lines.join('\n') || 'No active tasks'
  } catch {
    summary = 'Unable to fetch task summary'
  }

  const wrapUp = `## 📋 Meeting Wrap-up: ${meetingName}

**Ended:** ${new Date().toUTCString()}

### Active Work After Meeting
${summary}

> The team continues working autonomously. Next standup at **09:00 UTC** tomorrow.`

  await postToTeams(env, wrapUp)

  return new Response(JSON.stringify({ status: 'meeting_ended', summary_posted: true }), {
    headers: { 'Content-Type': 'application/json' }
  })
}

export async function handleGetStandup(_request: Request, env: Env): Promise<Response> {
  try {
    const r = await fetch(`${env.BACKEND_URL}/standup/now`)
    const data = await r.json() as any
    return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), { status: 500, headers: { 'Content-Type': 'application/json' } })
  }
}

async function postToTeams(env: Env, message: string): Promise<void> {
  if (!env.TEAMS_WEBHOOK_URL) return
  try {
    await fetch(env.TEAMS_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        '@type': 'MessageCard',
        '@context': 'http://schema.org/extensions',
        themeColor: '0076D7',
        summary: message.slice(0, 100),
        sections: [{ activityTitle: '🤖 AI Agent Team', activityText: message, markdown: true }]
      })
    })
  } catch (e) {
    console.error('Teams post failed:', e)
  }
}
