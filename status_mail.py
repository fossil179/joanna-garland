"""Daily note to the site owner.

The note says whether the public site is up and lists confirmed subscribers.
Addresses are sent only in that email. They are not printed or saved here.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "alerts.config.json"
ZONE = ZoneInfo("Europe/Berlin")
SITE = "https://joannagarland.com/"
FRESH_FOR = timedelta(hours=36)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def from_header(from_email: str) -> str:
    if "<" in from_email:
        return from_email
    return f"Joanna Garland tracker <{from_email}>"


def topic_labels(config: dict, topics: list[dict]) -> list[str]:
    names = {
        config["topics"]["starts"]: "tournament starts",
        config["topics"]["results"]: "each result",
    }
    chosen = []
    for topic in topics:
        if topic.get("subscription") != "opt_in":
            continue
        label = names.get(topic.get("id")) or (topic.get("name") or "").strip()
        if label and label not in chosen:
            chosen.append(label)
    return chosen


def subscriber_line(email: str, labels: list[str], unsubscribed: bool) -> str:
    if unsubscribed:
        return f"{email} — unsubscribed"
    if not labels:
        return f"{email} — subscribed, with no topics selected"
    return f"{email} — {', '.join(labels)}"


def assess(https_ok: bool, redirects: bool, updated_at: datetime | None, now: datetime) -> tuple[bool, list[str]]:
    lines = []
    if https_ok:
        lines.append("The site is up at https://joannagarland.com and the connection is encrypted.")
    else:
        lines.append("The site did not answer over HTTPS.")
    if redirects:
        lines.append("A visit to the plain address redirects to HTTPS.")
    else:
        lines.append("A visit to the plain address does not redirect to HTTPS.")
    if updated_at is None:
        lines.append("The schedule file could not be read.")
        fresh = False
    else:
        local = updated_at.astimezone(ZONE)
        lines.append(f"The schedule was last updated {local.strftime('%-d %b %Y, %H:%M')} Berlin time.")
        fresh = now - updated_at <= FRESH_FOR
        if not fresh:
            lines.append("That update is older than expected for a daily refresh.")
    return https_ok and redirects and fresh, lines


def status_message(ok: bool, checks: list[str], people: list[str], when: datetime) -> tuple[str, str, str]:
    subject = "Joanna Garland site is up" if ok else "Joanna Garland site needs a look"
    stamp = when.astimezone(ZONE).strftime("%-d %b %Y, %H:%M")
    if people:
        roster = people
    else:
        roster = ["Nobody is subscribed yet."]
    paragraphs = [
        f"Checked {stamp} Berlin time.",
        "Everything checked is in order." if ok else "Something in the check needs a look.",
        *checks,
        "Subscribed addresses:",
        *roster,
    ]
    text = "\n".join(paragraphs + ["", SITE.rstrip("/")])
    rows = "".join(
        "<tr><td style=\"padding-top:12px;font-family:Arial,Helvetica,sans-serif;"
        "font-size:16px;line-height:24px;color:#1c1412;\">"
        f"{escape(line)}</td></tr>"
        for line in paragraphs
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4efe8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4efe8">
<tr><td align="center" style="padding-top:28px;padding-bottom:28px;padding-left:16px;padding-right:16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#fffdf9" style="width:100%;max-width:600px;">
<tr><td style="padding-top:28px;padding-bottom:8px;padding-left:28px;padding-right:28px;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:34px;color:#1c1412;">Joanna Garland</td></tr>
<tr><td style="padding-left:28px;padding-right:28px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:18px;letter-spacing:0.08em;color:#9f2d22;">DAILY CHECK</td></tr>
<tr><td style="padding-left:28px;padding-right:28px;padding-bottom:28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{rows}
<tr><td style="padding-top:22px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:24px;"><a href="{escape(SITE)}" style="color:#9f2d22;">Open the site</a></td></tr>
</table>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    return subject, html, text


def request_json(url: str, api_key: str | None = None):
    headers = {"User-Agent": "joanna-garland-tracker"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def site_https_ok() -> bool:
    try:
        with urllib.request.urlopen(SITE, timeout=20) as response:
            final = response.geturl()
            return response.status == 200 and final.startswith("https://joannagarland.com")
    except (urllib.error.URLError, ssl.SSLError, TimeoutError):
        return False


def http_redirects() -> bool:
    request = urllib.request.Request("http://joannagarland.com/", headers={"User-Agent": "joanna-garland-tracker"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.geturl().startswith("https://joannagarland.com")
    except (urllib.error.URLError, TimeoutError):
        return False


def schedule_updated_at() -> datetime | None:
    try:
        payload = request_json(f"{SITE}data/joanna.json")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, KeyError):
        return None
    raw = payload.get("updatedAt") if isinstance(payload, dict) else None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def list_subscribers(api_key: str, config: dict) -> list[str]:
    segment = config["segmentId"]
    people = []
    after = ""
    while True:
        path = f"https://api.resend.com/segments/{segment}/contacts?limit=100"
        if after:
            path += f"&after={after}"
        page = request_json(path, api_key)
        batch = page.get("data") or []
        for contact in batch:
            email = (contact.get("email") or "").strip()
            if not email:
                continue
            topics = []
            try:
                listed = request_json(f"https://api.resend.com/contacts/{contact['id']}/topics", api_key)
                topics = listed.get("data") or []
            except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError):
                topics = []
            people.append(subscriber_line(email, topic_labels(config, topics), bool(contact.get("unsubscribed"))))
        if not page.get("has_more") or not batch:
            break
        after = batch[-1].get("id") or ""
        if not after:
            break
    return people


def post_email(api_key: str, body: dict, idempotency_key: str) -> bool:
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key[:256],
            "User-Agent": "joanna-garland-tracker",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        print(f"Status note was not sent ({error.code}).")
        return False
    except urllib.error.URLError:
        print("Status note was not sent.")
        return False


def deliver() -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    from_email = os.environ.get("FROM_EMAIL", "").strip()
    owner = os.environ.get("STATUS_EMAIL", "").strip()
    if not api_key or not from_email or not owner:
        print("Status note skipped: mail is not configured.")
        return False
    now = datetime.now(timezone.utc)
    config = load_config()
    ok, checks = assess(site_https_ok(), http_redirects(), schedule_updated_at(), now)
    people = list_subscribers(api_key, config)
    subject, html, text = status_message(ok, checks, people, now)
    sent = post_email(
        api_key,
        {
            "from": from_header(from_email),
            "to": [owner],
            "subject": subject,
            "html": html,
            "text": text,
        },
        f"site-status/{now.astimezone(ZONE).date().isoformat()}",
    )
    print(f"Status note {'sent' if sent else 'not sent'}. {len(people)} subscriber(s).")
    return sent


def _self_check() -> None:
    now = datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)
    fresh = datetime(2026, 11, 3, 13, 16, tzinfo=timezone.utc)
    ok, lines = assess(True, True, fresh, now)
    assert ok, lines
    assert any("encrypted" in line for line in lines)
    stale = datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc)
    bad, bad_lines = assess(True, False, stale, now)
    assert not bad
    assert any("does not redirect" in line for line in bad_lines)
    assert any("older than expected" in line for line in bad_lines)
    missing, _ = assess(False, True, None, now)
    assert not missing
    config = {
        "topics": {"starts": "start-id", "results": "result-id"},
    }
    labels = topic_labels(config, [
        {"id": "start-id", "subscription": "opt_in"},
        {"id": "result-id", "subscription": "opt_out"},
    ])
    assert labels == ["tournament starts"]
    assert subscriber_line("fan@example.com", labels, False) == "fan@example.com — tournament starts"
    subject, html, text = status_message(True, ["The site is up."], ["fan@example.com — tournament starts"], now)
    assert subject == "Joanna Garland site is up"
    assert "fan@example.com" in text and "fan@example.com" in html
    assert "Unsubscribe" not in html
    print("Status note checks passed.")


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        _self_check()
    else:
        raise SystemExit(0 if deliver() else 1)
