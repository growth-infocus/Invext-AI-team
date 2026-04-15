import { Env } from '../types'
import { parseIntent, callBackend } from '../index'

export async function handleEmail(request: Request, env: Env): Promise<Response> {
  try {
    // Parse Mailgun's multipart form data
    const formData = await request.formData()
    const sender = formData.get('sender') as string
    const subject = formData.get('subject') as string
    const bodyPlain = formData.get('body-plain') as string

    if (!sender || !bodyPlain) {
      return new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // Combine subject and body for context
    const messageText = subject ? `Subject: ${subject}\n\n${bodyPlain}` : bodyPlain

    // Parse intent and call backend
    const parsed = parseIntent(messageText)
    const reply = await callBackend(env, parsed)

    // Send reply via Mailgun API
    const mailgunUrl = `https://api.mailgun.net/v3/${env.MAILGUN_DOMAIN}/messages`
    const credentials = btoa(`api:${env.MAILGUN_API_KEY}`)

    const formBody = new URLSearchParams({
      from: env.REPLY_EMAIL_FROM,
      to: sender,
      subject: `Re: ${subject || '(no subject)'}`,
      text: reply,
    })

    const mailgunResponse = await fetch(mailgunUrl, {
      method: 'POST',
      headers: {
        Authorization: `Basic ${credentials}`,
      },
      body: formBody,
    })

    if (!mailgunResponse.ok) {
      console.error(`Mailgun API error: ${mailgunResponse.status}`)
    }

    // Return success response to Mailgun
    return new Response(JSON.stringify({ status: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('Email handler error:', err)
    return new Response(JSON.stringify({ status: 'error', error: String(err) }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
