/**
 * Double opt-in for the two Joanna Garland alert lists.
 *
 * Secrets (wrangler secret put): RESEND_API_KEY, SUBSCRIBE_SECRET, FROM_EMAIL
 * Optional secret: MAILING_ADDRESS
 * Optional var: ALLOWED_ORIGINS, comma-separated, for a custom site address.
 */

const DEFAULT_ORIGINS = [
  "https://joannagarland.com",
  "https://www.joannagarland.com",
  "https://fossil179.github.io",
  "http://127.0.0.1:8765",
  "http://localhost:8765",
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return cors(request, env, empty(204));
    if (request.method === "POST" && url.pathname === "/subscribe") {
      return cors(request, env, await subscribe(request, env, url));
    }
    if (request.method === "GET" && url.pathname === "/confirm") {
      return confirm(url, env);
    }
    return new Response("Not found", { status: 404 });
  },
};

function allowedOrigins(env) {
  const extra = (env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return new Set([...DEFAULT_ORIGINS, ...extra]);
}

function cors(request, env, response) {
  const origin = request.headers.get("Origin") || "";
  const headers = new Headers(response.headers);
  if (allowedOrigins(env).has(origin)) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Vary", "Origin");
    headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
    headers.set("Access-Control-Allow-Headers", "Content-Type");
  }
  return new Response(response.body, { status: response.status, headers });
}

function empty(status) {
  return new Response(null, { status });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function validEmail(value) {
  return typeof value === "string" && value.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

async function subscribe(request, env, url) {
  if (!env.RESEND_API_KEY || !env.SUBSCRIBE_SECRET || !env.FROM_EMAIL) {
    return json({ error: "Signup is not configured yet." }, 503);
  }
  const raw = await request.text();
  if (raw.length > 2000) return json({ error: "That signup could not be read." }, 400);
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ error: "That signup could not be read." }, 400);
  }
  if (body.company) return json({ ok: true });
  const email = String(body.email || "").trim().toLowerCase();
  const starts = Boolean(body.starts);
  const results = Boolean(body.results);
  if (!validEmail(email)) return json({ error: "Enter an email address." }, 400);
  if (!starts && !results) return json({ error: "Choose at least one kind of email." }, 400);

  const exp = Date.now() + 48 * 60 * 60 * 1000;
  const payload = `${email}|${starts ? 1 : 0}|${results ? 1 : 0}|${exp}`;
  const token = `${base64url(payload)}.${await hmac(env.SUBSCRIBE_SECRET, payload)}`;
  const link = `${url.origin}/confirm?token=${encodeURIComponent(token)}`;
  const sent = await sendConfirmation(env, email, link, starts, results);
  if (!sent) return json({ error: "The confirmation email could not be sent. Try again in a minute." }, 502);
  return json({ ok: true });
}

async function confirm(url, env) {
  if (!env.RESEND_API_KEY || !env.SUBSCRIBE_SECRET) return page("Signup is not configured yet.", false);
  const token = url.searchParams.get("token") || "";
  const parsed = await readToken(env.SUBSCRIBE_SECRET, token);
  if (!parsed) return page("This confirmation link has expired or is not valid.", false);
  const saved = await saveContact(env, parsed);
  if (!saved) return page("The list could not be updated. Open the link once more in a minute.", false);
  const choices = [];
  if (parsed.starts) choices.push("a tournament is about to start");
  if (parsed.results) choices.push("each result");
  return page(`You're confirmed. Emails will come when ${choices.join(" and ")}.`, true);
}

async function readToken(secret, token) {
  const split = token.split(".");
  if (split.length !== 2) return null;
  let payload;
  try {
    payload = decodeBase64url(split[0]);
  } catch {
    return null;
  }
  const expected = await hmac(secret, payload);
  if (!timingSafeEqual(expected, split[1])) return null;
  const parts = payload.split("|");
  if (parts.length !== 4) return null;
  const [email, starts, results, exp] = parts;
  if (!validEmail(email) || Number(exp) < Date.now()) return null;
  if (!["0", "1"].includes(starts) || !["0", "1"].includes(results)) return null;
  return { email, starts: starts === "1", results: results === "1" };
}

async function saveContact(env, parsed) {
  const topics = [
    { id: env.TOPIC_STARTS, subscription: parsed.starts ? "opt_in" : "opt_out" },
    { id: env.TOPIC_RESULTS, subscription: parsed.results ? "opt_in" : "opt_out" },
  ].filter((topic) => topic.id);
  const created = await resend(env, "POST", "/contacts", {
    email: parsed.email,
    unsubscribed: false,
    segments: env.SEGMENT_ID ? [{ id: env.SEGMENT_ID }] : [],
    topics,
  });
  if (created.ok) return true;
  if (created.status !== 409 && created.status !== 422) return false;
  const updated = await resend(env, "PATCH", `/contacts/${encodeURIComponent(parsed.email)}`, {
    unsubscribed: false,
    topics,
  });
  if (!updated.ok) return false;
  if (!env.SEGMENT_ID) return true;
  const contact = updated.body?.id ? updated.body : (await resend(env, "GET", `/contacts/${encodeURIComponent(parsed.email)}`)).body;
  const contactId = contact?.id;
  if (!contactId) return true;
  const joined = await resend(env, "POST", `/contacts/${contactId}/segments`, { segment_id: env.SEGMENT_ID });
  return joined.ok || joined.status === 409 || joined.status === 422;
}

async function sendConfirmation(env, email, link, starts, results) {
  const choices = [];
  if (starts) choices.push("when a tournament is about to start");
  if (results) choices.push("for each singles result");
  const address = (env.MAILING_ADDRESS || "").trim();
  const site = (env.SITE_URL || "https://joannagarland.com").replace(/\/$/, "");
  const text = [
    "Confirm Joanna Garland alerts",
    "",
    `You asked for an email ${choices.join(" and ")}.`,
    "",
    `Confirm: ${link}`,
    "",
    "The link lasts two days. If you didn't ask for this, ignore this email.",
    address,
    site,
  ].filter((line) => line !== undefined && line !== "").join("\n");
  const html = `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4efe8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4efe8">
<tr><td align="center" style="padding-top:28px;padding-bottom:28px;padding-left:16px;padding-right:16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#fffdf9" style="width:100%;max-width:600px;">
<tr><td style="padding-top:28px;padding-left:28px;padding-right:28px;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:34px;color:#1c1412;">Joanna Garland</td></tr>
<tr><td style="padding-top:14px;padding-left:28px;padding-right:28px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:24px;color:#1c1412;">Confirm that you want an email ${escapeHtml(choices.join(" and "))}.</td></tr>
<tr><td style="padding-top:22px;padding-left:28px;padding-right:28px;"><a href="${escapeHtml(link)}" style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:24px;color:#9f2d22;">Confirm these alerts</a></td></tr>
<tr><td style="padding-top:18px;padding-bottom:28px;padding-left:28px;padding-right:28px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:20px;color:#5e5148;">The link lasts two days. If you didn't ask for this, ignore this email.${address ? `<br>${escapeHtml(address)}` : ""}<br><a href="${escapeHtml(site)}/" style="color:#5e5148;">${escapeHtml(site)}</a></td></tr>
</table></td></tr></table></body></html>`;
  const from = env.FROM_EMAIL.includes("<") ? env.FROM_EMAIL : `Joanna Garland tracker <${env.FROM_EMAIL}>`;
  const result = await resend(env, "POST", "/emails", {
    from,
    to: [email],
    subject: "Confirm Joanna Garland alerts",
    html,
    text,
  }, `confirm/${await sha256(`${email}|${starts ? 1 : 0}|${results ? 1 : 0}`)}`);
  return result.ok;
}

async function resend(env, method, path, body, idempotencyKey) {
  const headers = {
    Authorization: `Bearer ${env.RESEND_API_KEY}`,
    "User-Agent": "joanna-garland-tracker",
  };
  if (body) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey.slice(0, 256);
  const response = await fetch(`https://api.resend.com${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let parsed = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  return { ok: response.ok, status: response.status, body: parsed };
}

function page(message, ok) {
  const html = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${ok ? "Confirmed" : "Not confirmed"}</title></head>
<body style="margin:0;background:#f4efe8;color:#1c1412;font-family:Georgia,'Times New Roman',serif;">
<main style="max-width:36rem;margin:4rem auto;padding:0 1.25rem;">
<p style="letter-spacing:0.08em;text-transform:uppercase;color:#9f2d22;font-family:Arial,Helvetica,sans-serif;font-size:13px;">Joanna Garland</p>
<h1 style="font-weight:normal;font-size:2rem;">${ok ? "You're on the list" : "That link didn't work"}</h1>
<p style="font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:1.5;">${escapeHtml(message)}</p>
</main></body></html>`;
  return new Response(html, { status: ok ? 200 : 400, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function base64url(value) {
  return btoa(value).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function decodeBase64url(value) {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "===".slice((value.length + 3) % 4);
  return atob(padded);
}

async function hmac(secret, payload) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return base64url(String.fromCharCode(...new Uint8Array(mac)));
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(left, right) {
  if (left.length !== right.length) return false;
  let mismatch = 0;
  for (let i = 0; i < left.length; i += 1) mismatch |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return mismatch === 0;
}
