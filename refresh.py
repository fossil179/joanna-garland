#!/usr/bin/env python3
"""Refresh Joanna Garland's schedule and results into public/data/joanna.json.

Uses the WTA site's public JSON feed for rankings, entry lists, and match
history, and the public ITF acceptance-list feed for upcoming ITF entries.
Official sites remain the source of record.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "public" / "data" / "joanna.json"

WTA_ID = "326512"
ITF_ID = "800446517"
PLAYER_NAME = "Joanna Garland"
TODAY = date.today()
LOOKAHEAD_DAYS = 140

WTA_PROFILE = "https://www.wtatennis.com/players/326512/joanna-garland"
ITF_PROFILE = "https://www.world.tennis/en/players/joanna-garland/800446517/tpe/wt/s/overview/"
ITF_ENTRIES = f"https://itf-entries.netlify.app/api/player/{ITF_ID}"

ROUND_LABEL = {
    "R128": "Round of 128",
    "R64": "Round of 64",
    "R32": "Round of 32",
    "R16": "Round of 16",
    "QF": "Quarterfinal",
    "SF": "Semifinal",
    "F": "Final",
    "Q": "Qualifying",
}
ROUND_RANK = {
    "Q": 0,
    "R128": 1,
    "R64": 2,
    "R32": 3,
    "R16": 4,
    "QF": 5,
    "SF": 6,
    "F": 7,
}
DRAW_LADDER = {
    128: ["R128", "R64", "R32", "R16", "QF", "SF", "F"],
    96: ["R128", "R64", "R32", "R16", "QF", "SF", "F"],
    64: ["R64", "R32", "R16", "QF", "SF", "F"],
    48: ["R64", "R32", "R16", "QF", "SF", "F"],
    32: ["R32", "R16", "QF", "SF", "F"],
    28: ["R32", "R16", "QF", "SF", "F"],
    16: ["R16", "QF", "SF", "F"],
}

ENTRY_LABEL = {
    "MD DA": "Main draw direct acceptance",
    "MD WC": "Main draw wildcard",
    "MD Q": "Main draw qualifier",
    "MD": "Main draw",
    "QU": "Qualifying",
    "Q": "Qualifying",
    "ALT": "Alternate",
    "WD": "Withdrawn",
    "SE": "Special exempt",
    "LL": "Lucky loser",
    "WC": "Wildcard",
}


def http_json(url: str, headers: dict | None = None, timeout: int = 45):
    req_headers = {
        "Accept": "application/json",
        "User-Agent": "JoannaGarlandTracker/1.0 (personal schedule tracker)",
    }
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, headers=req_headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def iso_date(value: str | None) -> str | None:
    if not value:
        return None
    return value[:10]


def parse_date(value: str | None) -> date | None:
    text = iso_date(value)
    if not text:
        return None
    return date.fromisoformat(text)


def title_place(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    if cleaned.isupper():
        return cleaned.title()
    return cleaned


def slugify(value: str) -> str:
    name = value.split(" - ")[0].lower()
    name = name.replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def wta_tournament_url(group_id: int, title: str) -> str:
    return f"https://www.wtatennis.com/tournaments/{group_id}/{slugify(title)}"


def age_on(dob: str, on: date) -> int:
    born = date.fromisoformat(dob)
    years = on.year - born.year
    if (on.month, on.day) < (born.month, born.day):
        years -= 1
    return years


def she_won(match: dict) -> bool:
    if str(match.get("player_1")) == WTA_ID:
        return match.get("winner") == 1
    return match.get("winner") == 2


def is_bye(match: dict) -> bool:
    return match.get("opponent") is None and match.get("reason_code") == "B"


def round_code(match: dict) -> str:
    raw = (match.get("round_name") or "").strip()
    qualifying = match.get("qpm_flag") == "Q"
    if raw == "Q" and not qualifying:
        return "QF"
    if raw == "S":
        return "SF"
    if raw == "Q" and qualifying:
        return "Q"
    return raw or "Q"


def round_label(code: str, qualifying: bool) -> str:
    base = ROUND_LABEL.get(code, code)
    if not qualifying:
        return base
    if code in {"Q", "QF"}:
        return "Qualifying"
    lowered = base[0].lower() + base[1:] if base else "round"
    return f"Qualifying {lowered}"


def flip_set(token: str) -> str:
    matched = re.match(r"(\d+)-(\d+)(\([^)]*\))?$", token)
    if not matched:
        return token
    left, right, tiebreak = matched.groups()
    return f"{right}-{left}{tiebreak or ''}"


def format_score(match: dict, won: bool) -> str:
    parts = [part for part in re.split(r"\s+", (match.get("scores") or "").strip()) if part]
    if not won:
        parts = [flip_set(part) for part in parts]
    score = " ".join(parts)
    if match.get("reason_code") == "R" and score and "ret" not in score.lower():
        score = f"{score} ret."
    return score


def opponent_name(match: dict) -> str:
    opponent = match.get("opponent") or {}
    if opponent.get("fullName"):
        return opponent["fullName"]
    team = match.get("team_name_2") or "Unknown"
    # "PARRY,  DIANE  FRA" -> Diane Parry
    bits = [part.strip() for part in team.split(",") if part.strip()]
    if len(bits) >= 2:
        last = bits[0].title()
        rest = bits[1].split()
        country_codes = {"TPE", "USA", "GBR", "FRA", "CHN", "AUS"}
        while rest and rest[-1].upper() in country_codes or (len(rest[-1]) == 3 and rest[-1].isupper()):
            rest.pop()
        first = " ".join(part.title() for part in rest)
        return f"{first} {last}".strip()
    return team.title()


def tournament_name(match: dict) -> str:
    tournament = match.get("tournament") or {}
    title = (tournament.get("title") or "").strip()
    short = (match.get("TournamentName") or "").strip().title()
    city = title_place(tournament.get("city"))
    if " - " in title:
        left, right = title.split(" - ", 1)
        left = left.strip()
        generic = any(token in left.upper() for token in ("ITF", "WTT", "$"))
        place = title_place(right.split(",")[0])
        if left and not generic:
            return left
        if short:
            return short
        if place:
            level = left.replace("WTT", "").strip()
            return f"{level} {place}".strip()
    if short and city and city.lower() not in short.lower():
        return f"{short}, {city}"
    return short or city or title or "Tournament"


def level_name(match: dict) -> str:
    tournament = match.get("tournament") or {}
    level = tournament.get("level")
    if level and level not in {"C", "IS", "P", "PM", "GS", "ITF"}:
        return level
    code = match.get("TournamentLevel")
    return {
        "GS": "Grand Slam",
        "PM": "WTA 1000",
        "P": "WTA 500",
        "IS": "WTA 250",
        "C": "WTA 125",
        "ITF": "ITF",
    }.get(code, level or code or "Tennis")


def surface_name(match: dict) -> str:
    tournament = match.get("tournament") or {}
    surface = tournament.get("surface") or match.get("Surface") or ""
    return surface.strip().title()


def fetch_ranking() -> dict:
    found = None
    for page in range(0, 12):
        rows = http_json(
            "https://api.wtatennis.com/tennis/players/ranked"
            f"?type=rankSingles&metric=singles&page={page}&pageSize=100"
        )
        if not rows:
            break
        for row in rows:
            player = row.get("player") or {}
            if str(player.get("id")) == WTA_ID:
                found = row
                break
        if found or len(rows) < 100:
            break
    if not found:
        raise RuntimeError("Joanna Garland was not found in the WTA singles rankings.")
    return {
        "ranking": found.get("ranking"),
        "points": found.get("points"),
        "movement": found.get("movement"),
        "rankedAt": iso_date(found.get("rankedAt")),
        "tournamentsPlayed": found.get("tournamentsPlayed"),
    }


def fetch_matches() -> list[dict]:
    matches = []
    seen = set()
    page = 0
    while page < 30:
        payload = http_json(
            "https://api.wtatennis.com/tennis/players/"
            f"{WTA_ID}/matches?page={page}&pageSize=100"
        )
        batch = payload.get("matches") or []
        if not batch:
            break
        added = 0
        for match in batch:
            if match.get("s_d_flag") != "S":
                continue
            key = (
                match.get("tourn_year"),
                match.get("tourn_nbr"),
                match.get("tourn_round"),
                match.get("round_name"),
                match.get("qpm_flag"),
                match.get("player_2"),
                match.get("scores"),
            )
            if key in seen:
                continue
            seen.add(key)
            matches.append(match)
            added += 1
        if len(batch) < 100 or added == 0:
            break
        page += 1
    return matches


def fetch_itf_entries() -> tuple[list[dict], str | None]:
    try:
        payload = http_json(ITF_ENTRIES, headers={"Cookie": "itf_h=1"})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return [], str(error)
    fields = payload.get("fields") or []
    entries = []
    for row in payload.get("entries") or []:
        entries.append(dict(zip(fields, row)))
    return entries, None


def fetch_wta_tournaments() -> list[dict]:
    start = TODAY.replace(day=1).isoformat()
    end_ordinal = TODAY.toordinal() + LOOKAHEAD_DAYS
    end = date.fromordinal(end_ordinal).isoformat()
    payload = http_json(
        "https://api.wtatennis.com/tennis/tournaments"
        f"?page=0&pageSize=100&from={start}&to={end}"
    )
    tournaments = []
    for tournament in payload.get("content") or []:
        if tournament.get("level") == "ITF":
            continue
        end_date = parse_date(tournament.get("endDate"))
        if end_date and end_date < TODAY and tournament.get("status") not in {"live", "inProgress"}:
            continue
        tournaments.append(tournament)
    return tournaments


def player_in_event(event: dict) -> dict | None:
    for entry in event.get("eventPlayers") or []:
        players = entry.get("players") or []
        if any(str(player.get("id")) == WTA_ID for player in players):
            return entry
    return None


def fetch_wta_entries(tournaments: list[dict]) -> tuple[list[dict], list[str]]:
    found = []
    errors = []
    for tournament in tournaments:
        group_id = tournament["tournamentGroup"]["id"]
        year = tournament["year"]
        url = f"https://api.wtatennis.com/tennis/tournaments/{group_id}/{year}/players"
        try:
            payload = http_json(url)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                errors.append(f"{tournament.get('title')}: {error}")
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            errors.append(f"{tournament.get('title')}: {error}")
            continue
        time.sleep(0.12)
        for event in payload.get("events") or []:
            code = (event.get("eventTypeCode") or "").upper()
            if code and not code.endswith("S"):
                continue
            entry = player_in_event(event)
            if not entry:
                continue
            found.append({"tournament": tournament, "eventCode": code or "LS", "entry": entry})
    return found, errors


def fetch_live_matches(group_id: int, year: int) -> list[dict]:
    payload = http_json(
        f"https://api.wtatennis.com/tennis/tournaments/{group_id}/{year}/matches"
    )
    hers = []
    for match in payload.get("matches") or []:
        ids = {str(match.get("PlayerIDA") or ""), str(match.get("PlayerIDB") or "")}
        if WTA_ID in ids:
            hers.append(match)
    return hers


def live_round_code(round_id: str, draw_size: int | None, qualifying: bool) -> str:
    if qualifying:
        return "Q"
    try:
        index = int(round_id) - 1
    except (TypeError, ValueError):
        return "R32"
    ladder = DRAW_LADDER.get(draw_size or 32, DRAW_LADDER[32])
    if 0 <= index < len(ladder):
        return ladder[index]
    return "R32"


def garland_won_live(match: dict) -> bool:
    result = match.get("ResultString") or ""
    lowered = result.lower()
    if "garland d" in lowered or lowered.startswith("j. garland"):
        return True
    if "d j. garland" in lowered or "d garland" in lowered:
        return False
    winner = str(match.get("Winner") or "")
    if str(match.get("PlayerIDA")) == WTA_ID and winner in {"1", "A"}:
        return True
    if str(match.get("PlayerIDB")) == WTA_ID and winner in {"2", "B"}:
        return False
    return "garland" in lowered and " d " in f" {lowered} " and lowered.find("garland") < lowered.find(" d ")


def live_opponent(match: dict) -> str:
    if str(match.get("PlayerIDA")) == WTA_ID:
        first = match.get("PlayerNameFirstB") or ""
        last = match.get("PlayerNameLastB") or ""
        country = match.get("PlayerCountryB") or ""
    else:
        first = match.get("PlayerNameFirstA") or ""
        last = match.get("PlayerNameLastA") or ""
        country = match.get("PlayerCountryA") or ""
    name = f"{first} {last}".strip() or "Unknown"
    return name, country


def build_history(matches: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for match in matches:
        if is_bye(match):
            continue
        key = (match.get("tourn_year"), str(match.get("tourn_nbr")))
        groups[key].append(match)

    tournaments = []
    for (year, number), rows in groups.items():
        rows.sort(key=lambda match: (0 if match.get("qpm_flag") == "Q" else 1, int(match.get("tourn_round") or 0)))
        sample = rows[-1]
        tournament = sample.get("tournament") or {}
        parsed = []
        for match in rows:
            code = round_code(match)
            qualifying = match.get("qpm_flag") == "Q"
            won = she_won(match)
            opponent = match.get("opponent") or {}
            rank = match.get("rank_2") if str(match.get("player_1")) == WTA_ID else match.get("rank_1")
            if not isinstance(rank, int) or rank <= 0 or rank >= 1500:
                rank = None
            parsed.append(
                {
                    "round": code,
                    "roundLabel": round_label(code, qualifying),
                    "qualifying": qualifying,
                    "opponent": opponent_name(match),
                    "opponentCountry": opponent.get("countryCode") or "",
                    "opponentRank": rank,
                    "score": format_score(match, won),
                    "won": won,
                    "retirement": match.get("reason_code") == "R",
                    "order": int(match.get("tourn_round") or 0),
                }
            )
        main = [match for match in parsed if not match["qualifying"]]
        pool = main or parsed
        best = max(pool, key=lambda match: (ROUND_RANK.get(match["round"], 0), match["order"]))
        won_final = any(match["round"] == "F" and match["won"] and not match["qualifying"] for match in parsed)
        if won_final:
            outcome = "Champion"
        elif best["round"] == "F":
            outcome = "Finalist"
        elif not main:
            outcome = "Lost in qualifying" if any(not match["won"] for match in pool) else "Qualifying"
        elif best["won"]:
            outcome = f"Reached the {best['roundLabel'].lower()}"
        else:
            outcome = f"Lost in the {best['roundLabel'].lower()}"
        wins = sum(1 for match in parsed if match["won"])
        losses = sum(1 for match in parsed if not match["won"])
        start = iso_date(tournament.get("startDate")) or iso_date(sample.get("StartDate"))
        tournaments.append(
            {
                "id": f"{year}-{number}",
                "year": int(year) if str(year).isdigit() else start[:4] if start else None,
                "name": tournament_name(sample),
                "level": level_name(sample),
                "surface": surface_name(sample),
                "indoor": (tournament.get("inOutdoor") or "") == "I",
                "city": title_place(tournament.get("city")),
                "country": tournament.get("country") or "",
                "start": start,
                "end": iso_date(tournament.get("endDate")),
                "wins": wins,
                "losses": losses,
                "outcome": outcome,
                "champion": won_final,
                "inProgress": False,
                "url": wta_tournament_url(int(number), tournament.get("title") or sample.get("TournamentName") or ""),
                "matches": parsed,
            }
        )
    tournaments.sort(key=lambda item: item.get("start") or "", reverse=True)
    return tournaments


def summarize(tournaments: list[dict], season: int) -> dict:
    def tally(rows: list[dict]) -> dict:
        wins = sum(item["wins"] for item in rows)
        losses = sum(item["losses"] for item in rows)
        titles = sum(1 for item in rows if item["champion"])
        return {"wins": wins, "losses": losses, "titles": titles, "played": len(rows)}

    countable = [item for item in tournaments if not item.get("provisional")]
    season_rows = [item for item in countable if item.get("year") == season]
    surfaces = {}
    levels = {}
    for item in season_rows:
        for bucket, key in ((surfaces, item.get("surface") or "Unknown"), (levels, item.get("level") or "Other")):
            current = bucket.setdefault(key, {"wins": 0, "losses": 0})
            current["wins"] += item["wins"]
            current["losses"] += item["losses"]
    return {
        "season": season,
        "seasonRecord": tally(season_rows),
        "careerRecord": tally(countable),
        "seasonSurfaces": surfaces,
        "seasonLevels": levels,
    }


def entry_status_label(code: str, entry_type: str | None = None) -> str:
    cleaned = (code or "").strip().upper()
    if cleaned in ENTRY_LABEL:
        return ENTRY_LABEL[cleaned]
    typed = (entry_type or "").strip().upper()
    if typed in ENTRY_LABEL:
        return ENTRY_LABEL[typed]
    if typed in {"Q", "QPM"}:
        return "Qualifying"
    if typed == "WC":
        return "Wildcard"
    if typed == "LL":
        return "Lucky loser"
    if not cleaned and not typed:
        return "Main draw"
    return cleaned or typed or "Entered"


def build_schedule(wta_entries: list[dict], itf_entries: list[dict], history_ids: set[str]) -> list[dict]:
    schedule = []
    seen_wta = set()
    for item in wta_entries:
        tournament = item["tournament"]
        entry = item["entry"]
        start = parse_date(tournament.get("startDate"))
        end = parse_date(tournament.get("endDate"))
        status = tournament.get("status") or ""
        in_play = status in {"live", "inProgress"} or (
            start is not None and end is not None and start <= TODAY <= end
        )
        if end and end < TODAY and not in_play:
            continue
        phase = "now" if in_play else "upcoming"
        group_id = tournament["tournamentGroup"]["id"]
        year = tournament["year"]
        if (year, group_id) in seen_wta:
            continue
        seen_wta.add((year, group_id))
        live_matches = []
        next_round = None
        still_in = not bool(entry.get("eliminated"))
        if phase == "now":
            try:
                raw_matches = fetch_live_matches(group_id, year)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                raw_matches = []
            draw_size = tournament.get("singlesDrawSize")
            parsed = []
            for match in raw_matches:
                qualifying = (match.get("DrawLevelType") or "").upper() == "Q"
                code = live_round_code(match.get("RoundID"), draw_size, qualifying)
                name, country = live_opponent(match)
                won = garland_won_live(match)
                score = match.get("ScoreString") or ""
                if not won and score:
                    score = ", ".join(flip_set(part.strip()) for part in score.split(","))
                parsed.append(
                    {
                        "round": code,
                        "roundLabel": round_label(code, qualifying),
                        "qualifying": qualifying,
                        "opponent": name,
                        "opponentCountry": country,
                        "score": score,
                        "won": won,
                        "state": match.get("MatchState") or "",
                        "order": int(match.get("RoundID") or 0),
                        "stamp": match.get("MatchTimeStamp") or "",
                    }
                )
            parsed.sort(key=lambda match: (match["order"], match["stamp"]))
            live_matches = parsed
            if parsed and still_in:
                last = parsed[-1]
                if last["won"] and last["state"] == "F" and last["round"] != "F":
                    ladder = DRAW_LADDER.get(draw_size or 32, DRAW_LADDER[32])
                    if last["round"] in ladder:
                        index = ladder.index(last["round"])
                        if index + 1 < len(ladder):
                            next_round = ROUND_LABEL[ladder[index + 1]]
        seed = (entry.get("seed") or "").strip() or None
        schedule.append(
            {
                "id": f"wta-{year}-{group_id}",
                "tour": "WTA",
                "name": (tournament.get("title") or "").split(" - ")[0],
                "level": tournament.get("level") or "",
                "city": title_place(tournament.get("city")),
                "country": tournament.get("country") or "",
                "surface": (tournament.get("surface") or "").title(),
                "indoor": tournament.get("inOutdoor") == "I",
                "start": iso_date(tournament.get("startDate")),
                "end": iso_date(tournament.get("endDate")),
                "phase": phase,
                "entryStatus": entry_status_label("", entry.get("entryType")),
                "seed": seed,
                "acceptancePosition": None,
                "priority": None,
                "note": "",
                "eliminated": bool(entry.get("eliminated")),
                "stillIn": still_in,
                "nextRound": next_round,
                "liveMatches": live_matches,
                "historyId": f"{year}-{group_id}" if f"{year}-{group_id}" in history_ids else None,
                "url": wta_tournament_url(group_id, tournament.get("title") or ""),
            }
        )

    for entry in itf_entries:
        group = (entry.get("entrygroup") or "").strip()
        start = iso_date(entry.get("startdate"))
        start_date = parse_date(start)
        if start_date and start_date < TODAY and group.upper() != "WD":
            # Acceptance lists linger briefly after the event. Keep only current and future weeks.
            if start_date.toordinal() + 7 < TODAY.toordinal():
                continue
        phase = "withdrawn" if group.upper() == "WD" else "upcoming"
        if start_date and start_date <= TODAY and phase != "withdrawn":
            phase = "now"
        end = (start_date + timedelta(days=6)).isoformat() if start_date else None
        key = (entry.get("tournamentkey") or "").lower()
        link = entry.get("tournamentlink") or ""
        schedule.append(
            {
                "id": f"itf-{key}",
                "tour": "ITF",
                "name": entry.get("tournamentname") or "ITF tournament",
                "level": (entry.get("tournamentname") or "").split(" ")[0],
                "city": "",
                "country": entry.get("hostnationcode") or "",
                "surface": {"H": "Hard", "C": "Clay", "G": "Grass", "A": "Carpet"}.get(
                    entry.get("surfacecode") or "", entry.get("surfacecode") or ""
                ),
                "indoor": (entry.get("indoororoutdoor") or "").lower() == "indoor",
                "start": start,
                "end": end,
                "datesLabel": entry.get("dates") or "",
                "phase": phase,
                "entryStatus": entry_status_label(group),
                "seed": None,
                "acceptancePosition": entry.get("position") or None,
                "priority": entry.get("priority") or None,
                "note": entry.get("information") or "",
                "wtaRankAtEntry": entry.get("playeratpwtarank") or None,
                "eliminated": False,
                "stillIn": phase != "withdrawn",
                "nextRound": None,
                "liveMatches": [],
                "historyId": None,
                "url": f"https://www.world.tennis{link}" if link else ITF_PROFILE,
            }
        )

    order = {"now": 0, "upcoming": 1, "withdrawn": 2}
    schedule.sort(key=lambda item: (order.get(item["phase"], 9), item.get("start") or "9999", item.get("priority") or 99))
    return schedule


def merge_live_results(tournaments: list[dict], schedule: list[dict]) -> list[dict]:
    by_id = {item["id"]: item for item in tournaments}
    for event in schedule:
        if event["tour"] != "WTA" or not event["liveMatches"]:
            continue
        history_id = event["id"].replace("wta-", "")
        if history_id in by_id:
            continue
        matches = []
        for match in event["liveMatches"]:
            matches.append(
                {
                    "round": match["round"],
                    "roundLabel": match["roundLabel"],
                    "qualifying": match["qualifying"],
                    "opponent": match["opponent"],
                    "opponentCountry": match.get("opponentCountry") or "",
                    "opponentRank": None,
                    "score": match.get("score") or "",
                    "won": match["won"],
                    "retirement": "ret" in (match.get("score") or "").lower(),
                    "order": match["order"],
                }
            )
        wins = sum(1 for match in matches if match["won"])
        losses = sum(1 for match in matches if not match["won"])
        if event["stillIn"] and event.get("nextRound"):
            outcome = f"In the {event['nextRound'].lower()}"
        elif event["eliminated"]:
            outcome = matches[-1]["roundLabel"] if matches else "Out"
        else:
            outcome = "In progress"
        by_id[history_id] = {
            "id": history_id,
            "year": int(event["start"][:4]) if event.get("start") else TODAY.year,
            "name": event["name"],
            "level": event["level"],
            "surface": event["surface"],
            "indoor": event["indoor"],
            "city": event["city"],
            "country": event["country"],
            "start": event["start"],
            "end": event["end"],
            "wins": wins,
            "losses": losses,
            "outcome": outcome,
            "champion": False,
            "inProgress": True,
            "url": event["url"],
            "matches": matches,
        }
    merged = list(by_id.values())
    merged.sort(key=lambda item: item.get("start") or "", reverse=True)
    return merged


def build() -> dict:
    warnings = []
    ranking = fetch_ranking()
    matches = fetch_matches()
    itf_entries, itf_error = fetch_itf_entries()
    if itf_error:
        warnings.append(f"ITF acceptance lists could not be loaded: {itf_error}")
    wta_tournaments = fetch_wta_tournaments()
    wta_entries, wta_errors = fetch_wta_entries(wta_tournaments)
    warnings.extend(wta_errors[:5])

    history = build_history(matches)
    history_ids = {item["id"] for item in history}
    schedule = build_schedule(wta_entries, itf_entries, history_ids)
    history = merge_live_results(history, schedule)
    summary = summarize(history, TODAY.year)
    dob = "2001-07-16"
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "player": {
            "name": PLAYER_NAME,
            "country": "TPE",
            "countryName": "Chinese Taipei",
            "dob": dob,
            "age": age_on(dob, TODAY),
            "handed": "Right-handed",
            "wtaId": int(WTA_ID),
            "itfId": ITF_ID,
            **ranking,
            "links": {
                "wta": WTA_PROFILE,
                "itf": ITF_PROFILE,
                "itfEntries": f"https://itf-entries.netlify.app/player/{ITF_ID}",
            },
        },
        "summary": summary,
        "schedule": schedule,
        "tournaments": history,
        "coverage": {
            "wtaTournamentsChecked": len(wta_tournaments),
            "lookaheadDays": LOOKAHEAD_DAYS,
            "matchCount": len(matches),
        },
        "warnings": warnings,
        "notes": [
            "ITF entries are acceptance-list spots. Priority 1 is her first-choice tournament that week. A direct acceptance can still change if players withdraw before the freeze.",
            "WTA entry lists are published tournament by tournament. This check covers WTA and WTA 125 events through the next few months, and only lists an event once her name is on it.",
            "Completed WTA match results usually leave out the current week until the tournament is finished. This week's scoreline is taken from the live tournament feed.",
        ],
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = build()
    playing = [item["name"] for item in payload["schedule"] if item["phase"] != "withdrawn"]
    print(f"Updated {DATA_PATH}")
    print(f"Rank {payload['player']['ranking']} · {payload['summary']['careerRecord']['played']} tournaments in the archive")
    print("Schedule:", ", ".join(playing) or "no active entries")
    if payload["warnings"]:
        print("Warnings:")
        for warning in payload["warnings"]:
            print(" -", warning)


if __name__ == "__main__":
    main()
