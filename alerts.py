"""Morning alerts for a tournament start and for each new singles result.

Nothing is sent unless RESEND_API_KEY and FROM_EMAIL are set. Addresses are
never written into this repository; only the ids of alerts already sent are.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "alerts.config.json"
SENT_PATH = ROOT / "public" / "data" / "alerts-sent.json"
ZONE = ZoneInfo("Europe/Berlin")
RESULT_CAP = 12


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_sent() -> dict:
    if not SENT_PATH.exists():
        return {"starts": [], "results": []}
    sent = json.loads(SENT_PATH.read_text(encoding="utf-8"))
    sent.setdefault("starts", [])
    sent.setdefault("results", [])
    return sent


def save_sent(sent: dict) -> None:
    SENT_PATH.write_text(json.dumps(sent, indent=2) + "\n", encoding="utf-8")


def parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def match_key(tournament_id: str, match: dict) -> str:
    qualifying = "q" if match.get("qualifying") else "m"
    opponent = " ".join((match.get("opponent") or "").split()).casefold()
    return f"{tournament_id}|{match.get('round')}|{qualifying}|{opponent}"


def collect_matches(payload: dict | None) -> dict:
    found = {}
    for tournament in (payload or {}).get("tournaments") or []:
        for match in tournament.get("matches") or []:
            if not (match.get("opponent") or "").strip():
                continue
            if not (match.get("score") or "").strip():
                continue
            found[match_key(tournament["id"], match)] = (tournament, match)
    return found


def plan_alerts(previous: dict | None, current: dict, sent: dict, today=None) -> tuple[list, list]:
    """Return (start events, new result tuples) that have not been sent.

    With no earlier schedule file, the archive is not treated as news.
    A start alert fires once, on the morning of the start or the morning before.
    """
    today = today or datetime.now(ZONE).date()
    sent_starts = set(sent.get("starts") or [])
    starts = []
    for event in current.get("schedule") or []:
        if event.get("phase") == "withdrawn" or event.get("id") in sent_starts:
            continue
        start = parse_date(event.get("start"))
        if start in (today, today + timedelta(days=1)):
            starts.append(event)

    sent_results = set(sent.get("results") or [])
    results = []
    if previous is not None:
        earlier = set(collect_matches(previous))
        for key, (tournament, match) in collect_matches(current).items():
            if key in earlier or key in sent_results:
                continue
            results.append((key, tournament, match))
    return starts, results


def place_name(event: dict) -> str:
    city = (event.get("city") or "").strip()
    return city or (event.get("name") or "the tournament")


def pretty_score(score: str) -> str:
    return ", ".join(part.strip() for part in (score or "").split(",") if part.strip())


def mailing_line() -> str:
    address = os.environ.get("MAILING_ADDRESS", "").strip()
    if address:
        return address
    return ""


def shell(kicker: str, paragraphs: list[str], link: str, link_label: str) -> tuple[str, str]:
    site = load_config().get("siteUrl", "").rstrip("/")
    address = mailing_line()
    text_parts = [kicker, ""]
    text_parts.extend(paragraphs)
    text_parts.extend(["", f"{link_label}: {link}", "", "Unsubscribe: {{{RESEND_UNSUBSCRIBE_URL}}}"])
    if address:
        text_parts.extend(["", address])
    text_parts.append(site)
    text = "\n".join(text_parts)

    rows = []
    for paragraph in paragraphs:
        rows.append(
            "<tr><td style=\"padding-top:14px;font-family:Arial,Helvetica,sans-serif;"
            "font-size:16px;line-height:24px;color:#1c1412;\">"
            f"{escape(paragraph)}</td></tr>"
        )
    address_row = ""
    if address:
        address_row = (
            "<tr><td style=\"padding-top:8px;font-family:Arial,Helvetica,sans-serif;"
            "font-size:13px;line-height:20px;color:#5e5148;\">"
            f"{escape(address)}</td></tr>"
        )
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{escape(kicker)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4efe8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4efe8">
<tr><td align="center" style="padding-top:28px;padding-bottom:28px;padding-left:16px;padding-right:16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background-color:#fffdf9;" bgcolor="#fffdf9">
<tr><td style="padding-top:28px;padding-bottom:8px;padding-left:28px;padding-right:28px;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:34px;color:#1c1412;">Joanna Garland</td></tr>
<tr><td style="padding-left:28px;padding-right:28px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:18px;letter-spacing:0.08em;color:#9f2d22;">{escape(kicker.upper())}</td></tr>
<tr><td style="padding-left:28px;padding-right:28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{''.join(rows)}
<tr><td style="padding-top:22px;padding-bottom:8px;">
<a href="{escape(link)}" style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:24px;color:#9f2d22;">{escape(link_label)}</a>
</td></tr>
</table>
</td></tr>
<tr><td style="padding-top:18px;padding-bottom:28px;padding-left:28px;padding-right:28px;border-top:1px solid #e3d5c6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:20px;color:#5e5148;">You asked for these alerts. <a href="{{{{{{RESEND_UNSUBSCRIBE_URL}}}}}}" style="color:#9f2d22;">Unsubscribe</a></td></tr>
{address_row}
<tr><td style="padding-top:8px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:20px;color:#5e5148;"><a href="{escape(site)}/" style="color:#5e5148;">{escape(site)}</a></td></tr>
</table>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    return html_body, text


def start_message(event: dict, today) -> tuple[str, str, str]:
    place = place_name(event)
    start = parse_date(event.get("start"))
    when = "today" if start == today else "tomorrow"
    subject = f"Joanna Garland {'plays today' if when == 'today' else 'starts tomorrow'} at {place}"
    bits = [event.get("name") or place]
    if event.get("level"):
        bits.append(event["level"])
    if event.get("surface"):
        bits.append(event["surface"])
    detail = " · ".join(bits)
    entry = event.get("entryStatus") or ""
    lines = [f"She {('plays today' if when == 'today' else 'starts tomorrow')} at {place}.", detail]
    if entry:
        lines.append(entry)
    html_body, text = shell("Tournament start", lines, event.get("url") or load_config()["siteUrl"], "Open the tournament")
    return subject, html_body, text


def result_message(tournament: dict, match: dict) -> tuple[str, str, str]:
    opponent = match.get("opponent") or "her opponent"
    verb = "beat" if match.get("won") else "lost to"
    subject = f"Joanna Garland {verb} {opponent}"
    score = pretty_score(match.get("score") or "")
    if match.get("retirement"):
        score = f"{score} ret.".strip()
    round_label = match.get("roundLabel") or ""
    if match.get("qualifying") and round_label and not round_label.lower().startswith("qualifying"):
        round_label = f"Qualifying, {round_label}"
    where = " · ".join(part for part in [tournament.get("name"), tournament.get("level"), round_label] if part)
    lines = [f"She {verb} {opponent}."]
    if score:
        lines.append(score)
    if where:
        lines.append(where)
    link = tournament.get("url") or load_config()["siteUrl"]
    html_body, text = shell("Result", lines, link, "Open the tournament")
    return subject, html_body, text


def digest_message(results: list, site: str) -> tuple[str, str, str]:
    subject = f"Joanna Garland: {len(results)} new results"
    lines = ["Several results arrived together. The latest ones are on the site."]
    for _key, tournament, match in results[:8]:
        verb = "beat" if match.get("won") else "lost to"
        lines.append(f"{tournament.get('name')}: {verb} {match.get('opponent')}")
    if len(results) > 8:
        lines.append(f"And {len(results) - 8} more.")
    html_body, text = shell("Results", lines, site, "Open the results")
    return subject, html_body, text


def post_broadcast(api_key: str, body: dict, idempotency_key: str) -> bool:
    request = urllib.request.Request(
        "https://api.resend.com/broadcasts",
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
        detail = error.read().decode("utf-8", "replace")[:400]
        print(f"Alert was not sent ({error.code}): {detail}")
        return False
    except urllib.error.URLError as error:
        print(f"Alert was not sent: {error.reason}")
        return False


def from_header(from_email: str) -> str:
    if "<" in from_email:
        return from_email
    return f"Joanna Garland tracker <{from_email}>"


def deliver(previous: dict | None, current: dict) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    from_email = os.environ.get("FROM_EMAIL", "").strip()
    if not api_key or not from_email:
        print("Alerts skipped: mail is not configured.")
        return
    config = load_config()
    sent = load_sent()
    today = datetime.now(ZONE).date()
    starts, results = plan_alerts(previous, current, sent, today)
    if not starts and not results:
        print("Alerts: nothing new.")
        return
    sender = from_header(from_email)
    changed = False
    for event in starts:
        subject, html_body, text = start_message(event, today)
        ok = post_broadcast(
            api_key,
            {
                "name": f"start {event['id']}"[:80],
                "from": sender,
                "subject": subject[:200],
                "html": html_body,
                "text": text,
                "segment_id": config["segmentId"],
                "topic_id": config["topics"]["starts"],
                "send": True,
            },
            f"start/{event['id']}",
        )
        if ok:
            sent["starts"].append(event["id"])
            changed = True
            save_sent(sent)
    if len(results) > RESULT_CAP:
        subject, html_body, text = digest_message(results, config["siteUrl"])
        stamp = current.get("updatedAt") or today.isoformat()
        ok = post_broadcast(
            api_key,
            {
                "name": f"results {stamp}"[:80],
                "from": sender,
                "subject": subject[:200],
                "html": html_body,
                "text": text,
                "segment_id": config["segmentId"],
                "topic_id": config["topics"]["results"],
                "send": True,
            },
            f"results-digest/{stamp}",
        )
        if ok:
            for key, _tournament, _match in results:
                sent["results"].append(key)
            changed = True
            save_sent(sent)
    else:
        for key, tournament, match in results:
            subject, html_body, text = result_message(tournament, match)
            ok = post_broadcast(
                api_key,
                {
                    "name": f"result {key}"[:80],
                    "from": sender,
                    "subject": subject[:200],
                    "html": html_body,
                    "text": text,
                    "segment_id": config["segmentId"],
                    "topic_id": config["topics"]["results"],
                    "send": True,
                },
                f"result/{key}",
            )
            if ok:
                sent["results"].append(key)
                changed = True
                save_sent(sent)
    if not changed and (starts or results):
        print("Alerts were not marked sent.")


def _self_check() -> None:
    from datetime import date

    today = date(2026, 11, 8)
    previous = {
        "schedule": [],
        "tournaments": [
            {
                "id": "2026-1",
                "name": "Chennai Open",
                "matches": [{"round": "R32", "qualifying": False, "opponent": "Jil Teichmann", "won": True, "score": "6-3,6-4"}],
            }
        ],
    }
    current = {
        "schedule": [
            {"id": "wta-old", "phase": "now", "start": "2026-11-02", "name": "Chennai Open", "city": "Chennai"},
            {"id": "itf-brisbane", "phase": "upcoming", "start": "2026-11-09", "name": "W75 Brisbane", "city": ""},
            {"id": "itf-gone", "phase": "withdrawn", "start": "2026-11-09", "name": "W50 Yokohama"},
        ],
        "tournaments": [
            {
                "id": "2026-1",
                "name": "Chennai Open",
                "level": "WTA 250",
                "url": "https://example.test/chennai",
                "matches": [
                    {"round": "R32", "roundLabel": "Round of 32", "qualifying": False, "opponent": "Jil Teichmann", "won": True, "score": "6-3,6-4"},
                    {"round": "R16", "roundLabel": "Round of 16", "qualifying": False, "opponent": "A Player", "won": False, "score": "3-6,4-6"},
                    {"round": "SF", "roundLabel": "Semifinal", "qualifying": False, "opponent": "Janice Tjen", "won": False, "score": ""},
                ],
            }
        ],
    }
    starts, results = plan_alerts(previous, current, {"starts": [], "results": []}, today)
    assert [item["id"] for item in starts] == ["itf-brisbane"], starts
    assert [item[0] for item in results] == ["2026-1|R16|m|a player"], results
    again, _ = plan_alerts(previous, current, {"starts": ["itf-brisbane"], "results": []}, today)
    assert again == []
    no_archive, _ = plan_alerts(None, current, {"starts": [], "results": []}, today)
    assert len(no_archive) == 1
    subject, html_body, _text = result_message(current["tournaments"][0], current["tournaments"][0]["matches"][1])
    assert subject == "Joanna Garland lost to A Player"
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html_body
    assert "{{RESEND_UNSUBSCRIBE_URL}}" not in html_body.replace("{{{RESEND_UNSUBSCRIBE_URL}}}", "")
    print("Alert planner checks passed.")


if __name__ == "__main__":
    _self_check()
