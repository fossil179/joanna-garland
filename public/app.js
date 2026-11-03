const scheduleView = document.querySelector("#schedule-view");
const resultsView = document.querySelector("#results-view");
const tabSchedule = document.querySelector("#tab-schedule");
const tabResults = document.querySelector("#tab-results");
const refreshButton = document.querySelector("#refresh");
const statusLine = document.querySelector("#status");
const updatedLine = document.querySelector("#updated");

const COUNTRIES = {
  TPE: "Chinese Taipei", AUS: "Australia", IND: "India", CHN: "China", USA: "United States",
  CAN: "Canada", JPN: "Japan", GBR: "Great Britain", FRA: "France", ESP: "Spain",
  GER: "Germany", ITA: "Italy", SUI: "Switzerland", CZE: "Czechia", ROU: "Romania",
  POL: "Poland", NED: "Netherlands", BEL: "Belgium", AUT: "Austria", CRO: "Croatia",
  SRB: "Serbia", SVK: "Slovakia", HUN: "Hungary", UKR: "Ukraine", KAZ: "Kazakhstan",
  UZB: "Uzbekistan", THA: "Thailand", KOR: "Korea", HKG: "Hong Kong", SGP: "Singapore",
  NZL: "New Zealand", MEX: "Mexico", BRA: "Brazil", ARG: "Argentina", COL: "Colombia",
  CHI: "Chile", ECU: "Ecuador", PHI: "Philippines", UAE: "United Arab Emirates",
  QAT: "Qatar", TUR: "Türkiye", EGY: "Egypt", TUN: "Tunisia", MAR: "Morocco",
  POR: "Portugal", ANG: "Angola",
};

let data = null;
let resultYear = "season";
let resultLevel = "All";
let resultQuery = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function countryName(code) {
  if (!code) return "";
  if (COUNTRIES[code]) return COUNTRIES[code];
  if (code.length > 3 && code === code.toUpperCase()) {
    return code[0] + code.slice(1).toLowerCase();
  }
  return code;
}

function parseDate(value) {
  if (!value || typeof value !== "string") return null;
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatRange(start, end, fallback) {
  if (fallback && !end) return fallback;
  const s = parseDate(start);
  const e = parseDate(end || start);
  if (!s) return fallback || "";
  if (!e || s.getTime() === e.getTime()) return `${s.getDate()} ${MONTHS[s.getMonth()]} ${s.getFullYear()}`;
  if (s.getMonth() === e.getMonth() && s.getFullYear() === e.getFullYear()) {
    return `${s.getDate()}–${e.getDate()} ${MONTHS[s.getMonth()]} ${s.getFullYear()}`;
  }
  if (s.getFullYear() === e.getFullYear()) {
    return `${s.getDate()} ${MONTHS[s.getMonth()]} – ${e.getDate()} ${MONTHS[e.getMonth()]} ${s.getFullYear()}`;
  }
  return `${s.getDate()} ${MONTHS[s.getMonth()]} ${s.getFullYear()} – ${e.getDate()} ${MONTHS[e.getMonth()]} ${e.getFullYear()}`;
}

function whenParts(event) {
  const start = parseDate(event.start);
  if (!start) return { month: "", days: event.datesLabel || "" };
  const end = parseDate(event.end);
  const month = MONTHS[start.getMonth()];
  const days = end && end.getDate() !== start.getDate() ? `${start.getDate()}–${end.getDate()}` : `${start.getDate()}`;
  return { month, days };
}

function relative(event) {
  const start = parseDate(event.start);
  if (!start || event.phase === "withdrawn") return "";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((start - today) / 86400000);
  if (event.phase === "now") return "This week";
  if (diff === 0) return "Starts today";
  if (diff === 1) return "Starts tomorrow";
  if (diff > 1) return `In ${diff} days`;
  return "";
}

function surfaceClass(surface) {
  const name = (surface || "").toLowerCase();
  if (name.includes("clay")) return "clay";
  if (name.includes("grass")) return "grass";
  return "hard";
}

function record(item) {
  const wins = item.wins ?? 0;
  const losses = item.losses ?? 0;
  const total = wins + losses;
  if (!total) return "—";
  return `${wins}–${losses}`;
}

function percent(item) {
  const wins = item.wins || 0;
  const losses = item.losses || 0;
  const total = wins + losses;
  if (!total) return "";
  return `${Math.round((wins / total) * 100)}%`;
}

function formatDay(value) {
  const parsed = parseDate(value);
  if (!parsed) return "";
  return `${parsed.getDate()} ${MONTHS[parsed.getMonth()]} ${parsed.getFullYear()}`;
}

function previousRankingDay(rankedAt) {
  const parsed = parseDate(rankedAt);
  if (!parsed) return "";
  parsed.setDate(parsed.getDate() - 7);
  return `${parsed.getDate()} ${MONTHS[parsed.getMonth()]} ${parsed.getFullYear()}`;
}

function movementText(movement, rankedAt) {
  const from = previousRankingDay(rankedAt);
  const when = from ? ` from ${from}` : "";
  if (movement === 0 || movement == null) return `unchanged${when}`;
  const places = `${Math.abs(movement)} place${Math.abs(movement) === 1 ? "" : "s"}`;
  if (movement < 0) return `down ${places}${when}`;
  return `up ${places}${when}`;
}

function renderHeader() {
  const player = data.player;
  const movement = player.movement;
  const moveClass = movement < 0 ? "down" : movement > 0 ? "up" : "";
  document.querySelector("#rank-block").innerHTML = `
    <span class="number">${esc(player.ranking)}</span>
    <span class="rank-caption">WTA ranking</span>
    <span class="rank-move ${moveClass}">(${esc(movementText(movement, player.rankedAt))})</span>
    <span class="rank-points">${esc(player.points)} pts</span>
  `;
  const when = new Date(data.updatedAt);
  updatedLine.textContent = `Updated ${when.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
}

function renderSnapshot() {
  const season = data.summary.seasonRecord;
  const career = data.summary.careerRecord;
  document.querySelector("#snapshot").innerHTML = `
    <article class="stat"><span>${esc(data.summary.season)} record</span><strong>${record(season)}</strong><em>${esc(percent(season))} · published matches</em></article>
    <article class="stat"><span>${esc(data.summary.season)} titles</span><strong>${esc(season.titles)}</strong><em>${esc(data.tournaments.filter((item) => item.champion && item.year === data.summary.season).map((item) => item.city || item.name).join(" · ") || "WTA and ITF")}</em></article>
    <article class="stat"><span>Career</span><strong>${record(career)}</strong><em>${esc(career.titles)} titles · ${esc(career.played)} events</em></article>
  `;
}

function matchHref(item, match) {
  const scores = wtaScoresUrl(item);
  if (match.matchId && scores) return `${scores}/${encodeURIComponent(match.matchId)}`;
  if (scores) return scores;
  if (item.level === "ITF") return data.player.links.itfActivity || "";
  return item.url || "";
}

function matchList(matches, item) {
  if (!matches || !matches.length) return "";
  return `<ul class="matches">${matches.map((match) => {
    const href = item ? matchHref(item, match) : "";
    const body = `
      <span class="mark ${match.won ? "win" : "loss"}">${match.won ? "W" : "L"}</span>
      <span><strong>${esc(match.roundLabel)}</strong>${match.date ? ` · ${esc(formatDay(match.date))}` : ""} vs ${esc(match.opponent)}${match.opponentCountry ? ` (${esc(match.opponentCountry)})` : ""}${match.opponentRank ? ` · No. ${esc(match.opponentRank)}` : ""}</span>
      <span class="score">${esc(match.score)}</span>`;
    const videos = videoLinks(match);
    return `<li>${href ? `<a class="match-link" href="${esc(href)}" target="_blank" rel="noreferrer">${body}</a>` : body}${videos}</li>`;
  }).join("")}</ul>`;
}

function videoLinks(match) {
  const videos = match.videos || [];
  if (!videos.length) return "";
  const links = videos.map((video) => {
    const label = video.kind === "full" ? "Full match" : "Highlights";
    const href = `https://www.youtube.com/watch?v=${encodeURIComponent(video.id)}`;
    return `<a href="${esc(href)}" target="_blank" rel="noreferrer" title="${esc(video.title)}">${label}</a>`;
  });
  return `<p class="match-videos">${links.join(" · ")}</p>`;
}

function eventCard(event) {
  const when = whenParts(event);
  const place = [event.city, countryName(event.country)].filter(Boolean).join(", ");
  const court = [event.surface, event.indoor ? "indoor" : event.surface ? "outdoor" : ""].filter(Boolean).join(" · ");
  const bits = [place, court, formatRange(event.start, event.end, event.datesLabel)].filter(Boolean);
  let status = event.entryStatus;
  if (event.seed) status += ` · seed ${event.seed}`;
  if (event.acceptancePosition) status += ` · acceptance list no. ${event.acceptancePosition}`;
  if (event.priority) status += ` · priority ${event.priority}`;
  if (event.wtaRankAtEntry) status += ` · ranked ${event.wtaRankAtEntry} on the list`;
  let extra = "";
  if (event.phase === "now" && event.stillIn && event.nextRound) {
    extra = `<p class="detail">Through to the ${esc(event.nextRound.toLowerCase())}</p>`;
    if (event.nextMatch) {
      const next = event.nextMatch;
      const when = [formatDay(next.date), next.time, next.court, next.opponent ? `vs ${next.opponent}` : ""].filter(Boolean);
      extra += `<p class="detail">Next match: ${esc(when.join(" · "))}</p>`;
    } else {
      extra += `<p class="detail">Next match: the ${esc(event.nextRound.toLowerCase())} is not on the order of play yet.</p>`;
    }
  } else if (event.phase === "now" && event.eliminated) {
    extra = `<p class="detail">Out of the tournament</p>`;
  } else if (event.phase === "withdrawn" && event.note) {
    extra = `<p class="detail">${esc(event.note.replace(/^W\s+/i, "Withdrew "))}</p>`;
  } else if (relative(event)) {
    extra = `<p class="detail">${esc(relative(event))}</p>`;
  }
  return `
    <article class="event ${esc(event.phase)}">
      <div class="when">
        <span class="month">${esc(when.month)}</span>
        <span class="days">${esc(when.days)}</span>
      </div>
      <div class="event-body">
        <div class="kicker">
          ${event.phase === "now" ? '<span class="pill now">Playing</span>' : ""}
          ${event.phase === "withdrawn" ? '<span class="pill">Withdrawn</span>' : ""}
          <span class="pill ${event.tour === "WTA" ? "wta" : "itf"}">${esc(event.tour)}</span>
          <span class="pill">${esc(event.level)}</span>
          <span class="pill ${surfaceClass(event.surface)}">${esc(event.surface || "Court")}</span>
        </div>
        <h3>${esc(event.name)}</h3>
        <p class="meta">${esc(bits.join(" · "))}</p>
        <p class="detail">${esc(status)}</p>
        ${extra}
        ${matchList(event.liveMatches, event)}
        <p class="links"><a href="${esc(event.url)}" target="_blank" rel="noreferrer">${event.tour === "ITF" ? "ITF page" : "WTA page"}</a></p>
      </div>
    </article>`;
}

function renderSchedule() {
  const active = data.schedule.filter((event) => event.phase !== "withdrawn");
  const withdrawn = data.schedule.filter((event) => event.phase === "withdrawn");
  const now = active.filter((event) => event.phase === "now");
  const upcoming = active.filter((event) => event.phase === "upcoming");
  const section = (title, events) => events.length
    ? `<section class="band"><h2>${esc(title)}</h2><div class="cards">${events.map(eventCard).join("")}</div></section>`
    : "";
  const empty = !active.length
    ? `<div class="empty"><p>No upcoming entries were published in the window that was checked. WTA lists often appear close to the event. Use Update after entry deadlines.</p></div>`
    : "";
  scheduleView.innerHTML = `
    ${section("This week", now)}
    ${section("Entered", upcoming)}
    ${empty}
    ${section("Withdrawn", withdrawn)}
  `;
}

function years() {
  const values = [...new Set(data.tournaments.map((item) => item.year).filter(Boolean))].sort((a, b) => b - a);
  return values;
}

function filteredTournaments() {
  return data.tournaments.filter((item) => {
    if (resultYear === "season" && item.year !== data.summary.season) return false;
    if (resultYear !== "season" && resultYear !== "all" && item.year !== Number(resultYear)) return false;
    if (resultLevel !== "All" && item.level !== resultLevel) return false;
    if (resultQuery) {
      const haystack = `${item.name} ${item.city} ${item.matches.map((match) => match.opponent).join(" ")}`.toLowerCase();
      if (!haystack.includes(resultQuery.toLowerCase())) return false;
    }
    return true;
  });
}

function resultCards(filtered) {
  if (!filtered.length) return `<div class="empty">No tournaments match these filters.</div>`;
  return filtered.map((item) => `
    <details class="result" ${item.inProgress ? "open" : ""}>
      <summary>
        <div class="kicker">
          <span class="pill ${item.champion ? "champion" : item.inProgress ? "now" : ""}">${esc(item.outcome)}</span>
          <span class="pill">${esc(item.level)}</span>
          <span class="pill ${surfaceClass(item.surface)}">${esc(item.surface)}${item.indoor ? " · indoor" : ""}</span>
          <span class="pill ${item.wins >= item.losses ? "win" : "loss"}">${record(item)}</span>
        </div>
        <h3>${esc(item.name)}</h3>
        <p class="meta">${esc([item.city, countryName(item.country), formatRange(item.start, item.end)].filter(Boolean).join(" · "))}</p>
        <p class="expand-hint">${item.matches.length} match${item.matches.length === 1 ? "" : "es"}</p>
      </summary>
      ${matchList(item.matches, item)}
      <p class="links">${resultLinks(item)}</p>
    </details>`).join("");
}

function wtaScoresUrl(item) {
  const url = (item.url || "").replace(/\/$/, "");
  if (!url.includes("wtatennis.com/tournaments/")) return url;
  if (url.endsWith("/scores")) return url;
  const year = item.year ? String(item.year) : "";
  const withYear = year && !url.includes(`/${year}`) ? `${url}/${year}` : url;
  return `${withYear}/scores`;
}

function resultLinks(item) {
  const links = [];
  const scores = wtaScoresUrl(item);
  if (scores) links.push(`<a href="${esc(scores)}" target="_blank" rel="noreferrer">WTA scores</a>`);
  if (item.level === "ITF" && data.player.links.itfActivity) {
    links.push(`<a href="${esc(data.player.links.itfActivity)}" target="_blank" rel="noreferrer">ITF results</a>`);
  }
  return links.join(" · ");
}

function paintResults() {
  const filtered = filteredTournaments();
  const count = resultsView.querySelector("#result-count");
  const list = resultsView.querySelector("#result-list");
  if (!count || !list) return;
  count.textContent = `${filtered.length} tournament${filtered.length === 1 ? "" : "s"}`;
  list.innerHTML = resultCards(filtered);
  resultsView.querySelectorAll("[data-year]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.year === resultYear));
  });
  resultsView.querySelectorAll("[data-level]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.level === resultLevel));
  });
}

function renderResults() {
  const yearValues = years();
  const levels = ["All", "Grand Slam", "WTA 1000", "WTA 500", "WTA 250", "WTA 125", "ITF"]
    .filter((level, index, all) => all.indexOf(level) === index);
  resultsView.innerHTML = `
    <div class="year" role="group" aria-label="Year">
      <button type="button" data-year="season">${esc(data.summary.season)}</button>
      <button type="button" data-year="all">All years</button>
      ${yearValues.filter((year) => year !== data.summary.season).map((year) =>
        `<button type="button" data-year="${year}">${year}</button>`
      ).join("")}
    </div>
    <div class="filters">
      <input class="search" id="result-search" type="search" placeholder="Tournament or opponent" value="${esc(resultQuery)}" aria-label="Search results" />
      ${levels.map((level) => `<button type="button" data-level="${esc(level)}">${esc(level)}</button>`).join("")}
    </div>
    <p class="meta" id="result-count"></p>
    <p class="links official-results">
      <a href="${esc(data.player.links.wtaMatches || data.player.links.wta)}" target="_blank" rel="noreferrer">Her results on the WTA</a>
      ·
      <a href="${esc(data.player.links.itfActivity || data.player.links.itf)}" target="_blank" rel="noreferrer">Her results on the ITF</a>
    </p>
    <div class="cards" id="result-list"></div>
  `;
  paintResults();
}

function renderNotes() {
  const player = data.player;
  const notes = (data.notes || []).map((note) => `<li>${esc(note)}</li>`).join("");
  const warnings = (data.warnings || []).map((note) => `<li>${esc(note)}</li>`).join("");
  document.querySelector("#notes").innerHTML = `
    <p>Checked ${esc(data.coverage.wtaTournamentsChecked)} WTA events over the next ${esc(data.coverage.lookaheadDays)} days, plus her ITF acceptance lists. ${esc(data.coverage.matchCount)} singles matches are in the archive. Rankings dated ${esc(player.rankedAt || "the latest WTA list")}.</p>
    <ul>${notes}${warnings}</ul>
    <p>
      <a href="${esc(player.links.wta)}" target="_blank" rel="noreferrer">WTA profile</a> ·
      <a href="${esc(player.links.itf)}" target="_blank" rel="noreferrer">ITF profile</a> ·
      <a href="${esc(player.links.itfEntries)}" target="_blank" rel="noreferrer">ITF acceptance lists</a>
    </p>
  `;
}

function render() {
  renderHeader();
  renderSnapshot();
  renderSchedule();
  renderResults();
  renderNotes();
}

function show(view) {
  const schedule = view === "schedule";
  scheduleView.hidden = !schedule;
  resultsView.hidden = schedule;
  tabSchedule.setAttribute("aria-pressed", String(schedule));
  tabResults.setAttribute("aria-pressed", String(!schedule));
}

tabSchedule.addEventListener("click", () => show("schedule"));
tabResults.addEventListener("click", () => show("results"));

resultsView.addEventListener("click", (event) => {
  const year = event.target.closest("[data-year]");
  const level = event.target.closest("[data-level]");
  if (year) {
    resultYear = year.dataset.year;
    paintResults();
  }
  if (level) {
    resultLevel = level.dataset.level;
    paintResults();
  }
});

resultsView.addEventListener("input", (event) => {
  if (event.target.id === "result-search") {
    resultQuery = event.target.value;
    paintResults();
  }
});

const onThisComputer = location.hostname === "127.0.0.1" || location.hostname === "localhost";
refreshButton.hidden = !onThisComputer;

async function load() {
  const response = await fetch("data/joanna.json", { cache: "no-cache" });
  if (!response.ok) throw new Error("The saved schedule could not be loaded.");
  data = await response.json();
  render();
  statusLine.textContent = "";
}

refreshButton.addEventListener("click", async () => {
  refreshButton.disabled = true;
  refreshButton.classList.add("spinning");
  statusLine.textContent = "Checking WTA entry lists and ITF acceptance lists. This takes about a minute.";
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Update failed");
    data = payload;
    render();
    statusLine.textContent = "Schedule updated.";
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    refreshButton.disabled = false;
    refreshButton.classList.remove("spinning");
  }
});

const alertForm = document.querySelector("#alert-form");
const alertMessage = document.querySelector("#alert-message");
const alertPending = document.querySelector("#alert-pending");
const alertConfig = window.GARLAND_ALERTS || {};
if (!alertConfig.subscribeUrl) alertPending.hidden = false;

alertForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = alertForm.email.value.trim();
  const starts = alertForm.starts.checked;
  const results = alertForm.results.checked;
  alertMessage.textContent = "";
  if (!starts && !results) {
    alertMessage.textContent = "Choose at least one kind of email.";
    return;
  }
  if (!alertConfig.subscribeUrl) {
    alertMessage.textContent = "Confirmation emails are not connected yet.";
    return;
  }
  const button = alertForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await fetch(alertConfig.subscribeUrl.replace(/\/$/, "") + "/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        starts,
        results,
        company: alertForm.company.value,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "The confirmation email could not be sent.");
    alertForm.reset();
    alertMessage.textContent = "Check your email and open the confirmation link. It lasts two days.";
  } catch (error) {
    alertMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

load().catch((error) => {
  scheduleView.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`;
  updatedLine.textContent = "No saved schedule";
});
