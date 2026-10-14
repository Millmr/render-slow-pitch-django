"""Read a league schedule from a TeamSideline "print view" page.

TeamSideline has no API, so this downloads the public standings/schedule
page (``.../common/StandingsResultsPrintView.aspx?d=...``) and reads the
tables. The page is an ASP.NET grid whose cells carry stable ids such as
``ctl00_OrgContentUnit_ScheduleGrid_ctl00_ctl05_HomeLabel``; those ids are
what the parser keys on, rather than the page's layout.

This module only knows about the page. It has no Django imports, so it can
be tested on its own; ``softball/league.py`` turns the result into games.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time
from html.parser import HTMLParser
from urllib.parse import urlsplit

ALLOWED_HOSTS = ("teamsideline.com",)
FETCH_TIMEOUT_SECONDS = 15
MAX_PAGE_BYTES = 3_000_000
USER_AGENT = "SlowPitchOrganizer/1.0 (team schedule sync)"

# Grid id -> the section its games belong to.
GRIDS = {
    "ScheduleGrid": "regular",
    "PlayoffGrid": "playoff",
    "Playoff2Grid": "playoff2",
}
_PREFIX = "ctl00_OrgContentUnit_"
_LABEL_ID = re.compile(rf"^{_PREFIX}(?P<grid>{'|'.join(GRIDS)})_ctl00_(?P<row>ctl\d+)_(?P<field>\w+?)Label$")
_GRID_ID = re.compile(rf"^{_PREFIX}(?P<grid>{'|'.join(GRIDS)})$")
_STANDINGS_ROW_ID = re.compile(rf"^{_PREFIX}standingsGrid_ctl00__\d+$")
_DATE = re.compile(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{4}))?")
_TIME = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp][Mm])")
_YEAR = re.compile(r"\b(20\d{2})\b")
_VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "col", "area", "base", "wbr", "source"}
# Standings columns, in page order. The last column (the coach's name and
# phone number) is deliberately not kept.
STANDINGS_COLUMNS = ("place", "team", "wins", "losses", "games_back", "games_played", "pct", "streak")


class LeaguePageError(Exception):
    """The link isn't usable, or the page didn't look like a TeamSideline schedule."""


class LeagueFetchError(LeaguePageError):
    """TeamSideline couldn't be reached (or answered with an error)."""


@dataclass
class LeagueGame:
    section: str  # "regular", "playoff" or "playoff2"
    key: str  # stable id for matching on later syncs
    starts_at: datetime  # naive, in the league's local time
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    location: str
    label: str = ""  # playoff game number, e.g. "G3"
    group: str = ""  # "Week 1", "Playoff Round 2"

    def involves(self, team_name: str) -> bool:
        return same_team(self.home, team_name) or same_team(self.away, team_name)


@dataclass
class LeaguePage:
    name: str
    games: list[LeagueGame] = field(default_factory=list)
    standings: list[dict] = field(default_factory=list)

    @property
    def teams(self) -> list[str]:
        """Team names in standings order (falls back to the schedule)."""
        if self.standings:
            return [row["team"] for row in self.standings]
        seen: dict[str, None] = {}
        for game in self.games:
            if game.section == "regular":
                for name in (game.home, game.away):
                    seen.setdefault(name, None)
        return list(seen)

    def find_team(self, team_name: str) -> str | None:
        return next((name for name in self.teams if same_team(name, team_name)), None)


def normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def same_team(a: str, b: str) -> bool:
    return bool(a) and normalize(a) == normalize(b)


def clean_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


# --------------------------------------------------------------------------- fetching


def _allowed_host(host: str | None) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def validate_url(url: str) -> str:
    """Only TeamSideline pages over HTTPS, so the server can't be pointed elsewhere."""
    url = (url or "").strip()
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme != "https" or not _allowed_host(parts.hostname) or parts.username or parts.password or port not in (None, 443):
        raise LeaguePageError("Use the https://teamsideline.com link to your league's standings and schedule page.")
    return url


class _SameSiteRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to other TeamSideline https URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_url(newurl)
        except LeaguePageError:
            raise LeagueFetchError("The league link redirected somewhere other than TeamSideline.") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameSiteRedirects)


def fetch(url: str) -> str:
    url = validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with _opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_PAGE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except LeaguePageError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise LeagueFetchError("Couldn't reach TeamSideline. Try again in a minute.") from exc
    if len(body) > MAX_PAGE_BYTES:
        raise LeagueFetchError("The league page was unexpectedly large.")
    return body.decode(charset, errors="replace")


# --------------------------------------------------------------------------- parsing


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.grid: str | None = None
        # (grid, row id) -> {field: text}, in page order
        self.rows: dict[tuple[str, str], dict[str, str]] = {}
        self.row_groups: dict[tuple[str, str], str] = {}
        self.group = {grid: "" for grid in GRIDS}
        self.standings: list[list[str]] = []
        self.title = ""
        # Active captures: (depth to close at, kind, key)
        self._captures: list[tuple[int, str, object]] = []
        self._buffers: dict[int, list[str]] = {}
        self._in_standings_row_depth: int | None = None
        self._title_done = False

    def _start_capture(self, kind, key):
        depth = len(self.stack)
        self._captures.append((depth, kind, key))
        self._buffers[id(self._captures[-1])] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id") or ""
        classes = (attrs.get("class") or "").split()
        if tag in _VOID_TAGS:
            if tag == "br":
                self.handle_data(" ")
            return
        self.stack.append(tag)

        grid = _GRID_ID.match(element_id)
        if grid:
            self.grid = grid.group("grid")

        label = _LABEL_ID.match(element_id)
        if label:
            self._start_capture("label", (label.group("grid"), label.group("row"), label.group("field")))
        elif tag == "tr" and "rgGroupHeader" in classes and self.grid:
            self._start_capture("group", self.grid)
        elif tag == "tr" and _STANDINGS_ROW_ID.match(element_id):
            self.standings.append([])
            self._in_standings_row_depth = len(self.stack)
        elif tag == "td" and self._in_standings_row_depth is not None:
            self.standings[-1].append("")
            self._start_capture("cell", None)
        elif "pageContentTitle" in classes and not self._title_done:
            self._start_capture("title", None)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self._close_captures()
            if open_tag == tag:
                break

    def _close_captures(self):
        depth = len(self.stack)
        while self._captures and self._captures[-1][0] > depth:
            capture = self._captures.pop()
            text = clean_text("".join(self._buffers.pop(id(capture))))
            _, kind, key = capture
            if kind == "label":
                grid, row, field_name = key
                self.rows.setdefault((grid, row), {})[field_name] = text
                self.row_groups.setdefault((grid, row), self.group[grid])
            elif kind == "group":
                self.group[key] = text
            elif kind == "cell" and self.standings:
                self.standings[-1][-1] = text
            elif kind == "title":
                self.title = text
                self._title_done = True
        if self._in_standings_row_depth is not None and depth < self._in_standings_row_depth:
            self._in_standings_row_depth = None

    def handle_data(self, data):
        for capture in self._captures:
            self._buffers[id(capture)].append(data)


def _score(text: str) -> int | None:
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def _parse_date(text: str, default_year: int) -> date | None:
    match = _DATE.search(text or "")
    if not match:
        return None
    year = int(match.group("year") or default_year)
    try:
        return date(year, int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None


def _parse_time(text: str) -> time | None:
    match = _TIME.search(text or "")
    if not match:
        return None
    hour = int(match.group("hour")) % 12 + (12 if match.group("ampm").lower() == "pm" else 0)
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse(html: str, fallback_year: int | None = None) -> LeaguePage:
    """Read the league name, every dated game, and the standings.

    Dates on the page have no year ("Mon 8/24"); the year comes from the league
    name ("Fall Softball League 2026"), else ``fallback_year``. A schedule that
    crosses New Year is rolled forward when a date jumps back by months.
    """
    parser = _Parser()
    parser.feed(html)
    parser.close()

    if not parser.rows and not parser.standings:
        raise LeaguePageError("That page doesn't look like a TeamSideline standings and schedule page.")

    name = re.sub(r"\s*\|\s*All Teams\s*$", "", parser.title).strip()
    year_match = _YEAR.search(name)
    year = int(year_match.group(1)) if year_match else (fallback_year or date.today().year)

    games: list[LeagueGame] = []
    last_date: dict[str, date] = {}
    previous: date | None = None
    for (grid, _row), cells in parser.rows.items():
        section = GRIDS[grid]
        day = _parse_date(cells.get("Date", ""), year)
        if day and previous and (previous - day).days > 120:
            day = day.replace(year=day.year + 1)  # e.g. a winter league running Dec -> Jan
        if day is None and cells.get("Date", "") == "" and section != "regular":
            day = last_date.get(grid)  # playoff rows only show the date on the first game
        starts = _parse_time(cells.get("Time", ""))
        home, away = cells.get("Home", ""), cells.get("Away", "")
        # Byes, rain-outs and holidays have no date/time or no opponent.
        if day is None or starts is None or not home or not away:
            continue
        last_date[grid] = day
        previous = day
        starts_at = datetime.combine(day, starts)
        label = cells.get("Game", "")
        key = f"{section}:{label}" if section != "regular" and label else f"{section}:{starts_at:%Y-%m-%dT%H:%M}"
        games.append(
            LeagueGame(
                section=section,
                key=key,
                starts_at=starts_at,
                home=home,
                away=away,
                home_score=_score(cells.get("HomeScore", "")),
                away_score=_score(cells.get("AwayScore", "")),
                location=cells.get("Schedule", ""),  # the field code sits in a "ScheduleLabel"
                label=label,
                group=parser.row_groups.get((grid, _row), ""),
            )
        )

    standings = []
    for cells in parser.standings:
        if len(cells) < len(STANDINGS_COLUMNS):
            continue
        row = dict(zip(STANDINGS_COLUMNS, cells))  # zip drops the coach column
        if row["team"]:
            standings.append(row)

    return LeaguePage(name=name, games=games, standings=standings)
