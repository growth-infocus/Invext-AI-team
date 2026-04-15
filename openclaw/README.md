# OpenClaw — AI Agent Team Gateway

OpenClaw is the edge gateway for your AI Agent Team, running as a Cloudflare Worker. It receives messages from multiple channels (WhatsApp, Email, Microsoft Teams, and direct HTTP API), parses them to understand intent, and routes them to your Python backend for processing.

## Architecture

```
WhatsApp → Twilio webhook  ─┐
Email    → Mailgun webhook ─┤→ OpenClaw (Cloudflare Worker) → AI Agent Backend (Python)
Teams    → Bot webhook    ─┤                                   http://localhost:8000
API      → Direct HTTP   ─┘
```

The Worker parses incoming messages to determine their intent and automatically routes them to the appropriate backend endpoint.

## Message Routing Rules

OpenClaw intelligently routes messages based on their content:

### Goal Messages (default)
- **Pattern**: Any plain text message without special markers
- **Route**: `POST /goal`
- **Example**: "Build a new checkout flow"
- **Behavior**: Manager agent plans and delegates to specialists

### Ask Messages (@role)
- **Pattern**: `@role <question>`
- **Route**: `POST /ask/{role}`
- **Example**: "@developer How do we handle async errors?"
- **Supported roles**: manager, developer, devops, qa, support, docs, design, ux, ui_test, api_test, qa_auto, security
- **Behavior**: Routes directly to the specified role's agent

### Status Messages
- **Pattern**: "status", "what's happening", "team status", "progress"
- **Route**: `GET /status`
- **Example**: "status"
- **Behavior**: Returns current team status and active tasks

### Issue Messages
- **Pattern**: `issue:`, `bug:`, `problem:`, or `error:` prefix
- **Route**: `POST /issue`
- **Example**: "issue: Users can't reset passwords on mobile"
- **Behavior**: Logs a support issue for the team

### Help Messages
- **Pattern**: "help"
- **Route**: Returns command list
- **Example**: "help"
- **Behavior**: Shows available commands

## Setup for Each Channel

### WhatsApp (Twilio)

1. **Create a Twilio account** and set up a WhatsApp Sandbox
2. **Get your credentials**:
   - Account SID
   - Auth Token
   - WhatsApp From number (e.g., whatsapp:+14155238886)
3. **Configure webhook** in Twilio Console:
   - Go to Messaging > WhatsApp Sandbox
   - Set "When a message comes in" webhook to: `https://your-openclaw-domain.com/webhook/whatsapp`
4. **Set environment variables** in Cloudflare Worker:
   ```bash
   wrangler secret put TWILIO_ACCOUNT_SID
   wrangler secret put TWILIO_AUTH_TOKEN
   wrangler secret put TWILIO_WHATSAPP_FROM
   ```

### Email (Mailgun)

1. **Create a Mailgun account**
2. **Verify a domain** in Mailgun
3. **Get your credentials**:
   - API Key
   - Domain (e.g., mg.yourdomain.com)
   - Reply-from address (e.g., manager@yourdomain.com)
4. **Configure Inbound Parse** in Mailgun:
   - Go to Receiving > Inbound
   - Add route: catch-all or specific address
   - Set webhook URL to: `https://your-openclaw-domain.com/webhook/email`
5. **Set environment variables**:
   ```bash
   wrangler secret put MAILGUN_API_KEY
   wrangler secret put MAILGUN_DOMAIN
   wrangler secret put REPLY_EMAIL_FROM
   ```

### Microsoft Teams

1. **Create a Teams app** in Azure Portal or Developer Portal
2. **Configure as a Bot**:
   - Enable the "Messages" capability
   - Set messaging endpoint to: `https://your-openclaw-domain.com/webhook/teams`
3. **Get bot token**:
   - Generate from Azure Bot Service
4. **Set environment variables**:
   ```bash
   wrangler secret put TEAMS_BOT_TOKEN
   ```

### Direct HTTP API

Use curl or any HTTP client to interact directly:

```bash
# Send a goal to Manager
curl -X POST https://your-openclaw-domain.com/api/goal \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"goal": "Create a new dashboard page"}'

# Ask a specific role
curl -X POST https://your-openclaw-domain.com/api/ask/developer \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the database schema?"}'

# Get team status
curl https://your-openclaw-domain.com/api/status

# Submit an issue
curl -X POST https://your-openclaw-domain.com/api/issue \
  -H "Content-Type: application/json" \
  -d '{"issue": "Button text is cut off on mobile"}'

# Health check
curl https://your-openclaw-domain.com/api/health
```

Optional Bearer token authentication via `API_KEY` environment variable protects the `/api/*` endpoints.

## Local Development

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Set environment variables** in `.env.local`:
   ```
   BACKEND_URL=http://localhost:8000
   TWILIO_ACCOUNT_SID=your_sid
   TWILIO_AUTH_TOKEN=your_token
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   MAILGUN_API_KEY=your_key
   MAILGUN_DOMAIN=mg.yourdomain.com
   REPLY_EMAIL_FROM=manager@yourdomain.com
   TEAMS_BOT_TOKEN=your_token
   API_KEY=dev-key
   ```

3. **Start development server**:
   ```bash
   npm run dev
   ```
   The Worker will run on `http://localhost:8787`

4. **Test endpoints**:
   ```bash
   # Test the root status page
   curl http://localhost:8787

   # Test API with Bearer token
   curl -X POST http://localhost:8787/api/goal \
     -H "Authorization: Bearer dev-key" \
     -H "Content-Type: application/json" \
     -d '{"goal": "Test this gateway"}'
   ```

5. **Type-check** before deployment:
   ```bash
   npm run type-check
   ```

## Deployment

1. **Set up Cloudflare account** and authenticate:
   ```bash
   wrangler login
   ```

2. **Configure `wrangler.toml`**:
   - Set your domain and zone name
   - Update route pattern to match your domain

3. **Store secrets** (these aren't in version control):
   ```bash
   wrangler secret put BACKEND_URL
   wrangler secret put TWILIO_ACCOUNT_SID
   wrangler secret put TWILIO_AUTH_TOKEN
   wrangler secret put TWILIO_WHATSAPP_FROM
   wrangler secret put MAILGUN_API_KEY
   wrangler secret put MAILGUN_DOMAIN
   wrangler secret put REPLY_EMAIL_FROM
   wrangler secret put TEAMS_BOT_TOKEN
   wrangler secret put API_KEY
   ```

4. **Deploy**:
   ```bash
   npm run deploy
   ```

5. **Verify deployment**:
   ```bash
   curl https://your-openclaw-domain.com
   ```

## Available Commands (User Perspective)

Users can send any of these commands through WhatsApp, Email, Teams, or API:

| Command | Example | Routes to |
|---------|---------|-----------|
| Plain goal | "Create a login page" | `/goal` (Manager) |
| Direct ask | "@developer How do async errors work?" | `/ask/developer` |
| Status | "status" | `/status` |
| Issue/Bug | "issue: Users report slow loading" | `/issue` |
| Help | "help" | Shows command list |

### Supported Roles
- manager
- developer
- devops
- qa
- support
- docs
- design
- ux
- ui_test
- api_test
- qa_auto
- security

## Error Handling

All handlers include comprehensive error handling:
- Malformed requests return sensible error messages
- Backend timeouts or failures return a friendly error message to the user
- Webhook validation errors are logged but don't break the service

## Files Overview

- **`src/index.ts`** — Main router, intent parser, backend caller
- **`src/types.ts`** — TypeScript interfaces for type safety
- **`src/handlers/api.ts`** — Direct HTTP API proxy
- **`src/handlers/whatsapp.ts`** — Twilio WhatsApp webhook handler
- **`src/handlers/email.ts`** — Mailgun inbound email webhook handler
- **`src/handlers/teams.ts`** — Microsoft Teams bot webhook handler
- **`package.json`** — Dependencies and scripts
- **`wrangler.toml`** — Cloudflare Worker configuration
- **`tsconfig.json`** — TypeScript configuration
- **`.env.example`** — Environment variable template

## Backend Integration

OpenClaw expects your Python backend to respond with JSON:

```json
{
  "plan": "string",
  "answer": "string",
  "response": "string",
  "summary": "string"
}
```

The response is automatically extracted and sent back to the user's channel (WhatsApp, Email, Teams, or API).

## License

Internal use only.
