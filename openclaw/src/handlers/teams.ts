import { Env } from '../types'
import { parseIntent, callBackend } from '../index'

interface TeamsActivity {
  type: string
  text: string
  from?: { id: string; name: string }
  conversation?: { id: string }
  serviceUrl?: string
  replyToId?: string
}

export async function handleTeams(request: Request, env: Env): Promise<Response> {
  try {
    // Parse JSON body
    const activity = (await request.json()) as TeamsActivity

    // Only process message activities
    if (activity.type !== 'message' || !activity.text) {
      return new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const messageText = activity.text
    const conversationId = activity.conversation?.id
    const replyToId = activity.replyToId
    const serviceUrl = activity.serviceUrl
    const fromName = activity.from?.name || 'Team Member'

    let reply = ''
    const lowerText = messageText.toLowerCase().trim()

    // Check for meeting-related intents
    if (lowerText.includes('join meeting') || lowerText.includes('start meeting')) {
      const r = await fetch(`${env.BACKEND_URL.replace(/\/$/, '')}/meeting/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ meeting_name: 'Teams Meeting', requested_by: fromName })
      })
      const data = await r.json() as any
      reply = `✅ ${data.message || 'AI Team joined the meeting'}`
    } else if (lowerText.includes('end meeting') || lowerText.includes('leave meeting')) {
      const r = await fetch(`${env.BACKEND_URL.replace(/\/$/, '')}/meeting/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ meeting_name: 'Teams Meeting' })
      })
      const data = await r.json() as any
      reply = `📋 ${data.message || 'Meeting ended and summary posted'}`
    } else if (lowerText.includes('standup')) {
      const r = await fetch(`${env.BACKEND_URL.replace(/\/$/, '')}/standup/now`)
      const data = await r.json() as any
      reply = data.message || 'Generating standup...'
    } else if (lowerText.includes('schedule') || lowerText.includes('jobs')) {
      const r = await fetch(`${env.BACKEND_URL.replace(/\/$/, '')}/scheduler/jobs`)
      const data = await r.json() as any
      const jobs = data.jobs || []
      reply = '📅 **Scheduled Jobs:**\n' + jobs.map((j: any) => `- **${j.schedule}**: ${j.description}`).join('\n')
    } else {
      // Parse intent and call backend
      const parsed = parseIntent(messageText)
      reply = await callBackend(env, parsed)
    }

    // Reply to Teams if we have the necessary context
    if (serviceUrl && conversationId && replyToId) {
      const teamsReplyUrl = `${serviceUrl.replace(/\/$/, '')}/v3/conversations/${conversationId}/activities/${replyToId}`

      const teamsResponse = await fetch(teamsReplyUrl, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${env.TEAMS_BOT_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          type: 'message',
          text: reply,
        }),
      })

      if (!teamsResponse.ok) {
        console.error(`Teams API error: ${teamsResponse.status}`)
      }
    }

    return new Response(JSON.stringify({ status: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('Teams handler error:', err)
    return new Response(JSON.stringify({ status: 'error', error: String(err) }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
