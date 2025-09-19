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

@client.tree.command(name="sleeper", description="Find undervalued sleeper picks with high upside potential.")
@app_commands.describe(position="Filter by position (QB, RB, WR, TE, K, D/ST) - leave empty for all positions")
async def sleeper(interaction: discord.Interaction, position: str = None):
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

        # Get all rostered players
        rostered_players = set()
        for team in league.teams:
            for player in team.roster:
                rostered_players.add(getattr(player, 'playerId', player.name))

        # Get free agents (this might be limited by ESPN API access)
        sleeper_candidates = []

        try:
            # Try to get free agents from ESPN API
            if hasattr(league, 'free_agents'):
                free_agents = league.free_agents()
            else:
                # Alternative approach - simulate common sleeper types
                free_agents = []

            for player in free_agents:
                # Skip if already rostered
                player_id = getattr(player, 'playerId', player.name)
                if player_id in rostered_players:
                    continue

                # Filter by position if specified
                player_pos = getattr(player, 'position', 'UNK')
                if position and player_pos.upper() != position.upper():
                    continue

                # Calculate sleeper score based on various factors
                projected_points = get_current_week_points(player, league)

                # Skip players with no projection
                if projected_points == 'N/A' or projected_points <= 0:
                    continue

                # Get additional player stats
                ownership_pct = getattr(player, 'percent_owned', 0)
                avg_points = getattr(player, 'avg_points', 0)
                total_points = getattr(player, 'total_points', 0)

                # Calculate sleeper score (high projection, low ownership)
                sleeper_score = 0
                if ownership_pct < 50:  # Less than 50% owned
                    sleeper_score += (50 - ownership_pct) * 0.1

                if projected_points > avg_points:  # Projected higher than average
                    sleeper_score += (projected_points - avg_points) * 0.5

                if projected_points >= 10:  # Decent projection threshold
                    sleeper_score += projected_points * 0.2

                sleeper_candidates.append({
                    'name': player.name,
                    'position': player_pos,
                    'projected': projected_points,
                    'ownership': ownership_pct,
                    'avg_points': avg_points,
                    'sleeper_score': sleeper_score,
                    'team': getattr(player, 'proTeam', 'UNK')
                })

        except Exception as e:
            print(f"Error accessing free agents: {e}")
            # Fallback: Analyze bench players from all teams as potential sleepers
            for team in league.teams:
                for player in team.roster:
                    if getattr(player, 'lineupSlot', None) == "BE":  # Bench players
                        player_pos = getattr(player, 'position', 'UNK')
                        if position and player_pos.upper() != position.upper():
                            continue

                        projected_points = get_current_week_points(player, league)
                        if projected_points == 'N/A' or projected_points <= 5:
                            continue

                        avg_points = getattr(player, 'avg_points', 0)
                        sleeper_score = projected_points * 0.3

                        if projected_points > avg_points and avg_points > 0:
                            sleeper_score += (projected_points - avg_points) * 0.4

                        sleeper_candidates.append({
                            'name': player.name,
                            'position': player_pos,
                            'projected': projected_points,
                            'ownership': 100,  # Rostered
                            'avg_points': avg_points,
                            'sleeper_score': sleeper_score,
                            'team': getattr(player, 'proTeam', 'UNK'),
                            'fantasy_team': team.team_name
                        })

        # Sort by sleeper score
        sleeper_candidates.sort(key=lambda x: x['sleeper_score'], reverse=True)

        # Create embed
        pos_filter = f" ({position.upper()})" if position else ""
        embed = discord.Embed(title=f"💤 Sleeper Picks{pos_filter}", color=discord.Color.green())

        if not sleeper_candidates:
            embed.add_field(name="No Sleepers Found", value="No undervalued players found with current criteria.", inline=False)
        else:
            # Top sleepers
            top_sleepers = sleeper_candidates[:8]  # Top 8 to avoid Discord limits

            sleeper_lines = []
            sleeper_lines.append(f"{'Player':<18} {'Pos':<3} {'Proj':<6} {'Own%':<5} {'Score':<5}")
            sleeper_lines.append(f"{'-'*18} {'-'*3} {'-'*6} {'-'*5} {'-'*5}")

            for sleeper in top_sleepers:
                name = sleeper['name'][:18]
                pos = sleeper['position'][:3]
                proj = f"{sleeper['projected']:.1f}"

                if 'fantasy_team' in sleeper:
                    own = "ROST"  # Rostered
                else:
                    own = f"{sleeper['ownership']:.0f}%"

                score = f"{sleeper['sleeper_score']:.1f}"

                line = f"{name:<18} {pos:<3} {proj:<6} {own:<5} {score:<5}"
                sleeper_lines.append(line)

            sleepers_table = f"```\n{chr(10).join(sleeper_lines)}\n```"
            embed.add_field(name="🎯 Top Sleeper Candidates", value=sleepers_table, inline=False)

            # Analysis
            analysis_lines = []
            best_sleeper = sleeper_candidates[0]
            analysis_lines.append(f"🌟 **Top Pick**: {best_sleeper['name']} ({best_sleeper['position']})")
            analysis_lines.append(f"📈 **Projected**: {best_sleeper['projected']:.1f} pts this week")

            if 'fantasy_team' in best_sleeper:
                analysis_lines.append(f"📍 **Status**: Benched on {best_sleeper['fantasy_team']}")
            else:
                analysis_lines.append(f"📍 **Status**: {best_sleeper['ownership']:.0f}% owned")

            # Add insights
            insights = []
            high_proj_count = len([s for s in sleeper_candidates if s['projected'] >= 15])
            if high_proj_count > 0:
                insights.append(f"🔥 {high_proj_count} players projected for 15+ pts")

            low_owned_count = len([s for s in sleeper_candidates if s.get('ownership', 100) < 25])
            if low_owned_count > 0:
                insights.append(f"💎 {low_owned_count} players under 25% ownership")

            if insights:
                analysis_lines.extend(insights)

            embed.add_field(name="🔍 Analysis", value="\n".join(analysis_lines), inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"Error finding sleepers: {e}"
        print(f"Sleeper command error: {e}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_msg)
            else:
                await interaction.response.send_message(error_msg)
        except Exception as follow_error:
            print(f"Failed to send error message: {follow_error}")

@client.tree.command(name="matchup", description="Detailed head-to-head matchup analysis for this week.")
@app_commands.describe(team1="First team name", team2="Second team name (optional - will try to find current matchup)")
async def matchup(interaction: discord.Interaction, team1: str, team2: str = None):
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

        # Find first team
        team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found.")
            return

        team2_obj = None
        current_week = getattr(league, 'current_week', 1)

        # If team2 not specified, try to find current week opponent
        if not team2:
            print(f"DEBUG: Trying to find opponent for {team1_obj.team_name} in week {current_week}")

            # Method 1: Try team schedule
            try:
                if hasattr(team1_obj, 'schedule') and team1_obj.schedule:
                    print(f"DEBUG: Team has schedule with {len(team1_obj.schedule)} entries")
                    if len(team1_obj.schedule) >= current_week:
                        current_matchup = team1_obj.schedule[current_week - 1]
                        print(f"DEBUG: Current matchup object: {current_matchup}")

                        if current_matchup:
                            # Debug what attributes the matchup has
                            if hasattr(current_matchup, '__dict__'):
                                print(f"DEBUG: Matchup attributes: {list(current_matchup.__dict__.keys())}")

                            if hasattr(current_matchup, 'home_team') and hasattr(current_matchup, 'away_team'):
                                team2_obj = current_matchup.away_team if current_matchup.home_team == team1_obj else current_matchup.home_team
                                print(f"DEBUG: Found opponent via schedule: {team2_obj.team_name if team2_obj else 'None'}")
            except (IndexError, AttributeError) as e:
                print(f"DEBUG: Schedule method failed: {e}")

            # Method 2: Try league scoreboard/matchups for current week
            if not team2_obj:
                try:
                    print("DEBUG: Trying league scoreboard method")
                    if hasattr(league, 'scoreboard') and callable(league.scoreboard):
                        current_scoreboard = league.scoreboard(current_week)
                        print(f"DEBUG: Got scoreboard for week {current_week}")

                        for matchup in current_scoreboard:
                            if hasattr(matchup, 'home_team') and hasattr(matchup, 'away_team'):
                                if matchup.home_team == team1_obj:
                                    team2_obj = matchup.away_team
                                    print(f"DEBUG: Found opponent via scoreboard (home): {team2_obj.team_name}")
                                    break
                                elif matchup.away_team == team1_obj:
                                    team2_obj = matchup.home_team
                                    print(f"DEBUG: Found opponent via scoreboard (away): {team2_obj.team_name}")
                                    break
                except Exception as e:
                    print(f"DEBUG: Scoreboard method failed: {e}")

            # Method 3: Alternative - check all other teams' schedules
            if not team2_obj:
                try:
                    print("DEBUG: Trying reverse lookup method")
                    for other_team in league.teams:
                        if other_team == team1_obj:
                            continue

                        if hasattr(other_team, 'schedule') and other_team.schedule:
                            if len(other_team.schedule) >= current_week:
                                other_matchup = other_team.schedule[current_week - 1]
                                if other_matchup and hasattr(other_matchup, 'home_team') and hasattr(other_matchup, 'away_team'):
                                    if other_matchup.home_team == team1_obj or other_matchup.away_team == team1_obj:
                                        team2_obj = other_team
                                        print(f"DEBUG: Found opponent via reverse lookup: {team2_obj.team_name}")
                                        break
                except Exception as e:
                    print(f"DEBUG: Reverse lookup failed: {e}")

        # If still no team2, require manual input
        if not team2_obj:
            if team2:
                team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
                if not team2_obj:
                    await interaction.followup.send(f"Team '{team2}' not found.")
                    return
            else:
                await interaction.followup.send(f"Could not find current opponent for {team1}. Please specify both teams: `/matchup {team1} TeamName`")
                return

        # Get team data and projections
        def get_team_lineup_data(team):
            starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]

            lineup_by_pos = {}
            total_projected = 0

            for player in starters:
                pos = getattr(player, 'position', 'FLEX')
                projected = get_current_week_points(player, league)

                if projected != 'N/A':
                    projected_val = float(projected)
                    total_projected += projected_val
                else:
                    projected_val = 0

                if pos not in lineup_by_pos:
                    lineup_by_pos[pos] = []

                lineup_by_pos[pos].append({
                    'name': player.name,
                    'projected': projected_val,
                    'team': getattr(player, 'proTeam', 'UNK')
                })

            return lineup_by_pos, total_projected

        team1_lineup, team1_total = get_team_lineup_data(team1_obj)
        team2_lineup, team2_total = get_team_lineup_data(team2_obj)

        # Create matchup embed
        embed = discord.Embed(title=f"⚔️ Week {current_week} Matchup Analysis", color=discord.Color.orange())

        # Team headers and totals with clearer labeling
        embed.add_field(name=f"🏈 {team1_obj.team_name}", value=f"**{team1_total:.1f} projected**", inline=True)
        embed.add_field(name="VS", value="⚔️", inline=True)
        embed.add_field(name=f"🏈 {team2_obj.team_name}", value=f"**{team2_total:.1f} projected**", inline=True)

        # Position-by-position breakdown
        all_positions = set(list(team1_lineup.keys()) + list(team2_lineup.keys()))
        position_order = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'D/ST', 'DST']

        # Sort positions by our preferred order
        sorted_positions = []
        for pos in position_order:
            if pos in all_positions:
                sorted_positions.append(pos)

        # Add any remaining positions
        for pos in all_positions:
            if pos not in sorted_positions:
                sorted_positions.append(pos)

        matchup_lines = []
        matchup_lines.append(f"{'Position':<8} {team1_obj.team_name[:12]:<12} {team2_obj.team_name[:12]:<12}")
        matchup_lines.append(f"{'-'*8} {'-'*12} {'-'*12}")

        position_advantages = {'team1': 0, 'team2': 0}

        for pos in sorted_positions:
            team1_players = team1_lineup.get(pos, [])
            team2_players = team2_lineup.get(pos, [])

            # Sort players by projection for each team
            team1_players_sorted = sorted(team1_players, key=lambda x: x['projected'], reverse=True)
            team2_players_sorted = sorted(team2_players, key=lambda x: x['projected'], reverse=True)

            # Calculate total for this position group
            team1_pos_total = sum(p['projected'] for p in team1_players)
            team2_pos_total = sum(p['projected'] for p in team2_players)

            # Track advantages based on position totals
            if team1_pos_total > team2_pos_total:
                position_advantages['team1'] += 1
            elif team2_pos_total > team1_pos_total:
                position_advantages['team2'] += 1

            # If multiple players at position, show the total and count
            if len(team1_players) > 1 or len(team2_players) > 1:
                team1_count = len(team1_players)
                team2_count = len(team2_players)
                pos_display = f"{pos}({max(team1_count, team2_count)})"

                team1_display = f"{team1_pos_total:.1f}" if team1_pos_total > 0 else "---"
                team2_display = f"{team2_pos_total:.1f}" if team2_pos_total > 0 else "---"
            else:
                pos_display = pos
                team1_display = f"{team1_pos_total:.1f}" if team1_pos_total > 0 else "---"
                team2_display = f"{team2_pos_total:.1f}" if team2_pos_total > 0 else "---"

            line = f"{pos_display:<8} {team1_display:<12} {team2_display:<12}"
            matchup_lines.append(line)

        matchup_table = f"```\n{chr(10).join(matchup_lines)}\n```"
        embed.add_field(name="📊 Position Breakdown (Projected Points)", value=matchup_table, inline=False)

        # Matchup analysis
        analysis_lines = []

        # Overall projection
        diff = abs(team1_total - team2_total)
        if team1_total > team2_total:
            analysis_lines.append(f"📈 **Projected Winner**: {team1_obj.team_name} by {diff:.1f} pts")
        elif team2_total > team1_total:
            analysis_lines.append(f"📈 **Projected Winner**: {team2_obj.team_name} by {diff:.1f} pts")
        else:
            analysis_lines.append("📈 **Projected**: Even matchup!")

        # Position advantages
        if position_advantages['team1'] > position_advantages['team2']:
            analysis_lines.append(f"🎯 **Position Edge**: {team1_obj.team_name} ({position_advantages['team1']} vs {position_advantages['team2']})")
        elif position_advantages['team2'] > position_advantages['team1']:
            analysis_lines.append(f"🎯 **Position Edge**: {team2_obj.team_name} ({position_advantages['team2']} vs {position_advantages['team1']})")
        else:
            analysis_lines.append("🎯 **Position Edge**: Even split")

        # Key players analysis
        all_team1_players = []
        all_team2_players = []

        for pos_players in team1_lineup.values():
            all_team1_players.extend(pos_players)
        for pos_players in team2_lineup.values():
            all_team2_players.extend(pos_players)

        if all_team1_players:
            team1_star = max(all_team1_players, key=lambda x: x['projected'])
            analysis_lines.append(f"⭐ **{team1_obj.team_name} Key Player**: {team1_star['name']} ({team1_star['projected']:.1f} pts)")

        if all_team2_players:
            team2_star = max(all_team2_players, key=lambda x: x['projected'])
            analysis_lines.append(f"⭐ **{team2_obj.team_name} Key Player**: {team2_star['name']} ({team2_star['projected']:.1f} pts)")

        # Closeness indicator
        if diff <= 5:
            analysis_lines.append("🔥 **Closeness**: Toss-up game! Could go either way")
        elif diff <= 15:
            analysis_lines.append("⚠️ **Closeness**: Competitive matchup")
        else:
            analysis_lines.append("💪 **Closeness**: Projected blowout")

        embed.add_field(name="🔍 Matchup Analysis", value="\n".join(analysis_lines), inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"Error analyzing matchup: {e}"
        print(f"Matchup command error: {e}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_msg)
            else:
                await interaction.response.send_message(error_msg)
        except Exception as follow_error:
            print(f"Failed to send error message: {follow_error}")

@client.tree.command(name="waiver", description="Analyze waiver wire for top pickup recommendations.")
@app_commands.describe(
    position="Filter by position (QB, RB, WR, TE, K, D/ST)",
    min_owned="Minimum ownership percentage (0-100, default: 0)",
    max_owned="Maximum ownership percentage (0-100, default: 50)"
)
async def waiver(interaction: discord.Interaction, position: str = None, min_owned: int = 0, max_owned: int = 50):
    await interaction.response.defer()

    try:
        league = League(league_id=LEAGUE_ID, year=SEASON_ID, espn_s2=ESPN_S2, swid=SWID)

        # Get all free agents
        free_agents = league.free_agents()

        if not free_agents:
            await interaction.followup.send("No free agents found in the league.", ephemeral=True)
            return

        # Filter by position if specified
        valid_positions = ['QB', 'RB', 'WR', 'TE', 'K', 'D/ST', 'DST']
        if position:
            position = position.upper()
            if position not in valid_positions:
                await interaction.followup.send(f"Invalid position. Valid options: {', '.join(valid_positions)}", ephemeral=True)
                return

            # Handle D/ST vs DST
            filter_pos = 'D/ST' if position == 'DST' else position
            free_agents = [p for p in free_agents if p.position == filter_pos]

        # Filter by ownership percentage
        filtered_agents = []
        for player in free_agents:
            ownership = getattr(player, 'percent_owned', 0)
            if min_owned <= ownership <= max_owned:
                projected = get_current_week_points(player, league)
                if projected and projected > 0:
                    filtered_agents.append({
                        'player': player,
                        'projected': projected,
                        'ownership': ownership
                    })

        if not filtered_agents:
            filter_desc = f" (position: {position})" if position else ""
            filter_desc += f" (ownership: {min_owned}-{max_owned}%)"
            await interaction.followup.send(f"No available players found with current filters{filter_desc}.", ephemeral=True)
            return

        # Sort by projected points
        filtered_agents.sort(key=lambda x: x['projected'], reverse=True)

        # Take top 15 for display
        top_pickups = filtered_agents[:15]

        # Create embed
        embed = discord.Embed(
            title="🎯 Waiver Wire Intelligence",
            description=f"Top pickup recommendations • Ownership filter: {min_owned}-{max_owned}%",
            color=0x00ff00
        )

        # Add filter info if position specified
        if position:
            embed.description += f" • Position: {position}"

        # Create pickup table
        header_line = f"{'Player':<22} {'Pos':<4} {'Proj':<6} {'Own%':<5}"
        table_lines = [header_line, "-" * len(header_line)]

        for i, pickup in enumerate(top_pickups, 1):
            player = pickup['player']
            name = player.name[:20] if len(player.name) > 20 else player.name
            pos = player.position
            proj = pickup['projected']
            own = pickup['ownership']

            line = f"{name:<22} {pos:<4} {proj:<6.1f} {own:<5.1f}"
            table_lines.append(line)

        pickup_table = f"```\n{chr(10).join(table_lines)}\n```"
        embed.add_field(name="📈 Top Available Players", value=pickup_table, inline=False)

        # Analysis section
        analysis_lines = []

        if top_pickups:
            # Best overall pickup
            best_pickup = top_pickups[0]
            analysis_lines.append(f"🔥 **Top Target**: {best_pickup['player'].name} ({best_pickup['projected']:.1f} pts, {best_pickup['ownership']:.1f}% owned)")

            # High projection, low ownership gems
            gems = [p for p in top_pickups if p['ownership'] <= 10 and p['projected'] >= 8]
            if gems:
                gem = gems[0]
                analysis_lines.append(f"💎 **Hidden Gem**: {gem['player'].name} ({gem['projected']:.1f} pts, {gem['ownership']:.1f}% owned)")

            # Position-specific advice
            if not position:
                # Count by position
                pos_counts = {}
                for pickup in top_pickups[:10]:  # Top 10 only
                    pos = pickup['player'].position
                    if pos not in pos_counts:
                        pos_counts[pos] = []
                    pos_counts[pos].append(pickup)

                # Find position with most depth
                if pos_counts:
                    deepest_pos = max(pos_counts.keys(), key=lambda p: len(pos_counts[p]))
                    if len(pos_counts[deepest_pos]) >= 3:
                        analysis_lines.append(f"🏈 **Deep Position**: {deepest_pos} has great waiver depth")

                    # Find scarcest position
                    scarcest_pos = min(pos_counts.keys(), key=lambda p: len(pos_counts[p]))
                    if len(pos_counts[scarcest_pos]) == 1:
                        analysis_lines.append(f"⚠️ **Scarce Position**: Limited {scarcest_pos} options available")

        # Ownership insights
        high_owned = [p for p in top_pickups if p['ownership'] >= 25]
        low_owned = [p for p in top_pickups if p['ownership'] <= 5]

        if high_owned:
            analysis_lines.append(f"📊 **Popular Targets**: {len(high_owned)} players with 25%+ ownership")
        if low_owned:
            analysis_lines.append(f"🎯 **Sleeper Options**: {len(low_owned)} players under 5% ownership")

        if analysis_lines:
            embed.add_field(name="🧠 Wire Intelligence", value="\n".join(analysis_lines), inline=False)

        # Add usage tip
        embed.set_footer(text="💡 Tip: Use position and ownership filters to narrow your search")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"Error analyzing waiver wire: {e}"
        print(f"Waiver error: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(error_msg, ephemeral=True)
        else:
            await interaction.followup.send(error_msg, ephemeral=True)

@client.tree.command(name="trade", description="Analyze potential trades between teams.")
@app_commands.describe(
    team1="First team name",
    team2="Second team name",
    team1_players="Players team1 gives up (comma-separated)",
    team2_players="Players team2 gives up (comma-separated)"
)
async def trade(interaction: discord.Interaction, team1: str, team2: str, team1_players: str, team2_players: str):
    await interaction.response.defer()

    try:
        league = League(league_id=LEAGUE_ID, year=SEASON_ID, espn_s2=ESPN_S2, swid=SWID)

        # Find teams
        team1_obj = None
        team2_obj = None

        for team in league.teams:
            # Try different owner attribute names
            owner_name = getattr(team, 'owner', '') or getattr(team, 'owners', '') or ''
            if isinstance(owner_name, list) and owner_name:
                owner_name = owner_name[0] if owner_name else ''

            if team1.lower() in team.team_name.lower() or (owner_name and team1.lower() in str(owner_name).lower()):
                team1_obj = team
            if team2.lower() in team.team_name.lower() or (owner_name and team2.lower() in str(owner_name).lower()):
                team2_obj = team

        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found. Available teams: {', '.join(t.team_name for t in league.teams)}", ephemeral=True)
            return

        if not team2_obj:
            await interaction.followup.send(f"Team '{team2}' not found. Available teams: {', '.join(t.team_name for t in league.teams)}", ephemeral=True)
            return

        # Parse player names
        team1_player_names = [name.strip() for name in team1_players.split(',')]
        team2_player_names = [name.strip() for name in team2_players.split(',')]

        # Find players on teams
        def find_player_on_team(player_name, team):
            for player in team.roster:
                if player_name.lower() in player.name.lower():
                    return player
            return None

        team1_trade_players = []
        team2_trade_players = []

        # Find team1 players
        for player_name in team1_player_names:
            player = find_player_on_team(player_name, team1_obj)
            if player:
                team1_trade_players.append(player)
            else:
                await interaction.followup.send(f"Player '{player_name}' not found on {team1_obj.team_name}.", ephemeral=True)
                return

        # Find team2 players
        for player_name in team2_player_names:
            player = find_player_on_team(player_name, team2_obj)
            if player:
                team2_trade_players.append(player)
            else:
                await interaction.followup.send(f"Player '{player_name}' not found on {team2_obj.team_name}.", ephemeral=True)
                return

        # Calculate trade values
        def get_player_value(player):
            projected = get_current_week_points(player, league)
            season_total = getattr(player, 'total_points', 0)
            avg_points = season_total / max(league.current_week - 1, 1) if season_total > 0 else projected if projected else 0
            return {
                'name': player.name,
                'position': player.position,
                'projected': projected if projected else 0,
                'season_total': season_total,
                'avg_points': avg_points,
                'player': player
            }

        team1_values = [get_player_value(p) for p in team1_trade_players]
        team2_values = [get_player_value(p) for p in team2_trade_players]

        # Calculate totals
        team1_proj_total = sum(p['projected'] for p in team1_values)
        team2_proj_total = sum(p['projected'] for p in team2_values)
        team1_avg_total = sum(p['avg_points'] for p in team1_values)
        team2_avg_total = sum(p['avg_points'] for p in team2_values)

        # Create embed
        embed = discord.Embed(
            title="🤝 Trade Analysis",
            description=f"{team1_obj.team_name} ↔️ {team2_obj.team_name}",
            color=0x4169E1
        )

        # Trade details
        team1_gives = []
        team2_gives = []

        for player_val in team1_values:
            team1_gives.append(f"{player_val['position']} {player_val['name']} ({player_val['projected']:.1f} pts)")

        for player_val in team2_values:
            team2_gives.append(f"{player_val['position']} {player_val['name']} ({player_val['projected']:.1f} pts)")

        embed.add_field(
            name=f"📤 {team1_obj.team_name} Gives",
            value="\n".join(team1_gives) if team1_gives else "None",
            inline=True
        )

        embed.add_field(
            name=f"📥 {team1_obj.team_name} Gets",
            value="\n".join(team2_gives) if team2_gives else "None",
            inline=True
        )

        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Spacer

        embed.add_field(
            name=f"📤 {team2_obj.team_name} Gives",
            value="\n".join(team2_gives) if team2_gives else "None",
            inline=True
        )

        embed.add_field(
            name=f"📥 {team2_obj.team_name} Gets",
            value="\n".join(team1_gives) if team1_gives else "None",
            inline=True
        )

        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Spacer

        # Trade value comparison
        value_lines = []
        value_lines.append(f"**Projected Points (This Week)**")
        value_lines.append(f"• {team1_obj.team_name}: {team1_proj_total:.1f} pts")
        value_lines.append(f"• {team2_obj.team_name}: {team2_proj_total:.1f} pts")
        proj_diff = abs(team1_proj_total - team2_proj_total)

        if team1_proj_total > team2_proj_total:
            value_lines.append(f"• **Edge**: {team1_obj.team_name} (+{proj_diff:.1f})")
        elif team2_proj_total > team1_proj_total:
            value_lines.append(f"• **Edge**: {team2_obj.team_name} (+{proj_diff:.1f})")
        else:
            value_lines.append(f"• **Edge**: Even trade")

        value_lines.append("")
        value_lines.append(f"**Season Average (Per Game)**")
        value_lines.append(f"• {team1_obj.team_name}: {team1_avg_total:.1f} pts")
        value_lines.append(f"• {team2_obj.team_name}: {team2_avg_total:.1f} pts")
        avg_diff = abs(team1_avg_total - team2_avg_total)

        if team1_avg_total > team2_avg_total:
            value_lines.append(f"• **Edge**: {team1_obj.team_name} (+{avg_diff:.1f})")
        elif team2_avg_total > team1_avg_total:
            value_lines.append(f"• **Edge**: {team2_obj.team_name} (+{avg_diff:.1f})")
        else:
            value_lines.append(f"• **Edge**: Even trade")

        embed.add_field(name="📊 Value Comparison", value="\n".join(value_lines), inline=False)

        # Position analysis
        position_analysis = []

        # Group by positions
        team1_positions = {}
        team2_positions = {}

        for player_val in team1_values:
            pos = player_val['position']
            if pos not in team1_positions:
                team1_positions[pos] = []
            team1_positions[pos].append(player_val)

        for player_val in team2_values:
            pos = player_val['position']
            if pos not in team2_positions:
                team2_positions[pos] = []
            team2_positions[pos].append(player_val)

        all_positions = set(team1_positions.keys()) | set(team2_positions.keys())

        for pos in sorted(all_positions):
            team1_pos_players = team1_positions.get(pos, [])
            team2_pos_players = team2_positions.get(pos, [])

            team1_pos_total = sum(p['projected'] for p in team1_pos_players)
            team2_pos_total = sum(p['projected'] for p in team2_pos_players)

            if team1_pos_total > 0 or team2_pos_total > 0:
                if team1_pos_total > team2_pos_total:
                    diff = team1_pos_total - team2_pos_total
                    position_analysis.append(f"**{pos}**: {team1_obj.team_name} advantage (+{diff:.1f})")
                elif team2_pos_total > team1_pos_total:
                    diff = team2_pos_total - team1_pos_total
                    position_analysis.append(f"**{pos}**: {team2_obj.team_name} advantage (+{diff:.1f})")
                else:
                    position_analysis.append(f"**{pos}**: Even swap")

        if position_analysis:
            embed.add_field(name="🎯 Position Analysis", value="\n".join(position_analysis), inline=False)

        # Trade recommendation
        recommendation_lines = []

        # Overall fairness
        total_diff = abs((team1_proj_total + team1_avg_total) - (team2_proj_total + team2_avg_total))
        if total_diff <= 2:
            recommendation_lines.append("✅ **Fairness**: Very fair trade")
        elif total_diff <= 5:
            recommendation_lines.append("⚖️ **Fairness**: Reasonably fair trade")
        elif total_diff <= 10:
            recommendation_lines.append("⚠️ **Fairness**: Slightly uneven trade")
        else:
            recommendation_lines.append("❌ **Fairness**: Significantly uneven trade")

        # Win-win analysis
        if len(set(p['position'] for p in team1_values)) != len(set(p['position'] for p in team2_values)):
            recommendation_lines.append("🔄 **Type**: Position diversification trade")
        else:
            recommendation_lines.append("🔄 **Type**: Like-for-like position trade")

        # Risk assessment
        injury_concerns = []
        for player_val in team1_values + team2_values:
            injury_status = getattr(player_val['player'], 'injuryStatus', None)
            if injury_status and injury_status not in ['ACTIVE', 'NORMAL', None]:
                injury_concerns.append(f"{player_val['name']} ({injury_status})")

        if injury_concerns:
            recommendation_lines.append(f"🏥 **Injury Risk**: {', '.join(injury_concerns)}")

        embed.add_field(name="💡 Trade Assessment", value="\n".join(recommendation_lines), inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"Error analyzing trade: {e}"
        print(f"Trade error: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(error_msg, ephemeral=True)
        else:
            await interaction.followup.send(error_msg, ephemeral=True)

@client.tree.command(name="menu", description="Interactive command menu for easy navigation.")
async def menu(interaction: discord.Interaction):
    """Main interactive menu for bot commands"""
    embed = discord.Embed(
        title="🏈 Fantasy Football Command Center",
        description="Select a category to explore available commands",
        color=0x32CD32
    )

    embed.add_field(
        name="📊 Team Analytics",
        value="• Team rosters & stats\n• Compare teams\n• Weekly matchups\n• League standings",
        inline=True
    )

    embed.add_field(
        name="🎯 Strategy Tools",
        value="• Waiver wire analysis\n• Trade analyzer\n• Sleeper picks\n• Player stats",
        inline=True
    )

    embed.add_field(
        name="📈 League Data",
        value="• Season statistics\n• Performance metrics\n• Head-to-head records",
        inline=True
    )

    view = MainMenuView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Interactive Menu Views
class MainMenuView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Team Analytics", emoji="📊", style=discord.ButtonStyle.primary, row=0)
    async def team_analytics(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📊 Team Analytics Commands",
            description="Choose a team analysis command",
            color=0x1E90FF
        )

        embed.add_field(
            name="Available Commands",
            value="• `/team [name]` - View team roster & player stats\n"
                  "• `/compare [team1] [team2]` - Compare two teams\n"
                  "• `/matchup [team1] [team2]` - Weekly matchup analysis\n"
                  "• `/standings` - League standings & records",
            inline=False
        )

        view = TeamAnalyticsView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Strategy Tools", emoji="🎯", style=discord.ButtonStyle.secondary, row=0)
    async def strategy_tools(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎯 Strategy Tools",
            description="Choose a strategy command",
            color=0xFF6347
        )

        embed.add_field(
            name="Available Commands",
            value="• `/waiver [position] [min_owned] [max_owned]` - Waiver wire analysis\n"
                  "• `/trade [team1] [team2] [players1] [players2]` - Trade analyzer\n"
                  "• `/sleeper [position] [min_proj] [max_owned]` - Find sleeper picks\n"
                  "• `/stats` - Advanced league statistics",
            inline=False
        )

        view = StrategyToolsView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="League Data", emoji="📈", style=discord.ButtonStyle.success, row=0)
    async def league_data(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📈 League Data Commands",
            description="Choose a league analysis command",
            color=0x32CD32
        )

        embed.add_field(
            name="Available Commands",
            value="• `/standings` - Current league standings\n"
                  "• `/stats` - Detailed league statistics\n"
                  "• `/compare [team1] [team2]` - Head-to-head analysis",
            inline=False
        )

        view = LeagueDataView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Back to Main", emoji="🏠", style=discord.ButtonStyle.gray, row=1)
    async def back_to_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Recreate main menu
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=0x32CD32
        )

        embed.add_field(
            name="📊 Team Analytics",
            value="• Team rosters & stats\n• Compare teams\n• Weekly matchups\n• League standings",
            inline=True
        )

        embed.add_field(
            name="🎯 Strategy Tools",
            value="• Waiver wire analysis\n• Trade analyzer\n• Sleeper picks\n• Player stats",
            inline=True
        )

        embed.add_field(
            name="📈 League Data",
            value="• Season statistics\n• Performance metrics\n• Head-to-head records",
            inline=True
        )

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

class TeamAnalyticsView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Team Roster", emoji="👥", style=discord.ButtonStyle.primary)
    async def team_roster(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👥 Team Roster Command",
            description="View detailed team roster with player stats",
            color=0x1E90FF
        )
        embed.add_field(
            name="Command",
            value="`/team [team_name]`",
            inline=False
        )
        embed.add_field(
            name="Example",
            value="`/team Swift Nation`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Starting lineup with projected points\n• Bench players\n• Player positions and injury status\n• Interactive buttons for filtering",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("team"))

    @discord.ui.button(label="Compare Teams", emoji="⚖️", style=discord.ButtonStyle.primary)
    async def compare_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚖️ Compare Teams Command",
            description="Comprehensive team comparison analysis",
            color=0x1E90FF
        )
        embed.add_field(
            name="Command",
            value="`/compare [team1] [team2]`",
            inline=False
        )
        embed.add_field(
            name="Example",
            value="`/compare \"Swift Nation\" \"Team SoloMid\"`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Season records and standings\n• Total points comparison\n• Head-to-head history\n• Weekly projections",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("team"))

    @discord.ui.button(label="Weekly Matchup", emoji="🏆", style=discord.ButtonStyle.primary)
    async def weekly_matchup(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏆 Weekly Matchup Command",
            description="Detailed current week matchup analysis",
            color=0x1E90FF
        )
        embed.add_field(
            name="Command",
            value="`/matchup [team1] [team2]` (team2 optional)",
            inline=False
        )
        embed.add_field(
            name="Example",
            value="`/matchup \"Swift Nation\"` (auto-finds opponent)",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Position-by-position breakdown\n• Projected winner\n• Key players for each team\n• Matchup competitiveness",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("team"))

    @discord.ui.button(label="League Standings", emoji="🏅", style=discord.ButtonStyle.primary)
    async def league_standings(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏅 League Standings Command",
            description="Current league standings and team records",
            color=0x1E90FF
        )
        embed.add_field(
            name="Command",
            value="`/standings`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Team rankings and records\n• Points for/against\n• Highest/lowest scoring teams\n• Best weekly performances",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("team"))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.gray, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainMenuView()
        await view.back_to_main(interaction, button)

class StrategyToolsView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Waiver Wire", emoji="🎯", style=discord.ButtonStyle.secondary)
    async def waiver_wire(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎯 Waiver Wire Command",
            description="Analyze available free agents for pickup opportunities",
            color=0xFF6347
        )
        embed.add_field(
            name="Command",
            value="`/waiver [position] [min_owned] [max_owned]`",
            inline=False
        )
        embed.add_field(
            name="Example",
            value="`/waiver RB 0 25` (RBs owned by 0-25% of leagues)",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Top available players by projection\n• Hidden gems (low ownership, high points)\n• Position depth analysis\n• Ownership insights",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("strategy"))

    @discord.ui.button(label="Trade Analyzer", emoji="🤝", style=discord.ButtonStyle.secondary)
    async def trade_analyzer(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🤝 Trade Analyzer Command",
            description="Comprehensive analysis of potential trades",
            color=0xFF6347
        )
        embed.add_field(
            name="Command",
            value="`/trade [team1] [team2] [team1_players] [team2_players]`",
            inline=False
        )
        embed.add_field(
            name="Example",
            value="`/trade \"Swift Nation\" \"Team SoloMid\" \"Lamar Jackson\" \"Josh Allen\"`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Projected points comparison\n• Season average analysis\n• Trade fairness assessment\n• Position analysis\n• Injury risk evaluation",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("strategy"))

    @discord.ui.button(label="Sleeper Picks", emoji="😴", style=discord.ButtonStyle.secondary)
    async def sleeper_picks(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="😴 Sleeper Picks Command",
            description="Find undervalued players with upside potential",
            color=0xFF6347
        )
        embed.add_field(
            name="Command",
            value="`/sleeper [position] [min_projection] [max_owned]`",
            inline=False
        )
        embed.add_field(
            name="Example",
            value="`/sleeper WR 8 15` (WRs with 8+ pts, <15% owned)",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• High-upside, low-owned players\n• Breakout candidate analysis\n• Value vs. ownership comparison\n• Position-specific sleepers",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("strategy"))

    @discord.ui.button(label="League Stats", emoji="📊", style=discord.ButtonStyle.secondary)
    async def league_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📊 League Statistics Command",
            description="Advanced statistical analysis of league performance",
            color=0xFF6347
        )
        embed.add_field(
            name="Command",
            value="`/stats`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Scoring consistency analysis\n• Weekly high/low performers\n• Luck vs. skill metrics\n• Team efficiency ratings",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("strategy"))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.gray, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainMenuView()
        await view.back_to_main(interaction, button)

class LeagueDataView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Standings", emoji="🏅", style=discord.ButtonStyle.success)
    async def standings(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏅 League Standings",
            description="Current league standings and records",
            color=0x32CD32
        )
        embed.add_field(
            name="Command",
            value="`/standings`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Team rankings by record\n• Points for and against\n• Playoff positioning\n• Season highlights",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("league"))

    @discord.ui.button(label="Statistics", emoji="📈", style=discord.ButtonStyle.success)
    async def statistics(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📈 League Statistics",
            description="Detailed performance analytics",
            color=0x32CD32
        )
        embed.add_field(
            name="Command",
            value="`/stats`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Consistency rankings\n• Weekly extremes\n• Efficiency metrics\n• Statistical insights",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("league"))

    @discord.ui.button(label="Team Comparison", emoji="⚖️", style=discord.ButtonStyle.success)
    async def team_comparison(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚖️ Team Comparison",
            description="Head-to-head team analysis",
            color=0x32CD32
        )
        embed.add_field(
            name="Command",
            value="`/compare [team1] [team2]`",
            inline=False
        )
        embed.add_field(
            name="What it shows",
            value="• Season performance comparison\n• Head-to-head records\n• Strength analysis\n• Projection differences",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=BackToMenuView("league"))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.gray, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainMenuView()
        await view.back_to_main(interaction, button)

class BackToMenuView(View):
    def __init__(self, menu_type):
        super().__init__(timeout=300)
        self.menu_type = menu_type

    @discord.ui.button(label="Back to Category", emoji="⬅️", style=discord.ButtonStyle.gray)
    async def back_to_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.menu_type == "team":
            embed = discord.Embed(
                title="📊 Team Analytics Commands",
                description="Choose a team analysis command",
                color=0x1E90FF
            )

            embed.add_field(
                name="Available Commands",
                value="• `/team [name]` - View team roster & player stats\n"
                      "• `/compare [team1] [team2]` - Compare two teams\n"
                      "• `/matchup [team1] [team2]` - Weekly matchup analysis\n"
                      "• `/standings` - League standings & records",
                inline=False
            )

            view = TeamAnalyticsView()
            await interaction.response.edit_message(embed=embed, view=view)
        elif self.menu_type == "strategy":
            embed = discord.Embed(
                title="🎯 Strategy Tools",
                description="Choose a strategy command",
                color=0xFF6347
            )

            embed.add_field(
                name="Available Commands",
                value="• `/waiver [position] [min_owned] [max_owned]` - Waiver wire analysis\n"
                      "• `/trade [team1] [team2] [players1] [players2]` - Trade analyzer\n"
                      "• `/sleeper [position] [min_proj] [max_owned]` - Find sleeper picks\n"
                      "• `/stats` - Advanced league statistics",
                inline=False
            )

            view = StrategyToolsView()
            await interaction.response.edit_message(embed=embed, view=view)
        elif self.menu_type == "league":
            embed = discord.Embed(
                title="📈 League Data Commands",
                description="Choose a league analysis command",
                color=0x32CD32
            )

            embed.add_field(
                name="Available Commands",
                value="• `/standings` - Current league standings\n"
                      "• `/stats` - Detailed league statistics\n"
                      "• `/compare [team1] [team2]` - Head-to-head analysis",
                inline=False
            )

            view = LeagueDataView()
            await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.primary)
    async def back_to_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Recreate main menu
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=0x32CD32
        )

        embed.add_field(
            name="📊 Team Analytics",
            value="• Team rosters & stats\n• Compare teams\n• Weekly matchups\n• League standings",
            inline=True
        )

        embed.add_field(
            name="🎯 Strategy Tools",
            value="• Waiver wire analysis\n• Trade analyzer\n• Sleeper picks\n• Player stats",
            inline=True
        )

        embed.add_field(
            name="📈 League Data",
            value="• Season statistics\n• Performance metrics\n• Head-to-head records",
            inline=True
        )

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

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
