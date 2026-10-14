"""The scorebook: game state from plate appearances, and stats built from them.

Innings and outs are worked out from the recorded plays, the next batter
comes from the saved lineup, and every change to the book rebuilds that
game's PlayerGameStats so the leaderboard always matches the book.
"""

from collections import defaultdict

from django.db import transaction

from .models import Game, LineupSpot, PlateAppearance, PlayerGameStats

Result = PlateAppearance.Result

# Plain strings (not enum members) so lookups match values read from the database.
OUT_RESULTS = {Result.OUT.value, Result.SAC_FLY.value}
# Walks and sacrifice flies are not at-bats.
AT_BAT_RESULTS = {r.value for r in (Result.SINGLE, Result.DOUBLE, Result.TRIPLE, Result.HOME_RUN, Result.OUT)}
HIT_FIELDS = {
    Result.SINGLE.value: "singles",
    Result.DOUBLE.value: "doubles",
    Result.TRIPLE.value: "triples",
    Result.HOME_RUN.value: "home_runs",
}
OUTS_PER_INNING = 3


def plate_appearances(game):
    return list(game.plate_appearances.select_related("membership").order_by("sequence"))


def lineup(game):
    """Lineup spots in batting order (empty if no lineup has been saved)."""
    return list(
        LineupSpot.objects.filter(lineup__game=game).select_related("membership").order_by("batting_order")
    )


def state(pas, lineup_ids):
    """Current inning, outs, and who's up / on deck."""
    if pas:
        inning = pas[-1].inning
        outs = sum(1 for pa in pas if pa.inning == inning and pa.result in OUT_RESULTS)
        if outs >= OUTS_PER_INNING:
            inning, outs = inning + 1, 0
    else:
        inning, outs = 1, 0

    next_batter = on_deck = None
    if lineup_ids:
        index = 0
        # Continue after the last batter who's in the lineup (skips pinch hitters).
        for pa in reversed(pas):
            if pa.membership_id in lineup_ids:
                index = (lineup_ids.index(pa.membership_id) + 1) % len(lineup_ids)
                break
        next_batter = lineup_ids[index]
        if len(lineup_ids) > 1:
            on_deck = lineup_ids[(index + 1) % len(lineup_ids)]
    return {"inning": inning, "outs": outs, "next_batter_id": next_batter, "on_deck_id": on_deck}


@transaction.atomic
def rebuild_stats(game):
    """Replace the game's stat lines with totals counted from the book."""
    PlayerGameStats.objects.filter(game=game).delete()
    totals = defaultdict(lambda: defaultdict(int))
    for pa in game.plate_appearances.all():
        line = totals[pa.membership_id]
        if pa.result in AT_BAT_RESULTS:
            line["at_bats"] += 1
        if pa.result in HIT_FIELDS:
            line[HIT_FIELDS[pa.result]] += 1
            line["hits"] += 1
        elif pa.result == Result.WALK.value:
            line["walks"] += 1
        elif pa.result == Result.SAC_FLY.value:
            line["sac_flies"] += 1
    PlayerGameStats.objects.bulk_create(
        [PlayerGameStats(game=game, membership_id=membership_id, **counts) for membership_id, counts in totals.items()]
    )


def _member(membership, batting_order=None):
    if membership is None:
        return None
    data = {
        "membership_id": membership.id,
        "display_name": membership.display_name,
        "jersey_number": membership.jersey_number,
    }
    if batting_order is not None:
        data["batting_order"] = batting_order
    return data


def payload(game, can_edit):
    pas = plate_appearances(game)
    spots = lineup(game)
    lineup_ids = [spot.membership_id for spot in spots]
    current = state(pas, lineup_ids)
    order_of = {spot.membership_id: spot.batting_order for spot in spots}
    members = {spot.membership_id: spot.membership for spot in spots}
    return {
        "game": game.id,
        "game_status": game.status,
        "counts_toward_season": game.status == Game.Status.COMPLETED,
        "can_edit": can_edit,
        "inning": current["inning"],
        "outs": current["outs"],
        "next_batter": _member(members.get(current["next_batter_id"]), order_of.get(current["next_batter_id"])),
        "on_deck": _member(members.get(current["on_deck_id"]), order_of.get(current["on_deck_id"])),
        "lineup": [_member(spot.membership, spot.batting_order) for spot in spots],
        "plate_appearances": [
            {
                "id": pa.id,
                "sequence": pa.sequence,
                "inning": pa.inning,
                "result": pa.result,
                **_member(pa.membership),
            }
            for pa in pas
        ],
    }
