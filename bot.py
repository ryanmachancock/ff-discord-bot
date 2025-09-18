print("Starting Fantasy Football bot...")

import os
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Select
from dotenv import load_dotenv
from espn_api.football import League

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
        await interaction.response.defer()
        if SWID and ESPN_S2:
            league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
        else:
            league = League(league_id=LEAGUE_ID, year=SEASON_ID)
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
        # ESPN lineup slot order for sorting
        slot_order = {
            'QB': 0, 'RB': 1, 'RB2': 2, 'WR': 3, 'WR2': 4, 'TE': 5, 'FLEX': 6, 'D/ST': 7, 'DST': 7, 'K': 8
        }
        flex_names = {'RB/WR/TE', 'WR/RB', 'WR/TE', 'RB/WR'}
        def get_points(player):
            return (
                getattr(player, 'points', None)
                or getattr(player, 'total_points', None)
                or getattr(player, 'score', None)
                or 'N/A'
            )
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        def get_status(player):
            status = getattr(player, 'injuryStatus', None)
            return status_emoji.get(status, status_emoji.get('NORMAL', '')) + (f' {status}' if status else '')
        def get_pos_emoji(player):
            return pos_emoji.get(player.position, '')
        def player_line(player):
            name = f"{player.name[:16]:16}"
            actual = str(get_points(player))[:6]
            proj = str(get_proj(player))[:6]
            return f"{get_pos_emoji(player)} {name} ··· {get_status(player)} ··· {actual} / {proj} pts"
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
        starters_text = f"""```
{chr(10).join(player_line(p) for p in starters_sorted)}
────────────────────────────────────────────
Total Starter Points: {total_starter_points:.1f}
```""" if starters_sorted else "None"
        bench_text = f"""```
{chr(10).join(player_line(p) for p in bench)}
```""" if bench else "None"
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
        await interaction.followup.send(f"Error fetching team: {e}")

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
            return (
                getattr(player, 'points', None)
                or getattr(player, 'total_points', None)
                or getattr(player, 'score', None)
                or 'N/A'
            )
        
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
        embed.add_field(name="This Week", value=f"{actual_points} pts (proj: {proj_points})", inline=True)
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
        
        # Get starter points for both teams
        def get_points(player):
            return (
                getattr(player, 'points', None)
                or getattr(player, 'total_points', None)
                or getattr(player, 'score', None)
                or 'N/A'
            )
        
        team1_starters = [p for p in team1_obj.roster if getattr(p, 'lineupSlot', None) != "BE"]
        team2_starters = [p for p in team2_obj.roster if getattr(p, 'lineupSlot', None) != "BE"]
        
        team1_total = sum(float(get_points(p)) for p in team1_starters if get_points(p) != 'N/A')
        team2_total = sum(float(get_points(p)) for p in team2_starters if get_points(p) != 'N/A')
        
        # Create comparison embed
        current_week = getattr(league, 'current_week', 'Unknown')
        embed = discord.Embed(title=f"⚔️ Team Comparison - Week {current_week}", color=discord.Color.purple())
        
        embed.add_field(name=f"🏈 {team1_obj.team_name}", value=f"**{team1_total:.1f} pts**", inline=True)
        embed.add_field(name="VS", value="⚔️", inline=True)
        embed.add_field(name=f"🏈 {team2_obj.team_name}", value=f"**{team2_total:.1f} pts**", inline=True)
        
        # Show winner
        if team1_total > team2_total:
            winner = f"🏆 {team1_obj.team_name} leads by {team1_total - team2_total:.1f} pts"
        elif team2_total > team1_total:
            winner = f"🏆 {team2_obj.team_name} leads by {team2_total - team1_total:.1f} pts"
        else:
            winner = "🤝 It's a tie!"
        
        embed.add_field(name="Current Leader", value=winner, inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error comparing teams: {e}")

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
            return (
                getattr(player, 'points', None)
                or getattr(player, 'total_points', None)
                or getattr(player, 'score', None)
                or 'N/A'
            )
        
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        
        def player_line(player):
            name = f"{player.name[:16]:16}"
            actual = str(get_points(player))[:6]
            proj = str(get_proj(player))[:6]
            return f"{player.name} ··· {actual} / {proj} pts"
        
        players_text = f"""```
{chr(10).join(player_line(p) for p in filtered_players)}
```"""
        
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
        
        def get_points(player):
            return (
                getattr(player, 'points', None)
                or getattr(player, 'total_points', None)
                or getattr(player, 'score', None)
                or 'N/A'
            )
        
        def get_proj(player):
            return (
                getattr(player, 'projected_points', None)
                or getattr(player, 'projected_total_points', None)
                or getattr(player, 'proj_score', None)
                or 'N/A'
            )
        
        def get_status(player):
            status = getattr(player, 'injuryStatus', None)
            return status_emoji.get(status, status_emoji.get('NORMAL', '')) + (f' {status}' if status else '')
        
        def get_pos_emoji(player):
            return pos_emoji.get(player.position, '')
        
        def player_line(player):
            name = f"{player.name[:16]:16}"
            actual = str(get_points(player))[:6]
            proj = str(get_proj(player))[:6]
            return f"{get_pos_emoji(player)} {name} ··· {get_status(player)} ··· {actual} / {proj} pts"
        
        starters = [p for p in self.view.team.roster if getattr(p, 'lineupSlot', None) != "BE"]
        bench = [p for p in self.view.team.roster if getattr(p, 'lineupSlot', None) == "BE"]
        
        total_starter_points = sum(float(get_points(p)) for p in starters if get_points(p) != 'N/A')
        
        starters_text = f"""```
{chr(10).join(player_line(p) for p in starters)}
────────────────────────────────────────────
Total Starter Points: {total_starter_points:.1f}
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
            return (
                getattr(player, 'points', None)
                or getattr(player, 'total_points', None)
                or getattr(player, 'score', None)
                or 'N/A'
            )
        
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
        embed.add_field(name="This Week", value=f"{actual_points} pts (proj: {proj_points})", inline=True)
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
