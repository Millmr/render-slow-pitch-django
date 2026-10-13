from django.contrib import admin

from .models import Game, GameAvailability, Lineup, LineupSpot, PlayerGameStats, Season, Team, TeamMembership


class SeasonInline(admin.TabularInline):
    model = Season
    extra = 0


class MembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "created_by", "created_at"]
    search_fields = ["name"]
    inlines = [SeasonInline, MembershipInline]


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["name", "team", "start_date", "end_date", "is_active"]
    list_filter = ["is_active", "team"]


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ["display_name", "team", "season", "role", "jersey_number", "is_substitute", "is_active", "user"]
    list_filter = ["team", "role", "is_active", "is_substitute"]
    search_fields = ["display_name", "user__username"]
    autocomplete_fields = ["user"]


class PlayerGameStatsInline(admin.TabularInline):
    model = PlayerGameStats
    extra = 0


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ["__str__", "season", "status", "team_score", "opponent_score", "result"]
    list_filter = ["status", "season__team"]
    inlines = [PlayerGameStatsInline]


@admin.register(GameAvailability)
class GameAvailabilityAdmin(admin.ModelAdmin):
    list_display = ["game", "membership", "status", "updated_at"]
    list_filter = ["status"]


class LineupSpotInline(admin.TabularInline):
    model = LineupSpot
    extra = 0


@admin.register(Lineup)
class LineupAdmin(admin.ModelAdmin):
    list_display = ["game", "updated_at"]
    inlines = [LineupSpotInline]


@admin.register(PlayerGameStats)
class PlayerGameStatsAdmin(admin.ModelAdmin):
    list_display = ["membership", "game", "at_bats", "hits", "home_runs", "walks"]
    list_filter = ["game__season"]
