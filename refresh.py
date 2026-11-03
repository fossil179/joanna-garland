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
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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
WTA_MATCHES = "https://www.wtatennis.com/players/326512/joanna-garland/matches"
WTA_RANKINGS = "https://www.wtatennis.com/rankings/singles"
ITF_PROFILE = "https://www.world.tennis/en/players/joanna-garland/800446517/tpe/wt/s/overview/"
ITF_ACTIVITY = "https://www.itftennis.com/en/players/joanna-garland/800446517/tpe/wt/s/activity/"
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


def wta_tournament_url(group_id: int, title: str, year: int | str | None = None, page: str = "") -> str:
    slug = slugify(title) or "tournament"
    url = f"https://www.wtatennis.com/tournaments/{int(str(group_id).strip())}/{slug}"
    if year:
        url = f"{url}/{year}"
    if page:
        url = f"{url}/{page}"
    return url


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
                    "date": "",
                    "matchId": "",
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
                "url": wta_tournament_url(
                    number,
                    tournament.get("title") or sample.get("TournamentName") or "",
                    year,
                    "scores",
                ),
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


def name_parts(name: str) -> list[str]:
    return [part for part in (name or "").replace(",", " ").split() if part]


def last_name(name: str) -> str:
    parts = name_parts(name)
    return parts[-1].casefold() if parts else ""


def name_keys(name: str) -> list[str]:
    parts = [part.casefold() for part in name_parts(name)]
    keys = []
    for length in range(1, min(3, len(parts)) + 1):
        keys.append(" ".join(parts[-length:]))
    return keys


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def upcoming_match(group_id: int, year: int, finished_names: set[str]) -> dict | None:
    try:
        payload = http_json(f"https://api.wtatennis.com/tennis/tournaments/{group_id}/{year}/oop")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    raw = payload.get("orderOfPlay") or []
    if not raw or not isinstance(raw[0], str):
        return None
    try:
        schedule = json.loads(raw[0])
    except json.JSONDecodeError:
        return None
    try:
        return upcoming_from_schedule(schedule, finished_names)
    except (AttributeError, TypeError, KeyError):
        return None


def upcoming_from_schedule(schedule: dict, finished_names: set[str]) -> dict | None:
    days = as_list((((schedule.get("OOP") or {}).get("Schedule") or {}).get("Day")))
    found = []
    for day in days:
        if not isinstance(day, dict):
            continue
        for court in as_list(day.get("Court")):
            if not isinstance(court, dict):
                continue
            match_node = court.get("Matches")
            matches = as_list(match_node.get("Match") if isinstance(match_node, dict) else match_node)
            for match in matches:
                if not isinstance(match, dict):
                    continue
                sides = []
                for side in as_list(match.get("Players")):
                    if not isinstance(side, dict):
                        continue
                    people = [player for player in as_list(side.get("Player")) if isinstance(player, dict)]
                    if people:
                        sides.append(people)
                ids = {str(player.get("id") or "") for people in sides for player in people}
                possible = match.get("PossiblePlayersIdsInMatch")
                possible_ids = as_list(possible.get("playerid") if isinstance(possible, dict) else possible)
                ids.update(str(item) for item in possible_ids)
                if WTA_ID not in ids:
                    continue
                opponent = ""
                for people in sides:
                    if any(str(player.get("id") or "") == WTA_ID for player in people):
                        continue
                    opponent = " / ".join(
                        " ".join(part for part in (player.get("FirstName") or "", player.get("SurName") or "") if part)
                        for player in people
                    )
                if not opponent:
                    line = match.get("MatchupDisplayLine") or ""
                    opponent = line
                if last_name(opponent) in finished_names:
                    continue
                if (match.get("Status") or "").lower() == "completed":
                    continue
                found.append(
                    {
                        "date": day.get("ISODate") or "",
                        "time": match.get("NotBefore") or "",
                        "court": court.get("CourtName") or "",
                        "opponent": opponent,
                    }
                )
    found.sort(key=lambda item: item.get("date") or "9999")
    return found[0] if found else None


def saved_match_dates() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        saved = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    found = {}
    for tournament in saved.get("tournaments") or []:
        for match in tournament.get("matches") or []:
            if match.get("date") or match.get("matchId"):
                key = (
                    tournament.get("id"),
                    match.get("round"),
                    (match.get("opponent") or "").casefold(),
                    bool(match.get("qualifying")),
                )
                found[key] = {"date": match.get("date") or "", "matchId": match.get("matchId") or ""}
    return found


def dates_by_opponent(year: str, number: str) -> dict:
    year = str(year).strip()
    number = str(number).strip()
    if not year.isdigit() or not number.isdigit():
        return {}
    try:
        payload = http_json(
            f"https://api.wtatennis.com/tennis/tournaments/{number}/{year}/matches",
            timeout=20,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return {}
    found = {}
    for match in payload.get("matches") or []:
        ids = {str(match.get("PlayerIDA") or ""), str(match.get("PlayerIDB") or "")}
        if WTA_ID not in ids:
            continue
        if str(match.get("PlayerIDB") or "") == WTA_ID:
            name = match.get("PlayerNameLastA") or ""
        else:
            name = match.get("PlayerNameLastB") or ""
        played = iso_date(match.get("MatchTimeStamp"))
        match_id = match.get("MatchID") or ""
        if name and (played or match_id):
            record = {"date": played or "", "matchId": match_id}
            for key in name_keys(name):
                found.setdefault(key, []).append(record)
    for records in found.values():
        records.sort(key=lambda record: record.get("date") or "")
    return found


VIDEO_REJECT = (
    "simulation", "gameplay", "preview", "interview", "reaction", "practice",
    "sit-down", "sit down", "live score", "breaks silence", "speechless",
    "shocks", "shocked", "unbelievable", "point slam", "hottest", "beauties",
)
VIDEO_GENERIC = {
    "open", "international", "tennis", "presented", "championships", "workday",
    "women", "womens", "the", "wta", "itf", "bnp", "paribas", "lexus", "group",
}
VIDEO_OFFICIAL = {
    "wta", "australian open", "roland-garros", "roland garros", "us open",
    "wimbledon", "tennis channel", "tennis tv",
}
VIDEO_BLOCKED_CHANNELS = {"pointbeaconlive"}
VIDEO_BLOCKED_CHANNEL_IDS = {"UCRXTPqn_tgjwHw8zHletD9g"}
VIDEO_BLOCKED_IDS = {"vLBlJQkXPBE", "kEMjw5Ky1hw"}


def tournament_tokens(tournament: dict) -> list[str]:
    text = f"{tournament.get('name') or ''} {tournament.get('city') or ''}"
    tokens = []
    for word in re.findall(r"[A-Za-z]{4,}", text):
        token = word.casefold()
        if token not in VIDEO_GENERIC:
            tokens.append(token)
    return tokens


def duration_seconds(value: str) -> int:
    parts = (value or "").split(":")
    if not parts or any(not part.isdigit() for part in parts):
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def channel_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def video_blocked(item: dict) -> bool:
    if item.get("id") in VIDEO_BLOCKED_IDS:
        return True
    if (item.get("channelId") or "") in VIDEO_BLOCKED_CHANNEL_IDS:
        return True
    handle = (item.get("channel") or "").casefold().lstrip("@")
    if handle in VIDEO_BLOCKED_CHANNELS:
        return True
    return channel_key(item.get("author") or "") in VIDEO_BLOCKED_CHANNELS


def owner_details(renderer: dict) -> tuple[str, str, str]:
    owner_node = renderer.get("ownerText") or {}
    runs = owner_node.get("runs") or []
    run = runs[0] if runs else {}
    if runs:
        owner = "".join(part.get("text") or "" for part in runs)
    else:
        owner = owner_node.get("simpleText") or ""
    endpoint = (run.get("navigationEndpoint") or {}).get("browseEndpoint") or {}
    handle = (endpoint.get("canonicalBaseUrl") or "").rstrip("/").split("/")[-1].lstrip("@")
    return owner, handle, endpoint.get("browseId") or ""


def youtube_search(query: str) -> list[dict]:
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"},
    )
    with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
        html = response.read().decode("utf-8", "replace")
    match = re.search(r"ytInitialData\s*=\s*(\{.*?\});</script>", html)
    if not match:
        return []
    payload = json.loads(match.group(1))
    found = []
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict) and renderer.get("videoId"):
                video_id = renderer["videoId"]
                if video_id not in seen:
                    seen.add(video_id)
                    title_node = renderer.get("title") or {}
                    if title_node.get("runs"):
                        title = "".join(part.get("text") or "" for part in title_node["runs"])
                    else:
                        title = title_node.get("simpleText") or ""
                    owner, handle, channel_id = owner_details(renderer)
                    length = (renderer.get("lengthText") or {}).get("simpleText") or ""
                    found.append(
                        {
                            "id": video_id,
                            "title": title,
                            "author": owner,
                            "channel": handle,
                            "channelId": channel_id,
                            "seconds": duration_seconds(length),
                        }
                    )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def video_kind(item: dict) -> str:
    title = item["title"].casefold()
    if "highlight" in title:
        return "highlights"
    if "full match" in title:
        return "full"
    if item["seconds"] >= 20 * 60 and " vs " in f" {title} ":
        return "full"
    if "game, set, match" in title or "game set match" in title:
        return "highlights"
    return ""


def video_matches(item: dict, tournament: dict, match: dict) -> bool:
    if video_blocked(item):
        return False
    title = item["title"].casefold()
    if "garland" not in title:
        return False
    if any(phrase in title for phrase in VIDEO_REJECT):
        return False
    if not video_kind(item):
        return False
    last = last_name(match.get("opponent") or "")
    if len(last) < 4 or not re.search(rf"\b{re.escape(last)}\b", title):
        return False
    years = {int(year) for year in re.findall(r"\b20\d{2}\b", item["title"])}
    match_year = int(tournament["year"]) if str(tournament.get("year") or "").isdigit() else None
    if years and match_year not in years:
        return False
    tokens = tournament_tokens(tournament)
    name = (tournament.get("name") or "").casefold()
    if "us open" in name:
        tokens.append("us open")
    if tokens and not any(token in title for token in tokens):
        return False
    if not tokens and not years:
        return False
    return True


def choose_videos(results: list[dict], tournament: dict, match: dict) -> list[dict]:
    ranked = {"highlights": [], "full": []}
    for index, item in enumerate(results):
        if not video_matches(item, tournament, match):
            continue
        kind = video_kind(item)
        official = item["author"].casefold() in VIDEO_OFFICIAL
        score = (2 if official else 0) - index
        ranked[kind].append((score, item))
    chosen = []
    for kind in ("highlights", "full"):
        if not ranked[kind]:
            continue
        ranked[kind].sort(key=lambda pair: pair[0], reverse=True)
        item = ranked[kind][0][1]
        chosen.append(
            {
                "id": item["id"],
                "title": item["title"],
                "kind": kind,
                "channel": item.get("channel") or "",
            }
        )
    return chosen


def saved_videos() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        saved = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    found = {}
    for tournament in saved.get("tournaments") or []:
        for match in tournament.get("matches") or []:
            if "videosChecked" not in match:
                continue
            key = (
                tournament.get("id"),
                match.get("round"),
                (match.get("opponent") or "").casefold(),
                bool(match.get("qualifying")),
            )
            found[key] = {
                "videos": match.get("videos") or [],
                "videosChecked": match.get("videosChecked") or "",
            }
    return found


def attach_videos(tournaments: list[dict], limit: int = 40) -> None:
    known = saved_videos()
    pending = []
    for tournament in tournaments:
        for match in tournament.get("matches") or []:
            key = (
                tournament.get("id"),
                match.get("round"),
                (match.get("opponent") or "").casefold(),
                bool(match.get("qualifying")),
            )
            saved = known.get(key)
            if saved:
                kept = [video for video in saved["videos"] if not video_blocked(video)]
                match["videos"] = kept
                if len(kept) == len(saved["videos"]):
                    match["videosChecked"] = saved["videosChecked"]
            played = parse_date(match.get("date")) or parse_date(tournament.get("start"))
            recent = played is not None and (TODAY - played).days <= 14
            checked = match.get("videosChecked")
            has_video = bool(match.get("videos"))
            if checked and has_video:
                continue
            if checked and not recent:
                continue
            if checked == TODAY.isoformat() and not has_video:
                continue
            pending.append((played or date.min, tournament, match))
    pending.sort(key=lambda item: item[0], reverse=True)
    for _, tournament, match in pending[:limit]:
        opponent = match.get("opponent") or ""
        query = " ".join(
            part for part in ("Joanna Garland", opponent, str(tournament.get("year") or "")) if part
        )
        try:
            results = youtube_search(query)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            continue
        match["videos"] = choose_videos(results, tournament, match)
        match["videosChecked"] = TODAY.isoformat()
        time.sleep(0.3)


def copy_videos_to_schedule(tournaments: list[dict], schedule: list[dict]) -> None:
    found = {}
    for tournament in tournaments:
        for match in tournament.get("matches") or []:
            if match.get("videos"):
                found[(tournament.get("id"), (match.get("opponent") or "").casefold(), match.get("round"))] = match["videos"]
    for event in schedule:
        history_id = str(event.get("id") or "").replace("wta-", "")
        for match in event.get("liveMatches") or []:
            videos = found.get((history_id, (match.get("opponent") or "").casefold(), match.get("round")))
            if videos:
                match["videos"] = videos


def attach_match_dates(tournaments: list[dict]) -> None:
    known = saved_match_dates()
    pending = []
    for tournament in tournaments:
        for match in tournament.get("matches") or []:
            key = (
                tournament.get("id"),
                match.get("round"),
                (match.get("opponent") or "").casefold(),
                bool(match.get("qualifying")),
            )
            saved = known.get(key) or {}
            if isinstance(saved, str):
                saved = {"date": saved, "matchId": ""}
            if saved.get("date") and not match.get("date"):
                match["date"] = saved["date"]
            if saved.get("matchId") and not match.get("matchId"):
                match["matchId"] = saved["matchId"]
        needs_id = tournament.get("level") != "ITF"
        if any(
            not match.get("date") or (needs_id and not match.get("matchId"))
            for match in tournament.get("matches") or []
        ):
            pending.append(tournament)
    if not pending:
        return

    def load(tournament: dict):
        parts = str(tournament["id"]).split("-", 1)
        if len(parts) != 2:
            return tournament["id"], {}
        return tournament["id"], dates_by_opponent(parts[0], parts[1])

    with ThreadPoolExecutor(max_workers=8) as pool:
        looked_up = dict(pool.map(load, pending))
    for tournament in pending:
        used = {}
        for match in tournament.get("matches") or []:
            lookup = looked_up.get(tournament["id"], {})
            keys = name_keys(match.get("opponent") or "")
            chosen = ""
            records = []
            for key in reversed(keys):
                if lookup.get(key):
                    chosen = key
                    records = lookup[key]
                    break
            index = used.get(chosen, 0)
            if not chosen or index >= len(records):
                continue
            record = records[index]
            if record.get("date") and not match.get("date"):
                match["date"] = record["date"]
            if record.get("matchId") and not match.get("matchId"):
                match["matchId"] = record["matchId"]
            used[chosen] = index + 1


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
        next_match = None
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
                        "date": iso_date(match.get("MatchTimeStamp")) or "",
                        "matchId": match.get("MatchID") or "",
                    }
                )
            parsed.sort(key=lambda match: (match["order"], match["stamp"]))
            played = [match for match in parsed if match.get("state") == "F" or match.get("score")]
            live_matches = played
            if played and still_in:
                last = played[-1]
                if last["won"] and last["round"] != "F":
                    ladder = DRAW_LADDER.get(draw_size or 32, DRAW_LADDER[32])
                    if last["round"] in ladder:
                        index = ladder.index(last["round"])
                        if index + 1 < len(ladder):
                            next_round = ROUND_LABEL[ladder[index + 1]]
            if still_in:
                finished = {last_name(match["opponent"]) for match in played}
                next_match = upcoming_match(group_id, year, finished)
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
                "nextMatch": next_match,
                "liveMatches": live_matches,
                "historyId": f"{year}-{group_id}" if f"{year}-{group_id}" in history_ids else None,
                "url": wta_tournament_url(group_id, tournament.get("title") or "", year),
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
                "nextMatch": None,
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
                    "date": match.get("date") or "",
                    "matchId": match.get("matchId") or "",
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


NEWS_LIMIT = 25
BLOCKED_NEWS_HOSTS = (
    "joannagarland.com",
    "google.com",
    "googleusercontent.com",
    "bing.com",
    "gdeltproject.org",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
)


def news_host(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def news_blocked(host: str) -> bool:
    return any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_NEWS_HOSTS)


def news_published(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 8:
        return None
    year, month, day = digits[0:4], digits[4:6], digits[6:8]
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def previous_news() -> list[dict]:
    if not DATA_PATH.exists():
        return []
    try:
        saved = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    articles = saved.get("news")
    return articles if isinstance(articles, list) else []


def parse_news(payload: dict) -> list[dict]:
    seen = set()
    articles = []
    for item in payload.get("articles") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        parsed = urllib.parse.urlsplit(url)
        host = news_host(url)
        if parsed.scheme not in {"http", "https"} or not host or not title or news_blocked(host):
            continue
        key = f"{host}{parsed.path.rstrip('/').lower()}"
        if key in seen:
            continue
        seen.add(key)
        language = str(item.get("language") or "").strip()
        article = {
            "title": title,
            "url": url,
            "source": host,
            "published": news_published(item.get("seendate")),
        }
        if language and language.lower() != "english":
            article["language"] = language
        articles.append(article)
    articles.sort(key=lambda article: article.get("published") or "", reverse=True)
    return articles[:NEWS_LIMIT]


def news_queries() -> list[dict]:
    """Recent window, then three earlier 90-day windows.

    Short windows stay within the public index's ordinary request size.
    Together they cover about a year, which is still recent coverage.
    """
    base = {
        "query": f'"{PLAYER_NAME}"',
        "mode": "ArtList",
        "maxrecords": "75",
        "format": "json",
        "sort": "DateDesc",
    }
    queries = [dict(base)]
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(3):
        window_end = end - timedelta(days=90)
        window_start = window_end - timedelta(days=90)
        queries.append({
            **base,
            "startdatetime": window_start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": window_end.strftime("%Y%m%d%H%M%S"),
        })
        end = window_end
    return queries


def load_news_window(params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"https://api.gdeltproject.org/api/v2/doc/doc?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "JoannaGarlandTracker/1.0 (personal schedule tracker)",
        },
    )
    with urllib.request.urlopen(request, timeout=45, context=ssl.create_default_context()) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict) or "articles" not in payload:
        raise ValueError("news index returned an unexpected response")
    return payload


def fetch_news() -> tuple[list[dict], str | None]:
    """Latest articles whose text mentions her name.

    The public news index covers recent coverage, not every story ever published.
    A failed check keeps the previous list so one outage does not clear the page.
    """
    saved = previous_news()
    collected = []
    loaded = False
    for index, params in enumerate(news_queries()):
        if index:
            time.sleep(6)
        attempts = 2 if index == 0 else 1
        window = None
        for attempt in range(attempts):
            try:
                window = load_news_window(params)
                break
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt + 1 < attempts:
                    time.sleep(6)
                    continue
                window = None
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
                window = None
                break
        if window is None:
            break
        loaded = True
        collected.extend(window.get("articles") or [])
    if loaded:
        return parse_news({"articles": collected}), None
    if saved:
        return saved, "News articles could not be refreshed. The list is from the last successful check."
    return [], "News articles could not be loaded."


def build() -> dict:
    warnings = []
    news, news_warning = fetch_news()
    if news_warning:
        warnings.append(news_warning)
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
    attach_match_dates(history)
    attach_videos(history)
    copy_videos_to_schedule(history, schedule)
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
                "wtaMatches": WTA_MATCHES,
                "wtaRankings": WTA_RANKINGS,
                "itf": ITF_PROFILE,
                "itfActivity": ITF_ACTIVITY,
                "itfEntries": f"https://itf-entries.netlify.app/player/{ITF_ID}",
            },
        },
        "summary": summary,
        "schedule": schedule,
        "news": news,
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
    previous = None
    if DATA_PATH.exists():
        try:
            previous = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
    payload = build()
    import alerts
    alerts.deliver(previous, payload)
    playing = [item["name"] for item in payload["schedule"] if item["phase"] != "withdrawn"]
    print(f"Updated {DATA_PATH}")
    print(f"Rank {payload['player']['ranking']} · {payload['summary']['careerRecord']['played']} tournaments in the archive")
    print(f"News: {len(payload.get('news') or [])} articles")
    print("Schedule:", ", ".join(playing) or "no active entries")
    if payload["warnings"]:
        print("Warnings:")
        for warning in payload["warnings"]:
            print(" -", warning)


if __name__ == "__main__":
    main()
