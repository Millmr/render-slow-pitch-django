"""Batting statistics.

Every number the API shows — leaderboards, player profiles, a single game's
box score — goes through ``with_rates`` so the formulas only live in one place.
Season numbers come from ORM aggregation over ``PlayerGameStats`` for
*completed* games only; nothing is precomputed or stored twice.

On-base percentage uses a simplified formula, (H + BB) / (AB + BB), because
the app doesn't record hit-by-pitches or sacrifice flies.
"""

from django.db.models import Count, Sum

from .models import Game, PlayerGameStats

COUNTING_FIELDS = ("at_bats", "hits", "singles", "doubles", "triples", "home_runs", "walks")
RATE_FIELDS = ("batting_average", "on_base_percentage", "slugging_percentage")

# Whitelist of leaderboard sort keys. Query parameters never reach the ORM.
LEADERBOARD_SORTS = (
    "batting_average",
    "on_base_percentage",
    "slugging_percentage",
    "hits",
    "home_runs",
    "doubles",
    "triples",
    "walks",
    "at_bats",
    "games_played",
)


def rate(numerator: int, denominator: int):
    """A rate stat rounded to 3 places, or None when it's undefined (0 AB)."""
    if not denominator:
        return None
    return round(numerator / denominator, 3)


def format_rate(value) -> str:
    """Baseball-style display: .333, 1.000, or an em dash with no at-bats."""
    if value is None:
        return "—"
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def with_rates(totals: dict) -> dict:
    """Add derived stats to a dict containing the counting fields."""
    line = dict(totals)
    at_bats, hits, walks = line["at_bats"], line["hits"], line["walks"]
    total_bases = line["singles"] + 2 * line["doubles"] + 3 * line["triples"] + 4 * line["home_runs"]
    line["total_bases"] = total_bases
    line["batting_average"] = rate(hits, at_bats)
    line["on_base_percentage"] = rate(hits + walks, at_bats + walks)
    line["slugging_percentage"] = rate(total_bases, at_bats)
    for field in RATE_FIELDS:
        line[f"{field}_display"] = format_rate(line[field])
    return line


def counted_stats(season):
    """Stat rows that count toward ``season``: completed games only."""
    return PlayerGameStats.objects.filter(game__season=season, game__status=Game.Status.COMPLETED)


def season_lines(season, memberships=None) -> list[dict]:
    """Season totals per player, aggregated in the database."""
    queryset = counted_stats(season)
    if memberships is not None:
        queryset = queryset.filter(membership__in=memberships)
    rows = (
        queryset.values("membership_id", "membership__display_name", "membership__jersey_number")
        .annotate(
            games_played=Count("game", distinct=True),
            # Prefixed because an annotation can't reuse a model field's name.
            **{f"total_{field}": Sum(field) for field in COUNTING_FIELDS},
        )
        .order_by()
    )
    lines = []
    for row in rows:
        totals = {
            "membership_id": row["membership_id"],
            "display_name": row["membership__display_name"],
            "jersey_number": row["membership__jersey_number"],
            "games_played": row["games_played"],
        }
        totals.update({field: row[f"total_{field}"] or 0 for field in COUNTING_FIELDS})
        lines.append(with_rates(totals))
    return lines


def empty_line(membership) -> dict:
    return with_rates(
        {
            "membership_id": membership.id,
            "display_name": membership.display_name,
            "jersey_number": membership.jersey_number,
            "games_played": 0,
            **{field: 0 for field in COUNTING_FIELDS},
        }
    )


def player_season_line(membership, season) -> dict:
    """Same aggregation the leaderboard uses, limited to one player."""
    lines = season_lines(season, memberships=[membership])
    return lines[0] if lines else empty_line(membership)


def game_line(stat: PlayerGameStats) -> dict:
    """One player's line for one game."""
    return with_rates(
        {
            "membership_id": stat.membership_id,
            "display_name": stat.membership.display_name,
            "jersey_number": stat.membership.jersey_number,
            **{field: getattr(stat, field) for field in COUNTING_FIELDS},
        }
    )


def game_log(membership, season) -> list[dict]:
    stats = (
        PlayerGameStats.objects.filter(membership=membership, game__season=season)
        .select_related("game", "membership")
        .order_by("game__scheduled_at", "game_id")
    )
    log = []
    for stat in stats:
        entry = {
            "game_id": stat.game_id,
            "opponent_name": stat.game.opponent_name,
            "scheduled_at": stat.game.scheduled_at,
            "game_status": stat.game.status,
            "counts_toward_season": stat.game.status == Game.Status.COMPLETED,
        }
        entry.update(game_line(stat))
        log.append(entry)
    return log


def leaderboard(season, sort: str = "batting_average") -> list[dict]:
    if sort not in LEADERBOARD_SORTS:
        raise ValueError(f"Unsupported sort: {sort}")
    lines = season_lines(season)
    # Highest first; players with an undefined rate (no at-bats) go last.
    lines.sort(key=lambda line: (line[sort] is None, -(line[sort] or 0), line["display_name"].lower()))
    for rank, line in enumerate(lines, start=1):
        line["rank"] = rank
    return lines
