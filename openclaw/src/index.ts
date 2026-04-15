import { handleWhatsApp } from './handlers/whatsapp'
import { handleEmail } from './handlers/email'
import { handleTeams } from './handlers/teams'
import { handleApiRequest } from './handlers/api'
import { handleMeetingJoin, handleMeetingEnd, handleGetStandup } from './handlers/meeting'
import type { Env, IntentParseResult, ParsedMessage } from './types'

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    const path = url.pathname

    try {
      // CORS preflight
      if (request.method === 'OPTIONS') {
        return new Response(null, { headers: corsHeaders() })
      }

      // Route by path
      if (path.startsWith('/webhook/whatsapp')) return handleWhatsApp(request, env)
      if (path.startsWith('/webhook/email')) return handleEmail(request, env)
      if (path.startsWith('/webhook/teams')) return handleTeams(request, env)
      if (path === '/meeting/join') return handleMeetingJoin(request, env)
      if (path === '/meeting/end') return handleMeetingEnd(request, env)
      if (path === '/meeting/standup') return handleGetStandup(request, env)
      if (path.startsWith('/api/')) return handleApiRequest(request, env, url)

      // Root — show status page
      if (path === '/' || path === '') {
        return new Response(
          JSON.stringify({
            service: 'OpenClaw — AI Agent Team Gateway',
            channels: [
              'WhatsApp (/webhook/whatsapp)',
              'Email (/webhook/email)',
              'Teams (/webhook/teams)',
              'API (/api/*)',
            ],
            meeting_endpoints: '/meeting/join, /meeting/end, /meeting/standup',
            backend: env.BACKEND_URL || 'not configured',
            status: 'running',
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json', ...corsHeaders() },
          }
        )
      }

      return new Response(JSON.stringify({ error: 'Not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    } catch (err) {
      console.error('Router error:', err)
      return new Response(
        JSON.stringify({ error: 'Internal server error', details: String(err) }),
        { status: 500, headers: { 'Content-Type': 'application/json' } }
      )
    }
  },
}

export function corsHeaders(): HeadersInit {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  }
}

export function parseIntent(text: string): IntentParseResult {
  const t = text.trim()

  // @role message → ask that specific agent
  const roleMatch = t.match(/^@(\w+)\s+(.+)/s)
  if (roleMatch) {
    const role = roleMatch[1].toLowerCase()
    const validRoles = [
      'manager',
      'developer',
      'devops',
      'qa',
      'support',
      'docs',
      'design',
      'ux',
      'ui_test',
      'api_test',
      'qa_auto',
      'security',
    ]
    if (validRoles.includes(role)) {
      return { intent: 'ask', role, cleanText: roleMatch[2] }
    }
  }

  // "status" or "what's happening" → status check
  if (/^(status|what.s happening|team status|progress)/i.test(t)) {
    return { intent: 'status', cleanText: t }
  }

  // "issue:" or "bug:" prefix → support issue
  if (/^(issue:|bug:|problem:|error:)/i.test(t)) {
    return {
      intent: 'issue',
      cleanText: t.replace(/^(issue:|bug:|problem:|error:)\s*/i, ''),
    }
  }

  // "help" → show available commands
  if (/^help$/i.test(t)) {
    return { intent: 'help', cleanText: t }
  }

  // Everything else → goal for Manager
  return { intent: 'goal', cleanText: t }
}

export async function callBackend(
  env: Env,
  parsed: { intent: string; role?: string; cleanText: string }
): Promise<string> {
  const base = env.BACKEND_URL.replace(/\/$/, '')

  try {
    if (parsed.intent === 'status') {
      const r = await fetch(`${base}/status`)
      if (!r.ok) throw new Error(`Backend returned ${r.status}`)
      const data = (await r.json()) as any
      return data.summary || JSON.stringify(data)
    }

    if (parsed.intent === 'help') {
      return `AI Agent Team — Commands:
• Just type your goal → Manager plans and delegates
• @developer <question> → Ask Developer directly
• @security <question> → Ask Security Engineer
• status → Team status
• issue: <description> → Submit a support issue

All roles: manager, developer, devops, qa, support, docs, design, ux, ui_test, api_test, qa_auto, security`
    }

    if (parsed.intent === 'issue') {
      const r = await fetch(`${base}/issue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ issue: parsed.cleanText }),
      })
      if (!r.ok) throw new Error(`Backend returned ${r.status}`)
      const data = (await r.json()) as any
      return data.answer || data.plan || JSON.stringify(data)
    }

    if (parsed.intent === 'ask' && parsed.role) {
      const r = await fetch(`${base}/ask/${parsed.role}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: parsed.cleanText }),
      })
      if (!r.ok) throw new Error(`Backend returned ${r.status}`)
      const data = (await r.json()) as any
      return data.answer || JSON.stringify(data)
    }

    // Default: send as goal to Manager
    const r = await fetch(`${base}/goal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal: parsed.cleanText }),
    })
    if (!r.ok) throw new Error(`Backend returned ${r.status}`)
    const data = (await r.json()) as any
    return data.plan || data.response || JSON.stringify(data)
  } catch (err) {
    return `Error contacting backend: ${String(err)}`
  }
}
