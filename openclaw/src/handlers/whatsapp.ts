import { Env } from '../types'
import { parseIntent, callBackend } from '../index'

export async function handleWhatsApp(request: Request, env: Env): Promise<Response> {
  try {
    // Parse Twilio's form-encoded body
    const formData = await request.formData()
    const body = formData.get('Body') as string
    const from = formData.get('From') as string

    if (!body || !from) {
      return new Response('<Response></Response>', {
        status: 200,
        headers: { 'Content-Type': 'application/xml' },
      })
    }

    // Parse intent and call backend
    const parsed = parseIntent(body)
    const reply = await callBackend(env, parsed)

    // Send reply via Twilio API
    const twilioUrl = `https://api.twilio.com/2010-04-01/Accounts/${env.TWILIO_ACCOUNT_SID}/Messages.json`
    const credentials = btoa(`${env.TWILIO_ACCOUNT_SID}:${env.TWILIO_AUTH_TOKEN}`)

    const formBody = new URLSearchParams({
      To: from,
      From: env.TWILIO_WHATSAPP_FROM,
      Body: reply,
    })

    const twilioResponse = await fetch(twilioUrl, {
      method: 'POST',
      headers: {
        Authorization: `Basic ${credentials}`,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: formBody.toString(),
    })

    if (!twilioResponse.ok) {
      console.error(`Twilio API error: ${twilioResponse.status}`)
    }

    // Return empty TwiML response (reply already sent via API)
    return new Response('<Response></Response>', {
      status: 200,
      headers: { 'Content-Type': 'application/xml' },
    })
  } catch (err) {
    console.error('WhatsApp handler error:', err)
    return new Response('<Response></Response>', {
      status: 200,
      headers: { 'Content-Type': 'application/xml' },
    })
  }
}
