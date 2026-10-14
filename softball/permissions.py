"""Team-scoped access rules.

Every query for team data starts from the teams the requesting user actively
belongs to, so changing an ID in a URL to another team's object returns 404
rather than leaking that it exists. Write operations then check for the
manager role and return 403 if it's missing. These checks live on the backend;
hiding buttons in React is only a convenience.
"""

from rest_framework.exceptions import PermissionDenied

from .models import TeamMembership


def active_memberships(user):
    if not user or not user.is_authenticated:
        return TeamMembership.objects.none()
    return TeamMembership.objects.filter(user=user, is_active=True)


def visible_team_ids(user):
    """Subquery of team IDs the user can read."""
    return active_memberships(user).values("team_id")


def is_manager(user, team) -> bool:
    return active_memberships(user).filter(team=team, role=TeamMembership.Role.MANAGER).exists()


def require_manager(user, team) -> None:
    if not is_manager(user, team):
        raise PermissionDenied("Only team managers can do that.")


BOOK_ROLES = (TeamMembership.Role.MANAGER, TeamMembership.Role.SCOREKEEPER)


def can_keep_book(user, team) -> bool:
    """Managers and scorekeepers record plays in the book."""
    return active_memberships(user).filter(team=team, role__in=BOOK_ROLES).exists()


def require_book_keeper(user, team) -> None:
    if not can_keep_book(user, team):
        raise PermissionDenied("Only managers and scorekeepers can keep the book.")
