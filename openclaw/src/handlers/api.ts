import { Env } from '../types'
import { parseIntent, callBackend } from '../index'

export async function handleApiRequest(
  request: Request,
  env: Env,
  url: URL
): Promise<Response> {
  try {
    // Check Bearer token if API_KEY is configured
    if (env.API_KEY && env.API_KEY.trim()) {
      const authHeader = request.headers.get('Authorization') || ''
      const expectedBearer = `Bearer ${env.API_KEY}`
      if (authHeader !== expectedBearer) {
        return new Response(JSON.stringify({ error: 'Unauthorized' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        })
      }
    }

    const pathname = url.pathname

    // POST /api/goal
    if (pathname === '/api/goal' && request.method === 'POST') {
      const body = (await request.json()) as any
      const goal = body.goal || body.text || ''
      if (!goal) {
        return new Response(JSON.stringify({ error: 'Missing goal field' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const parsed = parseIntent(goal)
      const response = await callBackend(env, parsed)
      return new Response(JSON.stringify({ response }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // POST /api/ask/:role
    if (pathname.match(/^\/api\/ask\/\w+$/) && request.method === 'POST') {
      const role = pathname.split('/')[3]
      const body = (await request.json()) as any
      const question = body.question || body.text || ''
      if (!question) {
        return new Response(JSON.stringify({ error: 'Missing question field' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const parsed = { intent: 'ask', role, cleanText: question }
      const response = await callBackend(env, parsed)
      return new Response(JSON.stringify({ response }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // GET /api/status
    if (pathname === '/api/status' && request.method === 'GET') {
      const parsed = { intent: 'status', cleanText: 'status' }
      const response = await callBackend(env, parsed)
      return new Response(JSON.stringify({ status: response }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // GET /api/health
    if (pathname === '/api/health' && request.method === 'GET') {
      return new Response(JSON.stringify({ health: 'ok', timestamp: new Date().toISOString() }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // POST /api/issue
    if (pathname === '/api/issue' && request.method === 'POST') {
      const body = (await request.json()) as any
      const issue = body.issue || body.text || ''
      if (!issue) {
        return new Response(JSON.stringify({ error: 'Missing issue field' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const parsed = { intent: 'issue', cleanText: issue }
      const response = await callBackend(env, parsed)
      return new Response(JSON.stringify({ response }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return new Response(JSON.stringify({ error: 'API endpoint not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('API handler error:', err)
    return new Response(
      JSON.stringify({ error: 'Internal server error', details: String(err) }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    )
  }
}
