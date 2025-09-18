print("Starting Fantasy Football bot...")

import os
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Select
from dotenv import load_dotenv
from espn_api.football import League
from tabulate import tabulate

def get_current_week_points(player, league):
    """Get current week projected/actual points for a player"""
    # Get current week from league
    current_week = getattr(league, 'current_week', 1)

    # Try to get current week stats from player.stats
    if hasattr(player, 'stats') and player.stats:
        try:
            # ESPN API stores stats by week - try to get current week's actual or projected points
            week_stats = player.stats.get(current_week, {})

            # Try actual points first (for games in progress or completed)
            actual_points = week_stats.get('points', None)
            if actual_points is not None and actual_points > 0:
                return actual_points

            # If no actual points, try projected points
            projected_points = week_stats.get('projected_points', None)
            if projected_points is not None:
                return projected_points

            # Alternative stat keys ESPN might use
            alt_points = week_stats.get('appliedStats', {}).get('0', None)  # ESPN sometimes uses stat ID 0 for fantasy points
            if alt_points is not None:
                return alt_points

        except Exception as e:
            print(f"Error accessing stats for {player.name}: {e}")

    # Fallback to simple attributes (likely season totals)
    return (
        getattr(player, 'projected_points', None)
        or getattr(player, 'points', None)
        or getattr(player, 'avg_points', 0)  # Weekly average as last resort
        or 'N/A'
    )

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
LEAGUE_ID = int(os.getenv('ESPN_LEAGUE_ID'))
SEASON_ID = int(os.getenv('ESPN_SEASON_ID'))
SWID = os.getenv('ESPN_SWID')
ESPN_S2 = os.getenv('ESPN_S2')

# Debug environment variables
print(f"TOKEN present: {bool(TOKEN)}")
print(f"LEAGUE_ID: {LEAGUE_ID}")
print(f"SEASON_ID: {SEASON_ID}")
print(f"SWID present: {bool(SWID)}")
print(f"ESPN_S2 present: {bool(ESPN_S2)}")

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

intents = discord.Intents.default()
client = MyClient(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    print('Bot is ready and commands are synced!')

@client.tree.command(name="ping", description="Check if the bot is alive.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message('Pong!')


@client.tree.command(name="team", description="Get the roster for a team by name.")
@app_commands.describe(team_name="The exact name of the team as it appears in ESPN.")
async def team(interaction: discord.Interaction, team_name: str):
    try:
        # Check if response is already done to prevent duplicate interactions
        if not interaction.response.is_done():
            await interaction.response.defer()
        else:
            print("DEBUG - Interaction already responded to, skipping defer")

        # Quick validation before ESPN API call
        if not LEAGUE_ID or not SEASON_ID:
            await interaction.followup.send("Bot configuration error: Missing league or season ID")
            return

        # Initialize league with timeout protection
        try:
            if SWID and ESPN_S2:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
            else:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID)
        except Exception as api_error:
            await interaction.followup.send(f"ESPN API error: {api_error}")
            return
        team = next((t for t in league.teams if t.team_name.lower() == team_name.lower()), None)
        if not team:
            await interaction.followup.send(f"Team '{team_name}' not found.")
            return
        # Single-width emoji mappings
        pos_emoji = {
            'QB': '🏈', 'RB': '🏃', 'WR': '🏃', 'TE': '🧩', 'K': '🦶', 'D/ST': '🛡️', 'DST': '🛡️', 'DEF': '🛡️', 'Bench': '🪑', 'BE': '🪑', 'IR': '🏥'
        }
        status_emoji = {
            'ACTIVE': '✅', 'QUESTIONABLE': '⚠️', 'OUT': '❌', 'INJURY_RESERVE': '🏥', 'NORMAL': '🔵', None: ''
        }
        status_abbrev = {
            'ACTIVE': 'A', 'QUESTIONABLE': 'Q', 'OUT': 'O', 'INJURY_RESERVE': 'IR', 'NORMAL': 'N', None: ''
        }
        # ESPN lineup slot order for sorting
        slot_order = {
            'QB': 0, 'RB': 1, 'RB2': 2, 'WR': 3, 'WR2': 4, 'TE': 5, 'FLEX': 6, 'D/ST': 7, 'DST': 7, 'K': 8
        }
        flex_names = {'RB/WR/TE', 'WR/RB', 'WR/TE', 'RB/WR'}
        def get_points(player):
            return get_current_week_points(player, league)

        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        def get_status(player):
            status = getattr(player, 'injuryStatus', None)
            abbrev = status_abbrev.get(status, status_abbrev.get('NORMAL', ''))
            return abbrev
        def player_row(player):
            pos = getattr(player, 'position', 'UNK')
            name = f"{pos} {player.name}"
            actual = str(get_points(player))
            status = get_status(player)
            # Format points consistently with 2 decimal places
            if actual == 'N/A':
                points_display = "N/A"
            else:
                points_display = f"{float(actual):5.2f}"
            return [name, status, f"{points_display} pts"]
        # Use lineupSlot == 'BE' for bench, all others are starters
        starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
        bench = [p for p in team.roster if getattr(p, 'lineupSlot', None) == "BE"]
        # Sort starters by ESPN lineup order
        def get_slot_sort_key(player):
            slot = getattr(player, 'lineupSlot', '').upper()
            if slot in flex_names or 'FLEX' in slot:
                return 6
            if slot == 'RB2':
                return 2
            if slot == 'WR2':
                return 4
            return slot_order.get(slot, slot_order.get(player.position, 99))
        starters_sorted = sorted(starters, key=get_slot_sort_key)
        # Calculate total starter points
        total_starter_points = sum(float(get_points(p)) for p in starters_sorted if get_points(p) != 'N/A')
        # Create custom formatted table for perfect alignment
        header = f"{'Player':<22} {'Status':<8} {'Projected':>9}"
        separator = f"{'-'*22} {'-'*8} {'-'*9}"

        lines = [header, separator]
        for p in starters_sorted:
            # Use position abbreviation instead of emoji
            pos = getattr(p, 'position', 'UNK')
            name = f"{pos} {p.name}"
            status = get_status(p)
            points = get_points(p)

            if points == 'N/A':
                points_str = "N/A pts"
            else:
                points_str = f"{float(points):5.2f} pts"

            line = f"{name:<22} {status:<8} {points_str:>9}"
            lines.append(line)

        lines.append(f"\nTotal Starter Points: {total_starter_points:.2f}")
        starters_text = f"```\n{chr(10).join(lines)}\n```"
        starters_text = starters_text if starters_sorted else "None"
        # Create custom formatted bench table for perfect alignment
        if bench:
            bench_header = f"{'Player':<22} {'Status':<8} {'Projected':>9}"
            bench_separator = f"{'-'*22} {'-'*8} {'-'*9}"

            bench_lines = [bench_header, bench_separator]
            for p in bench:
                # Use position abbreviation instead of emoji
                pos = getattr(p, 'position', 'UNK')
                name = f"{pos} {p.name}"
                status = get_status(p)
                points = get_points(p)

                if points == 'N/A':
                    points_str = "N/A pts"
                else:
                    points_str = f"{float(points):5.2f} pts"

                line = f"{name:<22} {status:<8} {points_str:>9}"
                bench_lines.append(line)

            bench_text = f"```\n{chr(10).join(bench_lines)}\n```"
        else:
            bench_text = "None"
        # Get current week
        current_week = getattr(league, 'current_week', 'Unknown')
        embed = discord.Embed(title=f"🏈 {team.team_name} Roster - Week {current_week}", color=discord.Color.blue())
        if hasattr(team, 'logo_url') and team.logo_url:
            embed.set_thumbnail(url=team.logo_url)
        else:
            embed.set_thumbnail(url="https://a.espncdn.com/i/espn/logos/nfl/NFL.png")
        embed.add_field(name="Starters", value=starters_text, inline=False)
        embed.add_field(name="Bench", value=bench_text, inline=False)
        
        # Create interactive view with buttons
        view = TeamView(team, league)
        await interaction.followup.send(embed=embed, view=view)
    except Exception as e:
        error_msg = f"Error fetching team: {e}"
        print(f"Team command error: {e}")  # Log for debugging
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_msg)
            else:
                await interaction.response.send_message(error_msg)
        except Exception as follow_error:
            print(f"Failed to send error message: {follow_error}")

@client.tree.command(name="player", description="Get detailed stats for a specific player.")
@app_commands.describe(player_name="The name of the player to look up.")
async def player(interaction: discord.Interaction, player_name: str):
    try:
        await interaction.response.defer()
        if SWID and ESPN_S2:
            league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
        else:
            league = League(league_id=LEAGUE_ID, year=SEASON_ID)
        # Search for player across all teams
        found_player = None
        player_team = None
        for team in league.teams:
            for p in team.roster:
                if player_name.lower() in p.name.lower():
                    found_player = p
                    player_team = team
                    break
            if found_player:
                break
        if not found_player:
            await interaction.followup.send(f"Player '{player_name}' not found in any team.")
            return
        # Try different opponent attribute names
        opponent = (
            getattr(found_player, 'opponent', None) or
            getattr(found_player, 'proOpponent', None) or
            getattr(found_player, 'nextOpponent', None) or
            getattr(found_player, 'opp', None) or
            'N/A'
        )
        
        # Get player stats - use the same logic as team command
        def get_points(player):
            return get_current_week_points(player, league)
        
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        
        actual_points = get_points(found_player)
        proj_points = get_proj(found_player)
        season_total = getattr(found_player, 'total_points', 'N/A')
        injury_status = getattr(found_player, 'injuryStatus', 'N/A')
        nfl_team = getattr(found_player, 'proTeam', 'N/A')
        position = getattr(found_player, 'position', 'N/A')
        # Create detailed embed
        embed = discord.Embed(title=f"📊 {found_player.name}", color=discord.Color.green())
        embed.add_field(name="Position", value=f"{position} - {nfl_team}", inline=True)
        # Format points for display
        if actual_points == 'N/A':
            points_formatted = "N/A"
        else:
            points_formatted = f"{float(actual_points):.2f}"
        embed.add_field(name="Projected", value=f"{points_formatted} pts", inline=True)
        embed.add_field(name="Season Total", value=f"{season_total} pts", inline=True)
        embed.add_field(name="Injury Status", value=injury_status, inline=True)
        embed.add_field(name="Team", value=player_team.team_name, inline=True)
        embed.add_field(name="Opponent", value=opponent, inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error fetching player: {e}")

@client.tree.command(name="compare", description="Compare two teams side-by-side.")
@app_commands.describe(team1="First team name", team2="Second team name")
async def compare(interaction: discord.Interaction, team1: str, team2: str):
    try:
        await interaction.response.defer()
        if SWID and ESPN_S2:
            league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
        else:
            league = League(league_id=LEAGUE_ID, year=SEASON_ID)
        
        # Find both teams
        team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
        team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
        
        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found.")
            return
        if not team2_obj:
            await interaction.followup.send(f"Team '{team2}' not found.")
            return
        
        # Get comprehensive team stats
        def get_points(player):
            return get_current_week_points(player, league)

        # Current week projected points
        team1_starters = [p for p in team1_obj.roster if getattr(p, 'lineupSlot', None) != "BE"]
        team2_starters = [p for p in team2_obj.roster if getattr(p, 'lineupSlot', None) != "BE"]

        team1_weekly = sum(float(get_points(p)) for p in team1_starters if get_points(p) != 'N/A')
        team2_weekly = sum(float(get_points(p)) for p in team2_starters if get_points(p) != 'N/A')

        # Season stats
        team1_wins = getattr(team1_obj, 'wins', 0)
        team1_losses = getattr(team1_obj, 'losses', 0)
        team1_ties = getattr(team1_obj, 'ties', 0)
        team1_season_points = getattr(team1_obj, 'points_for', 0.0)

        team2_wins = getattr(team2_obj, 'wins', 0)
        team2_losses = getattr(team2_obj, 'losses', 0)
        team2_ties = getattr(team2_obj, 'ties', 0)
        team2_season_points = getattr(team2_obj, 'points_for', 0.0)

        # Head-to-head record
        h2h_team1_wins = 0
        h2h_team2_wins = 0
        h2h_ties = 0

        # Check team schedules for head-to-head matchups
        try:
            for week_num, matchup in enumerate(team1_obj.schedule, 1):
                if matchup and hasattr(matchup, 'away_team') and hasattr(matchup, 'home_team'):
                    opponent = matchup.away_team if matchup.home_team == team1_obj else matchup.home_team
                    if opponent == team2_obj:
                        # Found a head-to-head matchup
                        if hasattr(matchup, 'winner'):
                            if matchup.winner == team1_obj:
                                h2h_team1_wins += 1
                            elif matchup.winner == team2_obj:
                                h2h_team2_wins += 1
                            else:
                                h2h_ties += 1
        except Exception as e:
            print(f"Error calculating head-to-head: {e}")

        # Create comprehensive comparison
        current_week = getattr(league, 'current_week', 'Unknown')
        embed = discord.Embed(title=f"⚔️ Team Comparison - Week {current_week}", color=discord.Color.purple())

        # Create team name abbreviations for table headers (max 12 chars to fit columns)
        team1_abbrev = team1_obj.team_name[:12]
        team2_abbrev = team2_obj.team_name[:12]

        # Format comparison data
        comparison_data = f"""```
{'Metric':<20} {team1_abbrev:<12} {team2_abbrev:<12}
{'-'*20} {'-'*12} {'-'*12}
{'Record':<20} {team1_wins}-{team1_losses}-{team1_ties:<9} {team2_wins}-{team2_losses}-{team2_ties}
{'Season Points':<20} {team1_season_points:<12.2f} {team2_season_points:<12.2f}
{'Weekly Projected':<20} {team1_weekly:<12.2f} {team2_weekly:<12.2f}
{'Head-to-Head':<20} {h2h_team1_wins}-{h2h_team2_wins}-{h2h_ties:<9} {h2h_team2_wins}-{h2h_team1_wins}-{h2h_ties}
```"""

        embed.add_field(name="📈 Comparison Stats", value=comparison_data, inline=False)

        # Analysis
        analysis_lines = []

        # Weekly projection leader
        if team1_weekly > team2_weekly:
            analysis_lines.append(f"📅 **This Week**: {team1_obj.team_name} projected to score {team1_weekly - team2_weekly:.2f} pts more")
        elif team2_weekly > team1_weekly:
            analysis_lines.append(f"📅 **This Week**: {team2_obj.team_name} projected to score {team2_weekly - team1_weekly:.2f} pts more")
        else:
            analysis_lines.append("📅 **This Week**: Projected to score the same!")

        # Season performance
        if team1_season_points > team2_season_points:
            analysis_lines.append(f"🏆 **Season Leader**: {team1_obj.team_name} (+{team1_season_points - team2_season_points:.2f} pts)")
        elif team2_season_points > team1_season_points:
            analysis_lines.append(f"🏆 **Season Leader**: {team2_obj.team_name} (+{team2_season_points - team1_season_points:.2f} pts)")
        else:
            analysis_lines.append("🏆 **Season**: Tied in total points!")

        # Head-to-head
        if h2h_team1_wins > h2h_team2_wins:
            analysis_lines.append(f"⚔️ **Head-to-Head**: {team1_obj.team_name} leads series {h2h_team1_wins}-{h2h_team2_wins}")
        elif h2h_team2_wins > h2h_team1_wins:
            analysis_lines.append(f"⚔️ **Head-to-Head**: {team2_obj.team_name} leads series {h2h_team2_wins}-{h2h_team1_wins}")
        elif h2h_team1_wins + h2h_team2_wins > 0:
            analysis_lines.append(f"⚔️ **Head-to-Head**: Series tied {h2h_team1_wins}-{h2h_team2_wins}")
        else:
            analysis_lines.append("⚔️ **Head-to-Head**: No previous matchups found")

        embed.add_field(name="🔍 Analysis", value="\n".join(analysis_lines), inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error comparing teams: {e}")

@client.tree.command(name="standings", description="Show league standings with records and points.")
async def standings(interaction: discord.Interaction):
    try:
        await interaction.response.defer()

        # Quick validation before ESPN API call
        if not LEAGUE_ID or not SEASON_ID:
            await interaction.followup.send("Bot configuration error: Missing league or season ID")
            return

        # Initialize league
        try:
            if SWID and ESPN_S2:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
            else:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID)
        except Exception as api_error:
            await interaction.followup.send(f"ESPN API error: {api_error}")
            return

        # Get all teams and their stats
        teams_data = []
        for team in league.teams:
            wins = getattr(team, 'wins', 0)
            losses = getattr(team, 'losses', 0)
            ties = getattr(team, 'ties', 0)
            points_for = getattr(team, 'points_for', 0.0)
            points_against = getattr(team, 'points_against', 0.0)

            # Calculate win percentage
            total_games = wins + losses + ties
            if total_games > 0:
                win_pct = (wins + (ties * 0.5)) / total_games
            else:
                win_pct = 0.0

            teams_data.append({
                'name': team.team_name,
                'wins': wins,
                'losses': losses,
                'ties': ties,
                'points_for': points_for,
                'points_against': points_against,
                'win_pct': win_pct
            })

        # Sort teams by win percentage (descending), then by points for (descending)
        teams_data.sort(key=lambda x: (x['win_pct'], x['points_for']), reverse=True)

        # Create standings table
        current_week = getattr(league, 'current_week', 'Unknown')
        embed = discord.Embed(title=f"🏆 League Standings - Week {current_week}", color=discord.Color.gold())

        # Format standings table
        standings_lines = []
        standings_lines.append(f"{'Rank':<4} {'Team':<20} {'Record':<8} {'PF':<8} {'PA':<8}")
        standings_lines.append(f"{'-'*4} {'-'*20} {'-'*8} {'-'*8} {'-'*8}")

        for rank, team in enumerate(teams_data, 1):
            name = team['name'][:20]  # Truncate long names
            record = f"{team['wins']}-{team['losses']}-{team['ties']}"
            pf = f"{team['points_for']:.1f}"
            pa = f"{team['points_against']:.1f}"

            line = f"{rank:<4} {name:<20} {record:<8} {pf:<8} {pa:<8}"
            standings_lines.append(line)

        standings_table = f"```\n{chr(10).join(standings_lines)}\n```"
        embed.add_field(name="📊 Current Standings", value=standings_table, inline=False)

        # Add some league stats
        highest_scoring = max(teams_data, key=lambda x: x['points_for']) if teams_data else None
        lowest_scoring = min(teams_data, key=lambda x: x['points_for']) if teams_data else None

        # Find highest single weekly score across all teams and weeks
        highest_weekly_score = 0.0
        highest_weekly_team = None
        highest_weekly_week = None

        try:
            current_week = getattr(league, 'current_week', 1)
            for team in league.teams:
                # Check each week's score for this team
                for week_num in range(1, current_week):
                    try:
                        # Try to get weekly score from team's schedule/matchups
                        if hasattr(team, 'scores') and week_num <= len(team.scores):
                            weekly_score = team.scores[week_num - 1]  # scores list is 0-indexed
                            if weekly_score and weekly_score > highest_weekly_score:
                                highest_weekly_score = weekly_score
                                highest_weekly_team = team.team_name
                                highest_weekly_week = week_num
                    except (IndexError, AttributeError, TypeError):
                        # If scores attribute doesn't exist or is formatted differently,
                        # try alternative method with matchups
                        try:
                            if hasattr(team, 'schedule') and week_num <= len(team.schedule):
                                matchup = team.schedule[week_num - 1]
                                if matchup and hasattr(matchup, 'home_score') and hasattr(matchup, 'away_score'):
                                    # Determine if this team was home or away
                                    if hasattr(matchup, 'home_team') and matchup.home_team == team:
                                        weekly_score = matchup.home_score
                                    elif hasattr(matchup, 'away_team') and matchup.away_team == team:
                                        weekly_score = matchup.away_score
                                    else:
                                        continue

                                    if weekly_score and weekly_score > highest_weekly_score:
                                        highest_weekly_score = weekly_score
                                        highest_weekly_team = team.team_name
                                        highest_weekly_week = week_num
                        except (IndexError, AttributeError, TypeError):
                            continue
        except Exception as e:
            print(f"Error calculating highest weekly score: {e}")

        stats_lines = []
        if highest_scoring:
            stats_lines.append(f"🔥 **Highest Scoring**: {highest_scoring['name']} ({highest_scoring['points_for']:.1f} pts)")
        if lowest_scoring:
            stats_lines.append(f"🧊 **Lowest Scoring**: {lowest_scoring['name']} ({lowest_scoring['points_for']:.1f} pts)")
        if highest_weekly_team and highest_weekly_score > 0:
            stats_lines.append(f"💥 **Best Weekly Score**: {highest_weekly_team} - {highest_weekly_score:.1f} pts (Week {highest_weekly_week})")
        else:
            stats_lines.append("💥 **Best Weekly Score**: Not available")

        embed.add_field(name="📈 League Stats", value="\n".join(stats_lines), inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"Error fetching standings: {e}"
        print(f"Standings command error: {e}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_msg)
            else:
                await interaction.response.send_message(error_msg)
        except Exception as follow_error:
            print(f"Failed to send error message: {follow_error}")

@client.tree.command(name="stats", description="Show detailed league analytics and interesting statistics.")
async def stats(interaction: discord.Interaction):
    try:
        await interaction.response.defer()

        # Quick validation before ESPN API call
        if not LEAGUE_ID or not SEASON_ID:
            await interaction.followup.send("Bot configuration error: Missing league or season ID")
            return

        # Initialize league
        try:
            if SWID and ESPN_S2:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
            else:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID)
        except Exception as api_error:
            await interaction.followup.send(f"ESPN API error: {api_error}")
            return

        current_week = getattr(league, 'current_week', 1)

        # Collect all team data with weekly scores
        teams_analytics = []

        for team in league.teams:
            weekly_scores = []

            # Get weekly scores for this team
            for week_num in range(1, current_week):
                try:
                    weekly_score = None
                    # Try multiple methods to get weekly scores
                    if hasattr(team, 'scores') and week_num <= len(team.scores):
                        weekly_score = team.scores[week_num - 1]
                    elif hasattr(team, 'schedule') and week_num <= len(team.schedule):
                        matchup = team.schedule[week_num - 1]
                        if matchup and hasattr(matchup, 'home_score') and hasattr(matchup, 'away_score'):
                            if hasattr(matchup, 'home_team') and matchup.home_team == team:
                                weekly_score = matchup.home_score
                            elif hasattr(matchup, 'away_team') and matchup.away_team == team:
                                weekly_score = matchup.away_score

                    if weekly_score and weekly_score > 0:
                        weekly_scores.append(weekly_score)
                except (IndexError, AttributeError, TypeError):
                    continue

            # Calculate team analytics
            team_data = {
                'name': team.team_name,
                'wins': getattr(team, 'wins', 0),
                'losses': getattr(team, 'losses', 0),
                'ties': getattr(team, 'ties', 0),
                'points_for': getattr(team, 'points_for', 0.0),
                'points_against': getattr(team, 'points_against', 0.0),
                'weekly_scores': weekly_scores
            }

            # Calculate consistency (standard deviation)
            if len(weekly_scores) > 1:
                avg_score = sum(weekly_scores) / len(weekly_scores)
                variance = sum((score - avg_score) ** 2 for score in weekly_scores) / len(weekly_scores)
                team_data['std_dev'] = variance ** 0.5
                team_data['avg_weekly'] = avg_score
            else:
                team_data['std_dev'] = 0
                team_data['avg_weekly'] = weekly_scores[0] if weekly_scores else 0

            # Calculate efficiency (wins per point)
            total_games = team_data['wins'] + team_data['losses'] + team_data['ties']
            if total_games > 0 and team_data['points_for'] > 0:
                win_pct = (team_data['wins'] + team_data['ties'] * 0.5) / total_games
                team_data['efficiency'] = win_pct / (team_data['points_for'] / 1000)  # Normalize points
            else:
                team_data['efficiency'] = 0

            teams_analytics.append(team_data)

        # Calculate interesting stats
        embed = discord.Embed(title=f"📈 League Analytics - Week {current_week}", color=discord.Color.blue())

        # 1. Consistency/Volatility
        if teams_analytics:
            most_consistent = min(teams_analytics, key=lambda x: x['std_dev'])
            most_volatile = max(teams_analytics, key=lambda x: x['std_dev'])

            consistency_text = f"""```
Most Consistent Team (Low Variance)
🎯 {most_consistent['name']:<25} ±{most_consistent['std_dev']:.1f} pts

Most Volatile Team (High Variance)
🎢 {most_volatile['name']:<25} ±{most_volatile['std_dev']:.1f} pts
```"""
            embed.add_field(name="📊 Team Consistency Analysis", value=consistency_text, inline=False)

        # 2. Weekly Extremes
        all_weekly_scores = []
        for team in teams_analytics:
            for score in team['weekly_scores']:
                all_weekly_scores.append((score, team['name']))

        if all_weekly_scores:
            highest_weekly = max(all_weekly_scores, key=lambda x: x[0])
            lowest_weekly = min(all_weekly_scores, key=lambda x: x[0])

            extremes_text = f"""```
Best Single Week Performance
💥 {highest_weekly[1]:<25} {highest_weekly[0]:.1f} pts

Worst Single Week Performance
🧊 {lowest_weekly[1]:<25} {lowest_weekly[0]:.1f} pts
```"""
            embed.add_field(name="🔥 Weekly Performance Extremes", value=extremes_text, inline=False)

        # 3. Luck/Efficiency Metrics
        # Find team with worst luck (high scoring but poor record)
        unlucky_teams = []
        efficient_teams = []

        for team in teams_analytics:
            total_games = team['wins'] + team['losses'] + team['ties']
            if total_games > 0:
                win_pct = (team['wins'] + team['ties'] * 0.5) / total_games
                # Calculate expected wins based on points scored vs league average
                avg_league_points = sum(t['points_for'] for t in teams_analytics) / len(teams_analytics)
                if team['points_for'] > avg_league_points and win_pct < 0.5:
                    unlucky_teams.append((team, win_pct, team['points_for']))

                efficient_teams.append((team, team['efficiency'], win_pct))

        # Format luck and efficiency stats
        luck_lines = []

        if efficient_teams:
            most_efficient = max(efficient_teams, key=lambda x: x[1])
            luck_lines.append(f"⚡ {most_efficient[0]['name']:<25} {most_efficient[2]:.1%} win rate")

        if unlucky_teams:
            unluckiest = max(unlucky_teams, key=lambda x: x[2] - (x[1] * 1000))  # Points minus expected
            luck_lines.append(f"😭 {unluckiest[0]['name']:<25} {unluckiest[1]:.1%} wins ({unluckiest[2]:.1f} pts)")

        # Schedule difficulty
        toughest_schedule = max(teams_analytics, key=lambda x: x['points_against'])
        easiest_schedule = min(teams_analytics, key=lambda x: x['points_against'])

        schedule_lines = [
            f"💪 {toughest_schedule['name']:<25} {toughest_schedule['points_against']:.1f} PA",
            f"😎 {easiest_schedule['name']:<25} {easiest_schedule['points_against']:.1f} PA"
        ]

        if luck_lines or schedule_lines:
            luck_efficiency_text = f"""```
Team Efficiency
{chr(10).join(luck_lines) if luck_lines else 'No efficiency data available'}

Schedule Difficulty
{chr(10).join(schedule_lines)}
```"""
            embed.add_field(name="🍀 Luck & Efficiency Analysis", value=luck_efficiency_text, inline=False)

        # 4. Current Streaks (placeholder for now)
        streak_text = """```
Current Momentum Analysis
🔥 Win/Loss streaks coming soon
📊 Recent form trends coming soon

Note: Requires additional matchup history data
```"""
        embed.add_field(name="📈 Momentum & Trends", value=streak_text, inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"Error fetching stats: {e}"
        print(f"Stats command error: {e}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_msg)
            else:
                await interaction.response.send_message(error_msg)
        except Exception as follow_error:
            print(f"Failed to send error message: {follow_error}")

# Interactive View for Team Command
class TeamView(View):
    def __init__(self, team, league):
        super().__init__(timeout=300)  # 5 minute timeout
        self.team = team
        self.league = league
        
        # Add filter buttons
        self.add_item(FilterByPositionButton("QB", "🏈"))
        self.add_item(FilterByPositionButton("RB", "🏃"))
        self.add_item(FilterByPositionButton("WR", "🏃"))
        self.add_item(FilterByPositionButton("TE", "🧩"))
        self.add_item(FilterByPositionButton("K", "🦶"))
        self.add_item(FilterByPositionButton("D/ST", "🛡️"))
        self.add_item(ShowAllButton())
        self.add_item(PlayerSelectDropdown(team))

class FilterByPositionButton(Button):
    def __init__(self, position, emoji):
        super().__init__(label=position, emoji=emoji, style=discord.ButtonStyle.secondary)
        self.position = position
    
    async def callback(self, interaction: discord.Interaction):
        # Filter team roster by position
        filtered_players = [p for p in self.view.team.roster if p.position == self.position]
        
        if not filtered_players:
            await interaction.response.send_message(f"No {self.position} players found on {self.view.team.team_name}.", ephemeral=True)
            return
        
        # Format filtered players
        def get_points(player):
            return get_current_week_points(player, self.view.league)
        
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )

        def player_row(player):
            pos = getattr(player, 'position', 'UNK')
            name = f"{pos} {player.name}"
            actual = str(get_points(player))
            if actual == 'N/A':
                points_display = "  N/A"
            else:
                points_display = f"{float(actual):5.2f}"
            return [name, f"{points_display} pts"]
        
        # Create custom formatted table for filtered players
        filter_header = f"{'Player':<22} {'Projected':>9}"
        filter_separator = f"{'-'*22} {'-'*9}"

        filter_lines = [filter_header, filter_separator]
        for p in filtered_players:
            # Use position abbreviation instead of emoji
            pos = getattr(p, 'position', 'UNK')
            name = f"{pos} {p.name}"
            points = get_points(p)

            if points == 'N/A':
                points_str = "N/A pts"
            else:
                points_str = f"{float(points):5.2f} pts"

            line = f"{name:<22} {points_str:>9}"
            filter_lines.append(line)

        players_text = f"```\n{chr(10).join(filter_lines)}\n```"
        
        embed = discord.Embed(title=f"🏈 {self.view.team.team_name} - {self.position} Players", color=discord.Color.blue())
        embed.add_field(name=f"{self.position} Players", value=players_text, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ShowAllButton(Button):
    def __init__(self):
        super().__init__(label="Show All", emoji="📋", style=discord.ButtonStyle.primary)
    
    async def callback(self, interaction: discord.Interaction):
        # Re-send the original team roster
        await interaction.response.defer()
        
        # Recreate the original team command logic
        pos_emoji = {
            'QB': '🏈', 'RB': '🏃', 'WR': '🏃', 'TE': '🧩', 'K': '🦶', 'D/ST': '🛡️', 'DST': '🛡️', 'DEF': '🛡️', 'Bench': '🪑', 'BE': '🪑', 'IR': '🏥'
        }
        status_emoji = {
            'ACTIVE': '✅', 'QUESTIONABLE': '⚠️', 'OUT': '❌', 'INJURY_RESERVE': '🏥', 'NORMAL': '🔵', None: ''
        }
        status_abbrev = {
            'ACTIVE': 'A', 'QUESTIONABLE': 'Q', 'OUT': 'O', 'INJURY_RESERVE': 'IR', 'NORMAL': 'N', None: ''
        }

        def get_points(player):
            return get_current_week_points(player, self.view.league)
        
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        
        def get_status(player):
            status = getattr(player, 'injuryStatus', None)
            abbrev = status_abbrev.get(status, status_abbrev.get('NORMAL', ''))
            return abbrev
        
        
        def player_line(player):
            pos = getattr(player, 'position', 'UNK')
            name = f"{pos} {player.name}"
            actual = str(get_points(player))
            status = get_status(player)
            # Format points consistently with fixed width
            if actual == 'N/A':
                points_display = "N/A pts"
            else:
                points_display = f"{float(actual):5.2f} pts"
            # Use consistent formatting
            return f"{name:<22} {status:<8} {points_display:>9}"
        
        starters = [p for p in self.view.team.roster if getattr(p, 'lineupSlot', None) != "BE"]
        bench = [p for p in self.view.team.roster if getattr(p, 'lineupSlot', None) == "BE"]
        
        total_starter_points = sum(float(get_points(p)) for p in starters if get_points(p) != 'N/A')
        
        starters_text = f"""```
{chr(10).join(player_line(p) for p in starters)}
────────────────────────────────────────────
Total Starter Points: {total_starter_points:.2f}
```""" if starters else "None"
        
        bench_text = f"""```
{chr(10).join(player_line(p) for p in bench)}
```""" if bench else "None"
        
        current_week = getattr(self.view.league, 'current_week', 'Unknown')
        embed = discord.Embed(title=f"🏈 {self.view.team.team_name} Roster - Week {current_week}", color=discord.Color.blue())
        
        if hasattr(self.view.team, 'logo_url') and self.view.team.logo_url:
            embed.set_thumbnail(url=self.view.team.logo_url)
        else:
            embed.set_thumbnail(url="https://a.espncdn.com/i/espn/logos/nfl/NFL.png")
        
        embed.add_field(name="Starters", value=starters_text, inline=False)
        embed.add_field(name="Bench", value=bench_text, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

class PlayerSelectDropdown(Select):
    def __init__(self, team):
        # Create options for all players on the team
        options = []
        for player in team.roster:
            options.append(discord.SelectOption(
                label=player.name,
                description=f"{player.position} - {player.proTeam}",
                value=player.name
            ))
        
        super().__init__(placeholder="Select a player for details...", options=options[:25])  # Discord limit
        self.team = team
    
    async def callback(self, interaction: discord.Interaction):
        selected_player_name = self.values[0]
        
        # Find the selected player
        selected_player = next((p for p in self.team.roster if p.name == selected_player_name), None)
        
        if not selected_player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return

        # Get player stats - use the same logic as team command
        def get_points(player):
            return get_current_week_points(player, self.view.league)
        
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        
        # Try different opponent attribute names
        opponent = (
            getattr(selected_player, 'opponent', None) or
            getattr(selected_player, 'proOpponent', None) or
            getattr(selected_player, 'nextOpponent', None) or
            getattr(selected_player, 'opp', None) or
            'N/A'
        )
        
        actual_points = get_points(selected_player)
        proj_points = get_proj(selected_player)
        season_total = getattr(selected_player, 'total_points', 'N/A')
        injury_status = getattr(selected_player, 'injuryStatus', 'N/A')
        nfl_team = getattr(selected_player, 'proTeam', 'N/A')
        position = getattr(selected_player, 'position', 'N/A')
        
        # Create detailed embed
        embed = discord.Embed(title=f"📊 {selected_player.name}", color=discord.Color.green())
        embed.add_field(name="Position", value=f"{position} - {nfl_team}", inline=True)
        # Format points for display
        if actual_points == 'N/A':
            points_formatted = "N/A"
        else:
            points_formatted = f"{float(actual_points):.2f}"
        embed.add_field(name="Projected", value=f"{points_formatted} pts", inline=True)
        embed.add_field(name="Season Total", value=f"{season_total} pts", inline=True)
        embed.add_field(name="Injury Status", value=injury_status, inline=True)
        embed.add_field(name="Team", value=self.team.team_name, inline=True)
        embed.add_field(name="Opponent", value=opponent, inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == '__main__':
    try:
        print("Attempting to connect to Discord...")
        client.run(TOKEN)
    except Exception as e:
        print(f"Bot failed to start: {e}")
        import traceback
        traceback.print_exc()
