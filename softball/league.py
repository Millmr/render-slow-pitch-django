"""Keep a season's games in step with its league schedule page.

The league is the source of truth for *when and where* games are and who
they're against; the app is the source of truth for everything the team adds
(roll call, lineups, the book, and any score a manager typed in). So a sync:

* creates games the team is in that the app doesn't have yet;
* updates the opponent, start time and field on games it already knows;
* adopts a game a manager added by hand when it starts at the same time;
* fills in final scores the league has posted, but never overwrites one
  entered in the app (a mismatch is reported instead);
* cancels league games that vanished from the page (a rain-out), unless
  they have scores or plays recorded. Cancelling can be undone in the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from . import teamsideline
from .models import Game, Season
from .teamsideline import LeagueGame, LeaguePage, same_team


@dataclass
class SyncSummary:
    created: int = 0
    updated: int = 0
    scored: int = 0
    cancelled: int = 0
    unchanged: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "created": self.created,
            "updated": self.updated,
            "scored": self.scored,
            "cancelled": self.cancelled,
            "unchanged": self.unchanged,
            "notes": self.notes,
        }


def load(url: str, fallback_year: int | None = None) -> LeaguePage:
    """Fetch and parse the league page (raises teamsideline.LeaguePageError)."""
    return teamsideline.parse(teamsideline.fetch(url), fallback_year=fallback_year)


def payload(season: Season) -> dict:
    standings = season.league_standings or []
    return {
        "season": season.id,
        "linked": bool(season.league_url),
        "url": season.league_url,
        "team_name": season.league_team_name,
        "league_name": season.league_name,
        "synced_at": season.league_synced_at,
        "standings": [row | {"is_us": same_team(row.get("team", ""), season.league_team_name)} for row in standings],
    }


def _describe(game: Game) -> str:
    when = timezone.localtime(game.scheduled_at)
    return f"{game.opponent_name} on {when:%a %-m/%-d} at {when:%-I:%M %p}"


def _apply_scores(game: Game, ours: int | None, theirs: int | None, summary: SyncSummary) -> bool:
    """Fill in a posted final score. Returns True when the game changed."""
    if ours is None or theirs is None or game.status == Game.Status.CANCELLED:
        return False
    if game.status == Game.Status.COMPLETED:
        if (game.team_score, game.opponent_score) != (ours, theirs):
            summary.notes.append(
                f"The league has {ours}-{theirs} for {_describe(game)}, but the app has "
                f"{game.team_score}-{game.opponent_score}. The app's score was kept."
            )
        return False
    game.team_score, game.opponent_score, game.status = ours, theirs, Game.Status.COMPLETED
    summary.scored += 1
    return True


def sync(season: Season, page: LeaguePage) -> SyncSummary:
    """Bring ``season``'s games in line with ``page``. Safe to run repeatedly."""
    team_name = season.league_team_name
    ours: list[LeagueGame] = [g for g in page.games if g.involves(team_name)]
    summary = SyncSummary()

    with transaction.atomic():
        Season.objects.select_for_update().get(pk=season.pk)  # one sync at a time
        existing = list(season.games.all())
        by_key = {g.league_key: g for g in existing if g.league_key}
        by_time = {g.scheduled_at: g for g in existing if not g.league_key}
        seen: set[str] = set()

        for league_game in ours:
            if league_game.key in seen:
                continue
            seen.add(league_game.key)
            home = same_team(league_game.home, team_name)
            opponent = league_game.away if home else league_game.home
            our_score = league_game.home_score if home else league_game.away_score
            their_score = league_game.away_score if home else league_game.home_score
            starts_at = timezone.make_aware(league_game.starts_at)
            location = league_game.location

            game = by_key.get(league_game.key) or by_time.pop(starts_at, None)
            if game is None:
                game = Game(season=season, league_key=league_game.key, opponent_name=opponent,
                            scheduled_at=starts_at, location=location)
                _apply_scores(game, our_score, their_score, summary)
                game.save()
                summary.created += 1
                continue

            changed = []
            for attr, value in (
                ("league_key", league_game.key),
                ("opponent_name", opponent),
                ("scheduled_at", starts_at),
                ("location", location or game.location),
            ):
                if getattr(game, attr) != value:
                    setattr(game, attr, value)
                    changed.append(attr)
            scored = _apply_scores(game, our_score, their_score, summary)
            if changed or scored:
                game.save()
            if set(changed) - {"league_key"}:
                summary.updated += 1
            elif not scored:
                summary.unchanged += 1

        # League games that are no longer on the page: usually a rain-out.
        for key, game in by_key.items():
            if key in seen or game.status != Game.Status.SCHEDULED:
                continue
            if game.plate_appearances.exists() or game.player_stats.exists():
                summary.notes.append(
                    f"{_describe(game)} is no longer on the league schedule, but it has stats, so it was left alone."
                )
                continue
            game.status = Game.Status.CANCELLED
            game.save(update_fields=["status", "updated_at"])
            summary.cancelled += 1
            summary.notes.append(f"{_describe(game)} is no longer on the league schedule, so it was cancelled.")

        season.league_name = page.name[:200]
        season.league_standings = page.standings
        season.league_synced_at = timezone.now()
        season.save(update_fields=["league_name", "league_standings", "league_synced_at"])
    return summary
