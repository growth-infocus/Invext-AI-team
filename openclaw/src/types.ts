export interface Env {
  BACKEND_URL: string;          // e.g. https://your-backend.com
  API_KEY: string;              // optional bearer token for direct API access
  TWILIO_ACCOUNT_SID: string;   // Twilio credentials for WhatsApp replies
  TWILIO_AUTH_TOKEN: string;
  TWILIO_WHATSAPP_FROM: string; // e.g. whatsapp:+14155238886
  MAILGUN_API_KEY: string;      // for sending email replies
  MAILGUN_DOMAIN: string;       // e.g. mg.yourdomain.com
  REPLY_EMAIL_FROM: string;     // e.g. manager@yourdomain.com
  TEAMS_BOT_TOKEN: string;      // Microsoft Teams Bot Framework token
  TEAMS_WEBHOOK_URL: string;    // Teams webhook for posting meeting messages
}

export interface BackendGoalRequest {
  goal: string;
}

export interface BackendAskRequest {
  question: string;
}

export interface ParsedMessage {
  text: string;           // the actual message content
  from: string;           // sender identifier (phone/email/teams user)
  channel: "whatsapp" | "email" | "teams" | "api";
  replyTo?: string;       // for email: reply-to address; for teams: conversation id
  role?: string;          // parsed target role if message starts with @role
  intent: "goal" | "ask" | "issue" | "status" | "help";
}

export interface IntentParseResult {
  intent: ParsedMessage['intent'];
  role?: string;
  cleanText: string;
}
