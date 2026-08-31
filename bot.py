print("Starting Fantasy Football bot...")

import os
import asyncio
import statistics
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View
from dotenv import load_dotenv
from espn_api.football import League
from tabulate import tabulate
import json
import time
from typing import Dict, Any, Optional
from image_render import render_team_card, render_standings_card, render_matchup_card, render_scoreboard_card, render_player_card, render_player_list_card, render_compare_card, render_trade_card, render_stat_tiles_card, render_power_rankings_card, render_league_pulse_card, render_league_info_card
from image_cache import get_images, get_logos_by_url

# Shared visual style so every command's embeds look like one bot instead of
# ~15 unrelated ad-hoc colors. BRAND = normal content, the rest are for
# state-confirmation moments only.
EMBED_COLOR_BRAND = 0x2C7DFA
EMBED_COLOR_SUCCESS = 0x2ECC71
EMBED_COLOR_WARNING = 0xF1C40F
EMBED_COLOR_ERROR = 0xE74C3C

POSITION_EMOJI = {
    'QB': '🏈', 'RB': '🏃', 'WR': '🏃', 'TE': '🧩', 'K': '🦶',
    'D/ST': '🛡️', 'DST': '🛡️', 'DEF': '🛡️', 'BE': '🪑', 'Bench': '🪑', 'IR': '🏥'
}
STATUS_EMOJI = {
    'ACTIVE': '✅', 'QUESTIONABLE': '⚠️', 'OUT': '❌', 'DOUBTFUL': '🔶',
    'INJURY_RESERVE': '🏥', 'NORMAL': '🔵', None: ''
}
STATUS_ABBREV = {
    'ACTIVE': 'A', 'QUESTIONABLE': 'Q', 'OUT': 'O', 'DOUBTFUL': 'D',
    'INJURY_RESERVE': 'IR', 'NORMAL': 'N', None: ''
}

class ESPNCache:
    """Simple memory cache for ESPN API data with TTL"""

    def __init__(self, ttl_seconds: int = 300):  # 5 minute default TTL
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.ttl = ttl_seconds

    def _is_expired(self, timestamp: float) -> bool:
        return time.time() - timestamp > self.ttl

    def get(self, key: str) -> Optional[Any]:
        """Get cached data if not expired"""
        if key in self.cache:
            entry = self.cache[key]
            if not self._is_expired(entry['timestamp']):
                return entry['data']
            else:
                # Remove expired entry
                del self.cache[key]
        return None

    def set(self, key: str, data: Any):
        """Store data in cache with current timestamp"""
        self.cache[key] = {
            'data': data,
            'timestamp': time.time()
        }

    def clear(self):
        """Clear all cached data"""
        self.cache.clear()

    def get_stats(self):
        """Get cache statistics"""
        total_entries = len(self.cache)
        expired_entries = sum(1 for entry in self.cache.values()
                            if self._is_expired(entry['timestamp']))
        return {
            'total': total_entries,
            'expired': expired_entries,
            'active': total_entries - expired_entries
        }

# Global cache instance
espn_cache = ESPNCache(ttl_seconds=300)  # 5 minute cache

class BackgroundRefreshManager:
    """Manages background refresh of ESPN data to reduce API call latency"""

    def __init__(self):
        self.refresh_task = None
        self.is_running = False

    def start_background_refresh(self):
        """Start the background refresh task"""
        if not self.is_running:
            self.refresh_task = asyncio.create_task(self._refresh_loop())
            self.is_running = True
            print("Background refresh started")

    def stop_background_refresh(self):
        """Stop the background refresh task"""
        if self.refresh_task:
            self.refresh_task.cancel()
            self.is_running = False
            print("Background refresh stopped")

    async def _refresh_loop(self):
        """Main refresh loop that runs every 3 minutes"""
        while True:
            try:
                await asyncio.sleep(180)  # 3 minutes
                await self._refresh_common_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Background refresh error: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retry

    async def _refresh_common_data(self):
        """Refresh commonly accessed data"""
        try:
            # Get all registered leagues and refresh their data
            all_leagues = league_manager.data.get('leagues', {})
            refreshed_count = 0

            for league_key, league_info in all_leagues.items():
                try:
                    # Create cache key
                    user_id = league_info.get('owner_id')
                    cache_key = f"league_{user_id}_default"

                    # Check if this league data is in cache and close to expiring
                    cached_data = espn_cache.get(cache_key)
                    if cached_data:
                        continue  # Skip if still fresh

                    # Refresh this league's data
                    if league_info.get('swid') and league_info.get('espn_s2'):
                        league = League(
                            league_id=league_info['league_id'],
                            year=league_info['year'],
                            swid=league_info['swid'],
                            espn_s2=league_info['espn_s2']
                        )
                    else:
                        league = League(
                            league_id=league_info['league_id'],
                            year=league_info['year']
                        )

                    # Trigger data loading
                    _ = league.teams  # This loads the team data

                    # Cache the refreshed data
                    espn_cache.set(cache_key, league)
                    refreshed_count += 1

                    # Add small delay to avoid overwhelming ESPN
                    await asyncio.sleep(2)

                except Exception as e:
                    print(f"Failed to refresh league {league_key}: {e}")
                    continue

            # Also refresh default league if configured
            if 'LEAGUE_ID' in globals() and LEAGUE_ID:
                try:
                    default_cache_key = f"default_league_{LEAGUE_ID}_{SEASON_ID}"
                    if not espn_cache.get(default_cache_key):
                        if SWID and ESPN_S2:
                            default_league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
                        else:
                            default_league = League(league_id=LEAGUE_ID, year=SEASON_ID)
                        _ = default_league.teams
                        espn_cache.set(default_cache_key, default_league)
                        refreshed_count += 1
                except Exception as e:
                    print(f"Failed to refresh default league: {e}")

            if refreshed_count > 0:
                print(f"Background refresh completed: {refreshed_count} leagues updated")

        except Exception as e:
            print(f"Background refresh error: {e}")

# Global background refresh manager
background_refresh_manager = BackgroundRefreshManager()

async def team_name_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete function for team names"""
    try:
        print(f"Team autocomplete called with input: '{current}'")  # Debug log

        # Try to get league (use cache if available)
        league = get_league(user_id=interaction.user.id)
        if not league:
            print("No league found for autocomplete")
            return []

        # Filter teams based on current input
        teams = league.teams
        print(f"Found {len(teams)} teams in league")

        if not current:
            # Return first 25 teams if no input
            choices = [app_commands.Choice(name=team.team_name, value=team.team_name)
                      for team in teams[:25]]
            print(f"Returning {len(choices)} teams for empty input")
            return choices

        # Fuzzy matching - prioritize starts with, then contains
        current_lower = current.lower()

        starts_with = [team for team in teams if team.team_name.lower().startswith(current_lower)]
        contains = [team for team in teams if current_lower in team.team_name.lower()
                   and team not in starts_with]

        # Combine and limit to 25 (Discord limit)
        filtered_teams = (starts_with + contains)[:25]

        choices = [app_commands.Choice(name=team.team_name, value=team.team_name)
                  for team in filtered_teams]
        print(f"Returning {len(choices)} filtered teams")
        return choices

    except Exception as e:
        print(f"Team autocomplete error: {e}")
        import traceback
        traceback.print_exc()
        return []

async def player_name_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete function for player names"""
    try:
        print(f"Player autocomplete called with input: '{current}'")  # Debug log

        # Try to get league (use cache if available)
        league = get_league(user_id=interaction.user.id)
        if not league:
            print("No league found for player autocomplete")
            return []

        # Collect all players from all teams
        all_players = []
        for team in league.teams:
            all_players.extend(team.roster)

        print(f"Found {len(all_players)} total players")

        if not current:
            # Return first 25 players if no input (prioritize common positions)
            priority_positions = ['QB', 'RB', 'WR', 'TE']
            priority_players = [p for p in all_players if getattr(p, 'position', '') in priority_positions]
            choices = [app_commands.Choice(name=f"{p.name} ({getattr(p, 'position', 'UNK')})", value=p.name)
                      for p in priority_players[:25]]
            print(f"Returning {len(choices)} priority players for empty input")
            return choices

        # Fuzzy matching for player names
        current_lower = current.lower()

        starts_with = [p for p in all_players if p.name.lower().startswith(current_lower)]
        contains = [p for p in all_players if current_lower in p.name.lower()
                   and p not in starts_with]

        # Combine and limit to 25
        filtered_players = (starts_with + contains)[:25]

        choices = [app_commands.Choice(name=f"{p.name} ({getattr(p, 'position', 'UNK')})", value=p.name)
                  for p in filtered_players]
        print(f"Returning {len(choices)} filtered players")
        return choices

    except Exception as e:
        print(f"Player autocomplete error: {e}")
        import traceback
        traceback.print_exc()
        return []

class SafeEmbedBuilder:
    """Discord embed builder with automatic character limit validation"""

    def __init__(self):
        self.title = None
        self.description = None
        self.fields = []
        self.footer = None
        self.color = EMBED_COLOR_BRAND
        self.thumbnail = None
        self._total_chars = 0

    @staticmethod
    def create():
        return SafeEmbedBuilder()

    def _update_char_count(self):
        """Calculate total character count across all embed elements"""
        self._total_chars = 0
        if self.title:
            self._total_chars += len(self.title)
        if self.description:
            self._total_chars += len(self.description)
        for field in self.fields:
            self._total_chars += len(field['name']) + len(field['value'])
        if self.footer:
            self._total_chars += len(self.footer)

    def set_title(self, title):
        """Set embed title with 256 character limit"""
        if len(title) > 256:
            title = title[:253] + "..."
        self.title = title
        self._update_char_count()
        return self

    def set_description(self, description):
        """Set embed description with 4096 character limit"""
        if len(description) > 4096:
            description = description[:4093] + "..."
        self.description = description
        self._update_char_count()
        if self._total_chars > 6000:
            # Truncate description to stay under total limit
            excess = self._total_chars - 6000
            new_desc_length = len(description) - excess - 3
            if new_desc_length > 0:
                self.description = description[:new_desc_length] + "..."
        return self

    def add_field(self, name, value, inline=False):
        """Add field with validation"""
        if len(self.fields) >= 25:
            return self  # Skip if at field limit

        # Truncate field name if too long
        if len(name) > 256:
            name = name[:253] + "..."

        # Truncate field value if too long
        if len(value) > 1024:
            value = value[:1021] + "..."

        field = {'name': name, 'value': value, 'inline': inline}
        self.fields.append(field)

        self._update_char_count()

        # If total exceeds limit, remove this field
        if self._total_chars > 6000:
            self.fields.pop()
            self._update_char_count()

        return self

    def set_footer(self, text):
        """Set footer with 2048 character limit"""
        if len(text) > 2048:
            text = text[:2045] + "..."
        self.footer = text
        self._update_char_count()
        return self

    def set_color(self, color):
        """Set embed color - accepts int or discord.Color"""
        # Convert discord.Color to int if needed
        if hasattr(color, 'value'):
            self.color = color.value
        else:
            self.color = color
        return self

    def set_thumbnail(self, url):
        """Set thumbnail URL"""
        self.thumbnail = url
        return self

    def build(self):
        """Build the final Discord embed"""
        embed_dict = {'color': self.color}

        if self.title:
            embed_dict['title'] = self.title
        if self.description:
            embed_dict['description'] = self.description
        if self.fields:
            embed_dict['fields'] = self.fields
        if self.footer:
            embed_dict['footer'] = {'text': self.footer}
        if self.thumbnail:
            embed_dict['thumbnail'] = {'url': self.thumbnail}

        return discord.Embed.from_dict(embed_dict)

class LeagueManager:
    def __init__(self):
        self.data_file = 'user_leagues.json'
        self.load_data()

    def load_data(self):
        """Load user league data from JSON file"""
        try:
            with open(self.data_file, 'r') as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {"users": {}, "leagues": {}}
            self.save_data()

    def save_data(self):
        """Save user league data to JSON file"""
        with open(self.data_file, 'w') as f:
            json.dump(self.data, f, indent=2)

    def register_league(self, user_id, league_name, league_id, swid=None, espn_s2=None):
        """Register a new league for a user"""
        user_id = str(user_id)

        # Test the league connection first
        try:
            # Use the same season as the default league
            season_year = SEASON_ID if 'SEASON_ID' in globals() else 2024

            if swid and espn_s2:
                test_league = League(league_id=league_id, year=season_year, swid=swid, espn_s2=espn_s2)
            else:
                test_league = League(league_id=league_id, year=season_year)

            # Try to access basic league info to verify it works
            teams = test_league.teams
            if not teams:
                raise ValueError("League has no teams")

            # Try to get league name, fallback to provided name
            try:
                league_name_from_api = test_league.name if hasattr(test_league, 'name') and test_league.name else league_name
            except:
                league_name_from_api = league_name

        except Exception as e:
            raise ValueError(f"Unable to connect to league: {str(e)}")

        # Store league info
        league_key = f"{league_id}_{user_id}"
        self.data['leagues'][league_key] = {
            'name': league_name_from_api or league_name,
            'league_id': league_id,
            'owner_id': user_id,
            'swid': swid,
            'espn_s2': espn_s2,
            'year': season_year
        }

        # Add to user's leagues
        if user_id not in self.data['users']:
            self.data['users'][user_id] = {
                'leagues': [],
                'default_league': None
            }

        if league_key not in self.data['users'][user_id]['leagues']:
            self.data['users'][user_id]['leagues'].append(league_key)

        # Set as default if it's the user's first league
        if not self.data['users'][user_id]['default_league']:
            self.data['users'][user_id]['default_league'] = league_key

        self.save_data()
        return league_key

    def get_user_leagues(self, user_id):
        """Get all leagues for a user"""
        user_id = str(user_id)
        if user_id not in self.data['users']:
            return []

        leagues = []
        for league_key in self.data['users'][user_id]['leagues']:
            if league_key in self.data['leagues']:
                leagues.append(self.data['leagues'][league_key])
        return leagues

    def get_league_connection(self, user_id, league_key=None):
        """Get a League object for the user's default or specified league"""
        user_id = str(user_id)

        if not league_key:
            # Use default league
            if user_id not in self.data['users'] or not self.data['users'][user_id]['default_league']:
                return None
            league_key = self.data['users'][user_id]['default_league']

        if league_key not in self.data['leagues']:
            return None

        league_info = self.data['leagues'][league_key]

        try:
            if league_info['swid'] and league_info['espn_s2']:
                return League(
                    league_id=league_info['league_id'],
                    year=league_info['year'],
                    swid=league_info['swid'],
                    espn_s2=league_info['espn_s2']
                )
            else:
                return League(
                    league_id=league_info['league_id'],
                    year=league_info['year']
                )
        except Exception:
            return None

    def set_default_league(self, user_id, league_key):
        """Set a user's default league"""
        user_id = str(user_id)
        if (user_id in self.data['users'] and
            league_key in self.data['users'][user_id]['leagues'] and
            league_key in self.data['leagues']):
            self.data['users'][user_id]['default_league'] = league_key
            self.save_data()
            return True
        return False

    def remove_league(self, user_id, league_key):
        """Remove a league from a user's list"""
        user_id = str(user_id)
        if (user_id in self.data['users'] and
            league_key in self.data['users'][user_id]['leagues']):
            self.data['users'][user_id]['leagues'].remove(league_key)

            # If this was the default league, clear it
            if self.data['users'][user_id]['default_league'] == league_key:
                remaining_leagues = self.data['users'][user_id]['leagues']
                self.data['users'][user_id]['default_league'] = remaining_leagues[0] if remaining_leagues else None

            # Remove from leagues dict if user was the owner
            if league_key in self.data['leagues'] and self.data['leagues'][league_key]['owner_id'] == user_id:
                del self.data['leagues'][league_key]

            self.save_data()
            return True
        return False

    def get_all_leagues(self):
        """Get all leagues available to everyone"""
        leagues = []
        for league_key, league_info in self.data['leagues'].items():
            leagues.append({
                'key': league_key,
                'name': league_info['name'],
                'league_id': league_info['league_id'],
                'owner_id': league_info['owner_id'],
                'year': league_info['year']
            })
        return leagues

    def get_league_by_key(self, league_key):
        """Get a League object by league key"""
        if league_key not in self.data['leagues']:
            return None

        league_info = self.data['leagues'][league_key]

        try:
            if league_info['swid'] and league_info['espn_s2']:
                return League(
                    league_id=league_info['league_id'],
                    year=league_info['year'],
                    swid=league_info['swid'],
                    espn_s2=league_info['espn_s2']
                )
            else:
                return League(
                    league_id=league_info['league_id'],
                    year=league_info['year']
                )
        except Exception:
            return None

    def find_leagues_by_name(self, league_name):
        """Find leagues that match a name pattern"""
        matches = []
        search_name = league_name.lower().strip()

        for league_key, league_info in self.data['leagues'].items():
            league_actual_name = league_info['name'].lower().strip()

            # Exact match first
            if search_name == league_actual_name:
                matches.insert(0, {
                    'key': league_key,
                    'name': league_info['name'],
                    'league_id': league_info['league_id'],
                    'owner_id': league_info['owner_id'],
                    'year': league_info['year']
                })
            # Partial match
            elif search_name in league_actual_name or league_actual_name in search_name:
                matches.append({
                    'key': league_key,
                    'name': league_info['name'],
                    'league_id': league_info['league_id'],
                    'owner_id': league_info['owner_id'],
                    'year': league_info['year']
                })

        return matches

# Initialize league manager
league_manager = LeagueManager()

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
            if actual_points is not None:
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

# Discord and API Constants
DISCORD_EMBED_FIELD_LIMIT = 25  # Discord's limit for embed fields
DISCORD_EMBED_CHAR_LIMIT = 1024  # Discord's character limit per embed field
DISCORD_MESSAGE_CHAR_LIMIT = 2000  # Discord's character limit per message
SCOREBOARD_CHAR_LIMIT = 1800  # Character limit for scoreboard embeds
AUTO_REFRESH_INTERVAL = 30  # Seconds between auto-refresh updates

# Error handling utilities
async def safe_interaction_response(interaction, content, ephemeral=False, embed=None, embeds=None, view=None):
    """Safely send interaction response with timeout handling"""
    try:
        # Prepare kwargs
        kwargs = {'ephemeral': ephemeral}
        if view is not None:
            kwargs['view'] = view
        if embed:
            kwargs['embed'] = embed
        if embeds:
            kwargs['embeds'] = embeds

        if not interaction.response.is_done():
            if embed or embeds:
                await interaction.response.send_message(content=content, **kwargs)
            else:
                await interaction.response.send_message(content, **kwargs)
        else:
            if embed or embeds:
                await interaction.followup.send(content=content, **kwargs)
            else:
                await interaction.followup.send(content, **kwargs)
    except discord.errors.NotFound:
        # Interaction expired - log and continue gracefully
        print(f"Interaction expired for user {interaction.user.id}: {content[:50]}...")
        return False
    except discord.errors.HTTPException as e:
        print(f"HTTP error in interaction: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error in interaction response: {e}")
        return False
    return True

async def safe_defer(interaction, ephemeral=False):
    """Safely defer interaction with timeout handling"""
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
            return True
    except discord.errors.NotFound:
        print(f"Interaction expired during defer for user {interaction.user.id}")
        return False
    except Exception as e:
        print(f"Error deferring interaction: {e}")
        return False
    return True
MAX_PLAYERS_DISPLAY = 20  # Maximum players to show in lists
API_RETRY_ATTEMPTS = 3  # Number of retry attempts for API calls
API_RETRY_DELAY = 2  # Seconds to wait between API retry attempts

# Validate required environment variables
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required")
if not LEAGUE_ID:
    raise ValueError("ESPN_LEAGUE_ID environment variable is required")
if not SEASON_ID:
    raise ValueError("ESPN_SEASON_ID environment variable is required")

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print("------")

        # Debug: Print all registered commands before syncing
        commands = [cmd.name for cmd in self.tree.get_commands()]
        print(f"DEBUG: Found {len(commands)} commands to sync: {commands}")

        try:
            synced = await self.tree.sync()
            print(f"Successfully synced {len(synced)} commands")
            for cmd in synced:
                print(f"  - {cmd.name}: {cmd.description}")
        except Exception as e:
            print(f"Failed to sync commands: {e}")
            import traceback
            traceback.print_exc()

    async def on_error(self, event, *args, **kwargs):
        """Global error handler to prevent bot crashes"""
        import traceback
        print(f"Discord.py error in {event}:")
        traceback.print_exc()
        # Bot continues running instead of crashing

    async def on_app_command_error(self, interaction: discord.Interaction, error):
        """Handle application command errors gracefully"""
        error_msg = f"❌ Command error: {str(error)}"
        print(f"App command error: {error}")

        # Try to respond to the user
        await safe_interaction_response(interaction, error_msg, ephemeral=True)

intents = discord.Intents.default()
client = MyClient(intents=intents)

def get_league_name(user_id=None):
    """Get the league name for a user"""
    if user_id:
        user_data = league_manager.data['users'].get(str(user_id), {})
        default_league_key = user_data.get('default_league')
        if default_league_key and default_league_key in league_manager.data['leagues']:
            return league_manager.data['leagues'][default_league_key]['name']
    # Fallback to default
    return "Fantasy League"

def get_league(user_id=None, league_key=None, timeout_retries=API_RETRY_ATTEMPTS):
    """Initialize and return league instance with caching and timeout handling"""
    import time

    # Create cache key
    if user_id:
        cache_key = f"league_{user_id}_{league_key or 'default'}"
    else:
        cache_key = f"default_league_{LEAGUE_ID}_{SEASON_ID}"

    # Try cache first
    cached_league = espn_cache.get(cache_key)
    if cached_league:
        return cached_league

    # Cache miss - fetch from ESPN API
    league = None

    # If user_id is provided, try to get their league
    if user_id:
        user_league = league_manager.get_league_connection(user_id, league_key)
        if user_league:
            league = user_league

    # Fallback to original default league if no user league found
    if not league:
        for attempt in range(timeout_retries):
            try:
                if SWID and ESPN_S2:
                    league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
                else:
                    league = League(league_id=LEAGUE_ID, year=SEASON_ID)

                # Test the connection with a simple call
                _ = league.teams  # This will trigger an API call
                break

            except Exception as e:
                if attempt < timeout_retries - 1:
                    print(f"League initialization attempt {attempt + 1} failed: {e}. Retrying in {API_RETRY_DELAY} seconds...")
                    time.sleep(API_RETRY_DELAY)
                    continue
                else:
                    print(f"Failed to initialize league after {timeout_retries} attempts: {e}")
                    raise ConnectionError(f"Unable to connect to ESPN Fantasy API: {e}")

    # Cache the successfully retrieved league
    if league:
        espn_cache.set(cache_key, league)
        return league
    else:
        raise ConnectionError("Unable to initialize any league connection")

def get_points(player):
    """Get total fantasy points for a player"""
    return getattr(player, 'total_points', 0)

def get_proj(player):
    """Get projected points for a player"""
    return getattr(player, 'projected_total_points', 0)

def validate_team_name(team_name, league_teams):
    """Validate and normalize team name input"""
    if not team_name or not isinstance(team_name, str):
        return None

    # Remove extra whitespace and convert to lowercase for comparison
    normalized_input = team_name.strip().lower()

    # Try exact match first
    for team in league_teams:
        if team.team_name.lower() == normalized_input:
            return team

    # Try partial match
    for team in league_teams:
        if normalized_input in team.team_name.lower():
            return team

    return None

def validate_player_name(player_name):
    """Validate and sanitize player name input"""
    if not player_name or not isinstance(player_name, str):
        return None

    # Remove extra whitespace and limit length
    sanitized = player_name.strip()[:50]  # Reasonable limit for player names

    # Basic sanitization - remove potentially harmful characters
    allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \'.-')
    sanitized = ''.join(c for c in sanitized if c in allowed_chars)

    return sanitized if sanitized else None

# Simple cache for league data to avoid repeated API calls
_league_cache = {}

def get_cached_league_data(cache_key, fetch_function, cache_duration_seconds=300):
    """Cache league data to avoid repeated API calls within 5 minutes"""
    import time

    current_time = time.time()

    if cache_key in _league_cache:
        cached_data, timestamp = _league_cache[cache_key]
        if current_time - timestamp < cache_duration_seconds:
            return cached_data

    # Fetch fresh data
    data = fetch_function()
    _league_cache[cache_key] = (data, current_time)
    return data

def safe_field_value(text, max_length=DISCORD_EMBED_CHAR_LIMIT):
    """Safely truncate text to fit Discord embed field limits"""
    if len(text) <= max_length:
        return text

    # Truncate and add ellipsis
    return text[:max_length-3] + "..."


@client.event
async def on_ready():
    print(f'Bot is ready! Commands should be synced via setup_hook.')

    # Start background refresh for better performance
    background_refresh_manager.start_background_refresh()

@client.tree.command(name="sync_commands", description="Manually sync bot commands with Discord (admin only).")
@app_commands.checks.has_permissions(administrator=True)
async def sync_commands(interaction: discord.Interaction):
    """Manually sync commands - useful for testing new features"""
    try:
        await interaction.response.defer(ephemeral=True)

        # Clear and re-sync commands
        client.tree.clear_commands()
        await client.tree.sync()

        # Reload commands (this forces re-registration)
        synced = await client.tree.sync()

        await interaction.followup.send(f"✅ Successfully cleared and synced {len(synced)} commands to Discord!\n⚠️ Autocomplete may take a few minutes to activate.", ephemeral=True)
        print(f"Manual clear+sync by {interaction.user}: {len(synced)} commands synced")

    except Exception as e:
        await interaction.followup.send(f"❌ Failed to sync commands: {str(e)[:100]}", ephemeral=True)
        print(f"Manual sync failed: {e}")

@client.tree.command(name="debug_autocomplete", description="Test autocomplete functionality (admin only).")
@app_commands.checks.has_permissions(administrator=True)
async def debug_autocomplete(interaction: discord.Interaction):
    """Debug command to test if autocomplete is working"""
    try:
        await interaction.response.defer(ephemeral=True)

        # Test if we can get league data
        league = get_league(user_id=interaction.user.id)
        if not league:
            await interaction.followup.send("❌ No league found for autocomplete testing", ephemeral=True)
            return

        team_count = len(league.teams)
        total_players = sum(len(team.roster) for team in league.teams)

        await interaction.followup.send(f"✅ Autocomplete data available:\n- {team_count} teams\n- {total_players} total players\n\nIf autocomplete still doesn't work, try restarting the bot.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Autocomplete debug failed: {str(e)[:100]}", ephemeral=True)

@client.tree.command(name="detailed_stats", description="Power rankings and league-wide scoring analytics.")
async def detailed_stats(interaction: discord.Interaction):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        current_week = getattr(league, 'current_week', 1)
        box_scores = league.box_scores(week=current_week)

        teams_analysis = []
        week_proj_by_team = {}
        for m in box_scores:
            for team, lineup in ((m.home_team, m.home_lineup), (m.away_team, m.away_lineup)):
                if team:
                    week_proj_by_team[team.team_id] = sum(float(getattr(p, 'projected_points', 0) or 0) for p in lineup if getattr(p, 'slot_position', None) != 'BE')

        for team in league.teams:
            games = team.wins + team.losses
            ppg = team.points_for / games if games else 0
            teams_analysis.append({
                'name': team.team_name, 'points_for': team.points_for, 'points_against': team.points_against,
                'wins': team.wins, 'losses': team.losses, 'ppg': ppg,
                'week_proj': week_proj_by_team.get(team.team_id, 0),
            })

        total_points = sum(t['points_for'] for t in teams_analysis)
        avg_ppg = sum(t['ppg'] for t in teams_analysis) / len(teams_analysis)

        for t in teams_analysis:
            win_pct = t['wins'] / max(t['wins'] + t['losses'], 1)
            t['power'] = (win_pct * 0.6) + ((t['ppg'] / avg_ppg) * 0.4) if avg_ppg else 0

        top5 = sorted(teams_analysis, key=lambda t: t['power'], reverse=True)[:5]
        rankings = [{'rank': i + 1, 'name': t['name'], 'record': f"{t['wins']}-{t['losses']}", 'ppg': t['ppg'], 'power': t['power']}
                    for i, t in enumerate(top5)]

        top_proj = max(teams_analysis, key=lambda t: t['week_proj'])

        card_data = {
            'league_name': get_league_name(user_id=interaction.user.id),
            'total_points': total_points, 'avg_ppg': avg_ppg, 'rankings': rankings,
            'footer_label': f"Top Week {current_week} Proj", 'footer_value': f"{top_proj['name']} \u00b7 {top_proj['week_proj']:.1f}",
        }
        image_buf = render_power_rankings_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="power_rankings.png"))
    except Exception as e:
        print(f"Detailed stats command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error generating power rankings: {e}", ephemeral=True)


@client.tree.command(name="ping", description="Check if the bot is alive.")
async def ping(interaction: discord.Interaction):
    import time
    start_time = time.time()

    embed = discord.Embed(
        title="🏓 Pong!",
        description="**Bot is online and responding**",
        color=EMBED_COLOR_SUCCESS
    )

    # Calculate response time
    response_time = round((time.time() - start_time) * 1000, 2)

    embed.add_field(
        name="⚡ Response Time",
        value=f"**{response_time}ms**",
        inline=True
    )

    embed.add_field(
        name="🏈 Status",
        value="**Ready for Fantasy Football!**",
        inline=True
    )

    embed.set_footer(text="💡 Try /help for available commands")

    await interaction.response.send_message(embed=embed)


@client.tree.command(name="team", description="Generate a visual roster card for a team.")
@app_commands.describe(team_name="The exact name of the team as it appears in ESPN.")
@app_commands.autocomplete(team_name=team_name_autocomplete)
async def team(interaction: discord.Interaction, team_name: str):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        team = next((t for t in league.teams if t.team_name.lower() == team_name.lower()), None)
        if not team:
            partial_matches = [t for t in league.teams if team_name.lower() in t.team_name.lower()]
            if partial_matches:
                suggestions = ", ".join(f"'{t.team_name}'" for t in partial_matches[:3])
                await interaction.followup.send(f"❌ Team '{team_name}' not found.\n💡 Did you mean: {suggestions}?")
            else:
                team_list = ", ".join(f"'{t.team_name}'" for t in league.teams[:5])
                await interaction.followup.send(f"❌ Team '{team_name}' not found.\n📋 Available teams: {team_list}...")
            return

        current_week = getattr(league, 'current_week', 1)

        # team.roster's player.stats dict comes back empty once the season
        # isn't "live" in ESPN's eyes (e.g. completed weeks) -- box_scores()
        # is the reliable source for this week's actual/projected points AND
        # gives a real pro_opponent field the plain roster objects don't have.
        box_score = next(
            (m for m in league.box_scores(week=current_week)
             if (m.home_team and m.home_team.team_id == team.team_id) or
                (m.away_team and m.away_team.team_id == team.team_id)),
            None
        )
        lineup = (box_score.home_lineup if box_score.home_team.team_id == team.team_id else box_score.away_lineup) if box_score else team.roster

        # FLEX shows up as a combo slot like "RB/WR/TE" -- D/ST and BE are
        # exact matches so they don't get swept into that bucket.
        slot_order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'FLEX': 4, 'D/ST': 5, 'K': 6}

        def display_slot(player):
            slot = getattr(player, 'slot_position', None) or getattr(player, 'lineupSlot', '') or ''
            if slot == 'BE':
                return 'BE'
            if slot in ('D/ST', 'DST'):
                return 'D/ST'
            if '/' in slot:
                return 'FLEX'
            return slot or player.position

        def sort_key(player):
            return slot_order.get(display_slot(player), 99)

        def get_status(player):
            if player.position in ('D/ST', 'DST', 'DEF'):
                return None
            status = getattr(player, 'injuryStatus', None)
            if status == 'QUESTIONABLE':
                return 'Q'
            if status in ('OUT', 'DOUBTFUL', 'INJURY_RESERVE'):
                return 'O'
            return None

        starters = sorted([p for p in lineup if display_slot(p) != 'BE'], key=sort_key)
        bench = [p for p in lineup if display_slot(p) == 'BE']

        player_ids = [p.playerId for p in starters + bench if p.position not in ('D/ST', 'DST', 'DEF')]
        team_abbrs = [p.proTeam.lower() for p in starters + bench if p.position in ('D/ST', 'DST', 'DEF') and p.proTeam]
        images = await get_images(player_ids=player_ids, team_abbrs=team_abbrs)

        def build_row(player):
            is_dst = player.position in ('D/ST', 'DST', 'DEF')
            return {
                'slot': display_slot(player),
                'position': player.position,
                'name': player.name,
                'team_abbr': player.proTeam or '',
                'opp_abbr': getattr(player, 'pro_opponent', '') or '',
                'headshot_path': images['teams'].get(player.proTeam.lower()) if is_dst and player.proTeam else images['players'].get(player.playerId),
                'is_logo': is_dst,
                'status': get_status(player),
                'actual': float(getattr(player, 'points', 0) or 0),
                'proj': float(getattr(player, 'projected_points', 0) or 0),
                'live': None,  # live scoreboard data isn't wired up yet -- see project memory
            }

        starter_rows = [build_row(p) for p in starters]
        bench_rows = [build_row(p) for p in bench]

        wins, losses = getattr(team, 'wins', 0), getattr(team, 'losses', 0)
        sorted_teams = sorted(league.teams, key=lambda t: (getattr(t, 'wins', 0), getattr(t, 'points_for', 0)), reverse=True)
        rank = next((i + 1 for i, t in enumerate(sorted_teams) if t.team_id == team.team_id), 0)

        owner_data = getattr(team, 'owners', None) or getattr(team, 'owner', None)
        owner_name = "N/A"
        if owner_data:
            if isinstance(owner_data, dict):
                owner_name = (owner_data.get('displayName') or
                              f"{owner_data.get('firstName', '')} {owner_data.get('lastName', '')}".strip() or
                              owner_data.get('id', 'N/A'))
            elif isinstance(owner_data, str):
                owner_name = owner_data
            elif isinstance(owner_data, list) and owner_data:
                first_owner = owner_data[0]
                if isinstance(first_owner, dict):
                    owner_name = (first_owner.get('firstName') or first_owner.get('displayName') or
                                  f"{first_owner.get('firstName', '')} {first_owner.get('lastName', '')}".strip() or
                                  first_owner.get('id', 'N/A'))
                else:
                    owner_name = str(first_owner)
        if owner_name and '@' in owner_name and '.' in owner_name:
            owner_name = owner_name.split('@')[0]
        if not owner_name or owner_name == "N/A" or len(str(owner_name)) > 50:
            owner_name = "Unknown Owner"

        card_data = {
            'team_name': team.team_name,
            'owner_name': owner_name,
            'record': f"{wins}-{losses}",
            'rank': rank,
            'total_teams': len(league.teams),
            'current_week': current_week,
            'proj_record': None,
            'proj_record_note': None,
            'live_games_count': 0,
            'starters': starter_rows,
            'bench': bench_rows,
            'starters_total_actual': sum(r['actual'] for r in starter_rows),
            'starters_total_proj': sum(r['proj'] for r in starter_rows),
        }

        image_buf = render_team_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="team_card.png"))
    except Exception as e:
        print(f"Team command error: {e}")
        await safe_interaction_response(interaction, f"❌ Error creating team card: {e}", ephemeral=True)

def _player_position_stats(player):
    """Position-specific stat tiles, pulled from the real season breakdown.
    espn_api's per-play offensive yardage fields (passing/rushing/receiving)
    already come back as a per-game rate, but the D/ST fields don't -- those
    are season totals, so they're divided by games played below (verified
    against Seahawks D/ST: raw defensivePointsAllowed of 286 makes no sense
    as "per game" but is a very normal season total over 17 games)."""
    b = (player.stats or {}).get(0, {}).get('breakdown', {})
    pos = player.position
    games_played = round(player.total_points / player.avg_points) if player.avg_points else 1

    def g(key, default=0):
        return b.get(key, default) or 0

    if pos == 'QB':
        return [
            {'value': f"{g('passingYards'):.1f}", 'label': 'Pass Yds/G'},
            {'value': f"{int(g('passingTouchdowns'))}", 'label': 'Pass TD'},
            {'value': f"{int(g('passingInterceptions'))}", 'label': 'INT'},
            {'value': f"{g('passingCompletionPercentage') * 100:.1f}%", 'label': 'Completion'},
            {'value': f"{g('rushingYards'):.1f}", 'label': 'Rush Yds/G'},
            {'value': f"{int(g('rushingTouchdowns'))}", 'label': 'Rush TD'},
        ]
    if pos == 'RB':
        return [
            {'value': f"{g('rushingYards'):.1f}", 'label': 'Rush Yds/G'},
            {'value': f"{int(g('rushingTouchdowns'))}", 'label': 'Rush TD'},
            {'value': f"{int(g('receivingReceptions'))}", 'label': 'Rec'},
            {'value': f"{g('receivingYards'):.1f}", 'label': 'Rec Yds/G'},
            {'value': f"{int(g('receivingTouchdowns'))}", 'label': 'Rec TD'},
            {'value': f"{int(g('lostFumbles'))}", 'label': 'Fumbles Lost'},
        ]
    if pos in ('WR', 'TE'):
        return [
            {'value': f"{int(g('receivingReceptions'))}", 'label': 'Rec'},
            {'value': f"{g('receivingYards'):.1f}", 'label': 'Rec Yds/G'},
            {'value': f"{int(g('receivingTouchdowns'))}", 'label': 'Rec TD'},
            {'value': f"{int(g('receivingTargets'))}", 'label': 'Targets'},
            {'value': f"{g('receivingYardsPerReception'):.1f}", 'label': 'Yds/Rec'},
            {'value': f"{g('receivingYardsAfterCatch'):.1f}", 'label': 'YAC/G'},
        ]
    if pos == 'K':
        made, att = g('madeFieldGoals'), g('attemptedFieldGoals')
        return [
            {'value': f"{int(made)}", 'label': 'FG Made'},
            {'value': f"{int(att)}", 'label': 'FG Att'},
            {'value': f"{(made / att * 100) if att else 0:.1f}%", 'label': 'FG%'},
            {'value': f"{int(g('madeExtraPoints'))}", 'label': 'XP Made'},
            {'value': f"{int(g('madeFieldGoalsFrom50Plus'))}", 'label': '50+ Made'},
            {'value': f"{int(g('madeFieldGoalsFrom40To49'))}", 'label': '40-49 Made'},
        ]
    if pos in ('D/ST', 'DST', 'DEF'):
        return [
            {'value': f"{g('defensiveSacks'):.1f}", 'label': 'Sacks'},
            {'value': f"{int(g('defensiveInterceptions'))}", 'label': 'INT'},
            {'value': f"{int(g('defensiveFumbles'))}", 'label': 'Fum Rec'},
            {'value': f"{int(g('defensivePlusSpecialTeamsTouchdowns'))}", 'label': 'Def/ST TD'},
            {'value': f"{g('defensivePointsAllowed') / games_played:.1f}", 'label': 'Pts Allowed/G'},
            {'value': f"{g('defensiveYardsAllowed') / games_played:.1f}", 'label': 'Yds Allowed/G'},
        ]
    return []


@client.tree.command(name="player", description="Visual player card with season stats.")
@app_commands.describe(player_name="The name of the player to look up.")
@app_commands.autocomplete(player_name=player_name_autocomplete)
async def player(interaction: discord.Interaction, player_name: str):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        player_name_lower = player_name.lower()
        found_player, player_team = None, None
        for team in league.teams:
            found = next((p for p in team.roster if player_name_lower in p.name.lower()), None)
            if found:
                found_player, player_team = found, team
                break
        if not found_player:
            await interaction.followup.send(f"Player '{player_name}' not found on any roster.")
            return

        is_dst = found_player.position in ('D/ST', 'DST', 'DEF')
        images = await get_images(
            player_ids=[] if is_dst else [found_player.playerId],
            team_abbrs=[found_player.proTeam.lower()] if found_player.proTeam else [],
        )
        headshot_path = images['teams'].get(found_player.proTeam.lower()) if is_dst else images['players'].get(found_player.playerId)
        team_logo_path = images['teams'].get(found_player.proTeam.lower())

        status_map = {'QUESTIONABLE': 'Questionable', 'OUT': 'Out', 'DOUBTFUL': 'Doubtful', 'INJURY_RESERVE': 'Injury Reserve'}
        status = status_map.get(getattr(found_player, 'injuryStatus', None), 'Active')

        current_week = getattr(league, 'current_week', 1)
        highlight_label = highlight_text = None
        try:
            box_score = next((m for m in league.box_scores(week=current_week)
                               if m.home_team and m.home_team.team_id == player_team.team_id or m.away_team and m.away_team.team_id == player_team.team_id), None)
            if box_score:
                lineup = box_score.home_lineup if box_score.home_team.team_id == player_team.team_id else box_score.away_lineup
                box_player = next((p for p in lineup if p.playerId == found_player.playerId), None)
                if box_player and getattr(box_player, 'game_played', 0) > 0:
                    role = "started by" if box_player.slot_position != 'BE' else "benched by"
                    highlight_label = f"Week {current_week}"
                    highlight_text = f"{box_player.points:.1f} pts (proj {box_player.projected_points:.1f}) · {role} {player_team.team_name}"
        except Exception as e:
            print(f"Player highlight lookup failed: {e}")

        games_played = round(found_player.total_points / found_player.avg_points) if found_player.avg_points else 0
        card_data = {
            'name': found_player.name, 'position': found_player.position, 'pro_team': found_player.proTeam or 'FA',
            'status': status, 'headshot_path': headshot_path, 'team_logo_path': team_logo_path,
            'ppg': found_player.avg_points, 'total_points': found_player.total_points, 'games_played': games_played,
            'highlight_label': highlight_label, 'highlight_text': highlight_text,
            'stats': _player_position_stats(found_player),
            'fantasy_team': player_team.team_name, 'roster_slot': getattr(found_player, 'lineupSlot', None),
        }
        image_buf = render_player_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="player.png"))
    except Exception as e:
        print(f"Player command error: {e}")
        await safe_interaction_response(interaction, f"❌ Error creating player card: {e}", ephemeral=True)

def _team_owner_name(team):
    owners = getattr(team, 'owners', None)
    if owners:
        first = owners[0]
        if isinstance(first, dict):
            return first.get('firstName') or first.get('displayName') or 'Unknown'
        return str(first)
    return 'Unknown'


def _team_record_str(team):
    r = f"{team.wins}-{team.losses}"
    return r + (f"-{team.ties}" if getattr(team, 'ties', 0) else '')


def _h2h_series(team_a, team_b):
    """Real head-to-head record between two teams this season, computed
    from actual weekly scores (same approach proven reliable for the
    standings/scoreboard cards) rather than a 'winner' attribute that
    isn't populated on every espn_api version."""
    a_wins = b_wins = ties = 0
    for week_num, opp in enumerate(team_a.schedule, 1):
        if not opp or getattr(opp, 'team_id', None) != team_b.team_id:
            continue
        if week_num - 1 >= len(team_a.scores) or week_num - 1 >= len(team_b.scores):
            continue
        sa, sb = team_a.scores[week_num - 1], team_b.scores[week_num - 1]
        if not sa and not sb:
            continue
        if sa > sb:
            a_wins += 1
        elif sb > sa:
            b_wins += 1
        else:
            ties += 1
    return a_wins, b_wins, ties


async def _build_compare_card(league, team1_obj, team2_obj, user_id, league1_name=None, league2_name=None):
    current_week = getattr(league, 'current_week', 1)
    starters1 = [p for p in team1_obj.roster if getattr(p, 'lineupSlot', None) != "BE"]
    starters2 = [p for p in team2_obj.roster if getattr(p, 'lineupSlot', None) != "BE"]
    proj1 = sum(float(get_current_week_points(p, league) or 0) for p in starters1 if get_current_week_points(p, league) != 'N/A')
    proj2 = sum(float(get_current_week_points(p, league) or 0) for p in starters2 if get_current_week_points(p, league) != 'N/A')

    games1 = team1_obj.wins + team1_obj.losses + getattr(team1_obj, 'ties', 0)
    games2 = team2_obj.wins + team2_obj.losses + getattr(team2_obj, 'ties', 0)
    ppg1 = team1_obj.points_for / games1 if games1 else 0
    ppg2 = team2_obj.points_for / games2 if games2 else 0

    logos = await get_logos_by_url([team1_obj.logo_url, team2_obj.logo_url])

    series_note, warn = None, False
    if league1_name is None:  # same league -- a real head-to-head series is meaningful
        w1, w2, ties = _h2h_series(team1_obj, team2_obj)
        if w1 or w2 or ties:
            if w1 > w2:
                series_note = f"{team1_obj.team_name} leads season series {w1}-{w2}"
            elif w2 > w1:
                series_note = f"{team2_obj.team_name} leads season series {w2}-{w1}"
            else:
                series_note = f"Season series tied {w1}-{w2}"
    else:
        series_note, warn = "Different Leagues \u00b7 Cross-League Comparison", True

    def row(label, v1, v2, lower_is_better=False):
        win1 = (v1 < v2) if lower_is_better else (v1 > v2)
        win2 = (v2 < v1) if lower_is_better else (v2 > v1)
        return {'label': label, 'left_val': f"{v1:.1f}", 'right_val': f"{v2:.1f}",
                'left_win': win1 if v1 != v2 else None, 'right_win': win2 if v1 != v2 else None}

    return {
        'team1': {'name': team1_obj.team_name, 'owner': _team_owner_name(team1_obj), 'record': _team_record_str(team1_obj),
                  'logo_path': logos.get(team1_obj.logo_url), 'league': league1_name},
        'team2': {'name': team2_obj.team_name, 'owner': _team_owner_name(team2_obj), 'record': _team_record_str(team2_obj),
                  'logo_path': logos.get(team2_obj.logo_url), 'league': league2_name},
        'series_note': series_note, 'series_note_warn': warn,
        'rows': [
            row('Points For', team1_obj.points_for, team2_obj.points_for),
            row('Points Against', team1_obj.points_against, team2_obj.points_against, lower_is_better=True),
            row('PPG', ppg1, ppg2),
            row(f"Week {current_week} Proj", proj1, proj2),
        ],
    }


@client.tree.command(name="compare", description="Visual season-long comparison of two teams.")
@app_commands.describe(team1="First team name", team2="Second team name")
@app_commands.autocomplete(team1=team_name_autocomplete, team2=team_name_autocomplete)
async def compare(interaction: discord.Interaction, team1: str, team2: str):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
        team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found.")
            return
        if not team2_obj:
            await interaction.followup.send(f"Team '{team2}' not found.")
            return

        card_data = await _build_compare_card(league, team1_obj, team2_obj, interaction.user.id)
        image_buf = render_compare_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="compare.png"))
    except Exception as e:
        print(f"Compare command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error comparing teams: {e}", ephemeral=True)


@client.tree.command(name="standings", description="Show league standings with records and points.")
async def standings(interaction: discord.Interaction):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        current_week = getattr(league, 'current_week', 1)
        settings = league.settings
        reg_season_count = getattr(settings, 'reg_season_count', 0)
        playoff_team_count = getattr(settings, 'playoff_team_count', 0)

        teams_data = []
        for team in league.teams:
            wins, losses, ties = team.wins, team.losses, getattr(team, 'ties', 0)
            games = wins + losses + ties
            win_pct = (wins + ties * 0.5) / games if games else 0.0
            teams_data.append({
                'team': team, 'wins': wins, 'losses': losses, 'ties': ties,
                'pf': team.points_for, 'pa': team.points_against, 'win_pct': win_pct,
            })
        teams_data.sort(key=lambda t: (t['win_pct'], t['pf']), reverse=True)

        logos = await get_logos_by_url([t['team'].logo_url for t in teams_data])

        def record_str(t):
            r = f"{t['wins']}-{t['losses']}"
            return r + f"-{t['ties']}" if t['ties'] else r

        def owner_name(team):
            owners = getattr(team, 'owners', None)
            if owners:
                first = owners[0]
                if isinstance(first, dict):
                    return first.get('firstName') or first.get('displayName') or 'Unknown'
                return str(first)
            return 'Unknown'

        rows = []
        for i, t in enumerate(teams_data):
            team = t['team']
            streak_type = getattr(team, 'streak_type', None)
            streak_len = getattr(team, 'streak_length', 0)
            streak = f"{'W' if streak_type == 'WIN' else 'L'}{streak_len}" if streak_type else "-"
            rows.append({
                'rank': i + 1, 'name': team.team_name, 'owner': owner_name(team),
                'record': record_str(t), 'pf': t['pf'], 'pa': t['pa'], 'streak': streak,
                'logo_path': logos.get(team.logo_url),
            })

        playoff_gb = None
        if 0 < playoff_team_count < len(teams_data):
            leader, chaser = teams_data[playoff_team_count - 1], teams_data[playoff_team_count]
            playoff_gb = ((leader['wins'] - chaser['wins']) + (chaser['losses'] - leader['losses'])) / 2

        if reg_season_count and current_week > reg_season_count:
            header_right_label, header_right_sub = "FINAL", f"{reg_season_count} Games Played"
        else:
            header_right_label, header_right_sub = f"WK {current_week}", "Regular Season"

        rec_rule = next((r for r in settings.scoring_format if r.get('abbr') == 'REC'), None)
        rec_pts = rec_rule['points'] if rec_rule else 0
        scoring_label = "Full PPR" if rec_pts == 1 else "Half PPR" if rec_pts == 0.5 else "Standard" if rec_pts == 0 else f"{rec_pts:g} PPR"

        card_data = {
            'league_name': get_league_name(user_id=interaction.user.id),
            'team_count': len(teams_data), 'scoring_label': scoring_label,
            'header_right_label': header_right_label, 'header_right_sub': header_right_sub,
            'playoff_team_count': playoff_team_count, 'playoff_gb': playoff_gb,
            'teams': rows,
        }
        image_buf = render_standings_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="standings.png"))
    except Exception as e:
        print(f"Standings command error: {e}")
        await safe_interaction_response(interaction, f"❌ Error creating standings card: {e}", ephemeral=True)
@client.tree.command(name="stats", description="League superlatives -- consistency, luck, schedule strength.")
async def stats(interaction: discord.Interaction):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        reg_season_count = getattr(league.settings, 'reg_season_count', getattr(league, 'current_week', 1) - 1)

        teams_analytics = []
        for team in league.teams:
            scores = [s for s in getattr(team, 'scores', [])[:reg_season_count] if s]
            games = team.wins + team.losses + getattr(team, 'ties', 0)
            win_pct = (team.wins + getattr(team, 'ties', 0) * 0.5) / games if games else 0
            std_dev = statistics.pstdev(scores) if len(scores) > 1 else 0
            teams_analytics.append({
                'name': team.team_name, 'points_for': team.points_for, 'points_against': team.points_against,
                'win_pct': win_pct, 'std_dev': std_dev, 'scores': scores,
            })

        if not teams_analytics:
            await interaction.followup.send("No team data available.")
            return

        most_consistent = min(teams_analytics, key=lambda t: t['std_dev'])
        most_volatile = max(teams_analytics, key=lambda t: t['std_dev'])

        all_weekly = [(s, t['name']) for t in teams_analytics for s in t['scores']]
        best_week = max(all_weekly, key=lambda x: x[0]) if all_weekly else (0, 'N/A')
        worst_week = min(all_weekly, key=lambda x: x[0]) if all_weekly else (0, 'N/A')

        for t in teams_analytics:
            t['efficiency'] = t['win_pct'] / (t['points_for'] / 1000) if t['points_for'] else 0
        most_efficient = max(teams_analytics, key=lambda t: t['efficiency'])

        avg_pf = sum(t['points_for'] for t in teams_analytics) / len(teams_analytics)
        unlucky_candidates = [t for t in teams_analytics if t['points_for'] > avg_pf and t['win_pct'] < 0.5]
        unluckiest = max(unlucky_candidates, key=lambda t: t['points_for']) if unlucky_candidates else None

        toughest = max(teams_analytics, key=lambda t: t['points_against'])
        easiest = min(teams_analytics, key=lambda t: t['points_against'])

        def record_of(name):
            team = next(t for t in league.teams if t.team_name == name)
            return f"{team.wins}-{team.losses}"

        tiles = [
            {'label': 'Most Consistent', 'team': most_consistent['name'], 'value': f"\u03c3 {most_consistent['std_dev']:.1f}", 'sub': None},
            {'label': 'Most Volatile', 'team': most_volatile['name'], 'value': f"\u03c3 {most_volatile['std_dev']:.1f}", 'sub': None},
            {'label': 'Best Single Week', 'team': best_week[1], 'value': f"{best_week[0]:.1f}", 'sub': 'pts'},
            {'label': 'Worst Single Week', 'team': worst_week[1], 'value': f"{worst_week[0]:.1f}", 'sub': 'pts'},
            {'label': 'Most Efficient', 'team': most_efficient['name'], 'value': f"{most_efficient['efficiency']:.3f}", 'sub': 'win/1000pf'},
        ]
        if unluckiest:
            tiles.append({'label': 'Unluckiest', 'team': unluckiest['name'], 'value': f"{unluckiest['points_for']:.1f} PF", 'sub': record_of(unluckiest['name'])})
        tiles.append({'label': 'Toughest Schedule', 'team': toughest['name'], 'value': f"{toughest['points_against']:.1f}", 'sub': 'PA'})
        tiles.append({'label': 'Easiest Schedule', 'team': easiest['name'], 'value': f"{easiest['points_against']:.1f}", 'sub': 'PA'})

        card_data = {
            'title': 'League Stats', 'league_name': get_league_name(user_id=interaction.user.id),
            'subtitle': 'Full Season', 'tiles': tiles,
        }
        image_buf = render_stat_tiles_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="stats.png"))
    except Exception as e:
        print(f"Stats command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error fetching stats: {e}", ephemeral=True)


@client.tree.command(name="sleeper", description="Find undervalued sleeper picks with high upside potential.")
@app_commands.describe(position="Filter by position (QB, RB, WR, TE, K, D/ST) - leave empty for all positions")
async def sleeper(interaction: discord.Interaction, position: str = None):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        free_agents = league.free_agents(size=200)
        candidates = []
        for p in free_agents:
            if position and p.position.upper() != position.upper():
                continue
            proj = getattr(p, 'projected_total_points', 0) or 0
            avg = getattr(p, 'avg_points', 0) or 0
            owned = getattr(p, 'percent_owned', 0) or 0
            if proj <= 0:
                continue
            score = 0
            if owned < 50:
                score += (50 - owned) * 0.1
            if proj > avg:
                score += (proj - avg) * 0.5
            if proj >= 150:  # season-long projection, not a single week
                score += proj * 0.02
            candidates.append({'player': p, 'proj': proj, 'avg': avg, 'owned': owned, 'score': score})

        candidates.sort(key=lambda c: c['score'], reverse=True)
        top = candidates[:5]

        if not top:
            await interaction.followup.send("No sleeper candidates found with current criteria.")
            return

        images = await get_images(player_ids=[c['player'].playerId for c in top], team_abbrs=[])
        players = [{
            'rank': i + 1, 'name': c['player'].name, 'position': c['player'].position,
            'sub': f"{c['player'].proTeam} \u00b7 {c['owned']:.1f}% owned",
            'headshot_path': images['players'].get(c['player'].playerId),
            'metric1_val': f"{c['proj']:.1f}", 'metric1_label': 'proj',
            'metric2_val': f"{c['avg']:.1f}", 'metric2_label': 'avg',
            'tag': None, 'tag_type': None,
        } for i, c in enumerate(top)]

        avg_owned = sum(c['owned'] for c in top) / len(top)
        card_data = {
            'title': 'Sleeper Picks', 'league_name': get_league_name(user_id=interaction.user.id),
            'subtitle': f"{position.upper()} Only" if position else "All Positions",
            'header_right_val': f"{avg_owned:.1f}%", 'header_right_sub': 'avg owned',
            'players': players, 'footer_label': None, 'footer_value': None,
        }
        image_buf = render_player_list_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="sleepers.png"))
    except Exception as e:
        print(f"Sleeper command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error finding sleepers: {e}", ephemeral=True)

@client.tree.command(name="matchup", description="Head-to-head visual matchup card for this week.")
@app_commands.describe(team1="First team name", team2="Second team name (optional - will try to find current matchup)")
async def matchup(interaction: discord.Interaction, team1: str, team2: str = None):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found.")
            return

        current_week = getattr(league, 'current_week', 1)
        box_scores = league.box_scores(week=current_week)

        box_score = None
        team2_obj = None
        if team2:
            team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
            if not team2_obj:
                await interaction.followup.send(f"Team '{team2}' not found.")
                return
            box_score = next((m for m in box_scores if {getattr(m.home_team, 'team_id', None), getattr(m.away_team, 'team_id', None)} == {team1_obj.team_id, team2_obj.team_id}), None)
        else:
            box_score = next((m for m in box_scores if (m.home_team and m.home_team.team_id == team1_obj.team_id) or (m.away_team and m.away_team.team_id == team1_obj.team_id)), None)
            if box_score:
                team2_obj = box_score.away_team if box_score.home_team.team_id == team1_obj.team_id else box_score.home_team

        if not box_score or not team2_obj:
            await interaction.followup.send(f"Could not find a matchup for {team1_obj.team_name} in week {current_week}. Specify both teams: `/matchup {team1} TeamName`")
            return

        team1_is_home = box_score.home_team.team_id == team1_obj.team_id
        lineup1 = box_score.home_lineup if team1_is_home else box_score.away_lineup
        lineup2 = box_score.away_lineup if team1_is_home else box_score.home_lineup
        score1 = box_score.home_score if team1_is_home else box_score.away_score
        score2 = box_score.away_score if team1_is_home else box_score.home_score

        slot_order = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'FLEX': 4, 'D/ST': 5, 'K': 6}

        def display_slot(p):
            slot = getattr(p, 'slot_position', '') or ''
            if slot == 'BE':
                return 'BE'
            if slot in ('D/ST', 'DST'):
                return 'D/ST'
            if '/' in slot:
                return 'FLEX'
            return slot or p.position

        # Bench isn't slotted by position, so pair the two benches by index
        # rather than trying to match positions that don't correspond.
        starters1 = sorted([p for p in lineup1 if display_slot(p) != 'BE'], key=lambda p: slot_order.get(display_slot(p), 99))
        starters2 = sorted([p for p in lineup2 if display_slot(p) != 'BE'], key=lambda p: slot_order.get(display_slot(p), 99))
        bench1 = [p for p in lineup1 if display_slot(p) == 'BE']
        bench2 = [p for p in lineup2 if display_slot(p) == 'BE']

        all_players = starters1 + starters2 + bench1 + bench2
        player_ids = [p.playerId for p in all_players if p.position not in ('D/ST', 'DST', 'DEF')]
        team_abbrs = [p.proTeam.lower() for p in all_players if p.position in ('D/ST', 'DST', 'DEF') and p.proTeam]
        images = await get_images(player_ids=player_ids, team_abbrs=team_abbrs)
        logos = await get_logos_by_url([team1_obj.logo_url, team2_obj.logo_url])

        def side(p, win=None):
            is_dst = p.position in ('D/ST', 'DST', 'DEF')
            return {
                'name': p.name, 'position': p.position,
                'headshot_path': images['teams'].get(p.proTeam.lower()) if is_dst and p.proTeam else images['players'].get(p.playerId),
                'is_logo': is_dst,
                'pts': float(getattr(p, 'points', 0) or 0), 'proj': float(getattr(p, 'projected_points', 0) or 0),
                'win': win,
            }

        starter_rows = []
        for p1, p2 in zip(starters1, starters2):
            pts1, pts2 = float(getattr(p1, 'points', 0) or 0), float(getattr(p2, 'points', 0) or 0)
            starter_rows.append({'slot': display_slot(p1), 'left': side(p1, pts1 > pts2), 'right': side(p2, pts2 > pts1)})

        bench_rows = [{'slot': 'BE', 'left': side(p1), 'right': side(p2)} for p1, p2 in zip(bench1, bench2)]

        gp_values = [getattr(p, 'game_played', 0) for p in lineup1]
        if gp_values and all(v == 100 for v in gp_values):
            status = "Final"
        elif any(v > 0 for v in gp_values):
            status = "Live"
        else:
            status = "Upcoming"

        def record_str(t):
            r = f"{t.wins}-{t.losses}"
            return r + (f"-{t.ties}" if getattr(t, 'ties', 0) else '')

        def owner_name(t):
            owners = getattr(t, 'owners', None)
            if owners:
                first = owners[0]
                return (first.get('firstName') or first.get('displayName') or 'Unknown') if isinstance(first, dict) else str(first)
            return 'Unknown'

        card_data = {
            'team1': {'name': team1_obj.team_name, 'owner': owner_name(team1_obj), 'record': record_str(team1_obj),
                      'score': float(score1), 'logo_path': logos.get(team1_obj.logo_url)},
            'team2': {'name': team2_obj.team_name, 'owner': owner_name(team2_obj), 'record': record_str(team2_obj),
                      'score': float(score2), 'logo_path': logos.get(team2_obj.logo_url)},
            'header_sub': f"{status} · Week {current_week}",
            'starters': starter_rows, 'bench': bench_rows,
            'totals': {'left': sum(r['left']['pts'] for r in starter_rows), 'right': sum(r['right']['pts'] for r in starter_rows)},
        }
        image_buf = render_matchup_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="matchup.png"))
    except Exception as e:
        print(f"Matchup command error: {e}")
        await safe_interaction_response(interaction, f"❌ Error creating matchup card: {e}", ephemeral=True)

@client.tree.command(name="waiver", description="Analyze waiver wire for top pickup recommendations.")
@app_commands.describe(
    position="Filter by position (QB, RB, WR, TE, K, D/ST)",
    min_owned="Minimum ownership percentage (0-100, default: 0)",
    max_owned="Maximum ownership percentage (0-100, default: 50)"
)
async def waiver(interaction: discord.Interaction, position: str = None, min_owned: int = 0, max_owned: int = 50):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        valid_positions = ['QB', 'RB', 'WR', 'TE', 'K', 'D/ST', 'DST']
        if position:
            position = position.upper()
            if position not in valid_positions:
                await interaction.followup.send(f"Invalid position. Valid options: {', '.join(valid_positions)}", ephemeral=True)
                return
            position = 'D/ST' if position == 'DST' else position

        free_agents = league.free_agents(size=200)
        candidates = []
        for p in free_agents:
            if position and p.position != position:
                continue
            owned = getattr(p, 'percent_owned', 0) or 0
            proj = getattr(p, 'projected_total_points', 0) or 0
            if not (min_owned <= owned <= max_owned) or proj <= 0:
                continue
            candidates.append({'player': p, 'proj': proj, 'owned': owned})

        if not candidates:
            await interaction.followup.send(f"No available players found with current filters (ownership {min_owned}-{max_owned}%).", ephemeral=True)
            return

        candidates.sort(key=lambda c: c['proj'], reverse=True)
        top10 = candidates[:10]
        top = top10[:5]

        gem = next((c for c in top10 if c['owned'] <= 10 and c['proj'] >= 80), None)
        popular_ids = {c['player'].playerId for c in top10 if c['owned'] >= 25}

        pos_counts = {}
        for c in top10:
            pos_counts.setdefault(c['player'].position, []).append(c)
        deepest = max(pos_counts, key=lambda p: len(pos_counts[p])) if pos_counts else None
        scarcest = min(pos_counts, key=lambda p: len(pos_counts[p])) if pos_counts else None
        footer_value = None
        if deepest and scarcest and deepest != scarcest:
            footer_value = f"{deepest} deep \u00b7 {scarcest} scarce"

        images = await get_images(player_ids=[c['player'].playerId for c in top], team_abbrs=[])
        players = []
        for i, c in enumerate(top):
            p = c['player']
            tag, tag_type = None, None
            if gem and p.playerId == gem['player'].playerId:
                tag, tag_type = 'Hidden Gem', 'gem'
            elif p.playerId in popular_ids:
                tag, tag_type = 'Popular', 'popular'
            players.append({
                'rank': i + 1, 'name': p.name, 'position': p.position,
                'sub': f"{p.proTeam} \u00b7 {c['owned']:.1f}% owned",
                'headshot_path': images['players'].get(p.playerId),
                'metric1_val': f"{c['proj']:.1f}", 'metric1_label': 'proj',
                'metric2_val': None, 'metric2_label': None,
                'tag': tag, 'tag_type': tag_type,
            })

        card_data = {
            'title': 'Waiver Targets', 'league_name': get_league_name(user_id=interaction.user.id),
            'subtitle': f"{min_owned}-{max_owned}% Owned", 'header_right_val': None, 'header_right_sub': None,
            'players': players,
            'footer_label': 'Deepest / Scarcest Position' if footer_value else None, 'footer_value': footer_value,
        }
        image_buf = render_player_list_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="waiver.png"))
    except Exception as e:
        print(f"Waiver command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error analyzing waiver wire: {e}", ephemeral=True)


@client.tree.command(name="trade", description="Visual trade analysis between two teams.")
@app_commands.describe(
    team1="First team name",
    team2="Second team name",
    team1_players="Players team1 gives up (comma-separated)",
    team2_players="Players team2 gives up (comma-separated)"
)
async def trade(interaction: discord.Interaction, team1: str, team2: str, team1_players: str, team2_players: str):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        team1_obj = next((t for t in league.teams if team1.lower() in t.team_name.lower()), None)
        team2_obj = next((t for t in league.teams if team2.lower() in t.team_name.lower()), None)
        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found.")
            return
        if not team2_obj:
            await interaction.followup.send(f"Team '{team2}' not found.")
            return

        def find_player(name, team):
            return next((p for p in team.roster if name.lower() in p.name.lower()), None)

        team1_names = [n.strip() for n in team1_players.split(',')]
        team2_names = [n.strip() for n in team2_players.split(',')]

        send_players, receive_players = [], []
        for name in team1_names:
            p = find_player(name, team1_obj)
            if not p:
                await interaction.followup.send(f"Player '{name}' not found on {team1_obj.team_name}.")
                return
            send_players.append(p)
        for name in team2_names:
            p = find_player(name, team2_obj)
            if not p:
                await interaction.followup.send(f"Player '{name}' not found on {team2_obj.team_name}.")
                return
            receive_players.append(p)

        def player_row(p):
            return {'name': p.name, 'position': p.position, 'avg': p.avg_points or 0}

        send_rows = [player_row(p) for p in send_players]
        receive_rows = [player_row(p) for p in receive_players]
        send_total = sum(r['avg'] for r in send_rows)
        receive_total = sum(r['avg'] for r in receive_rows)

        diff = abs(send_total - receive_total)
        if diff <= 2:
            fairness_label = "Very Fair"
        elif diff <= 5:
            fairness_label = "Reasonably Fair"
        elif diff <= 10:
            fairness_label = "Slightly Uneven"
        else:
            fairness_label = "Significantly Uneven"

        send_positions = sorted({p.position for p in send_players})
        receive_positions = sorted({p.position for p in receive_players})
        if send_positions == receive_positions:
            trade_type = "Like-for-Like"
        else:
            trade_type = f"Position Diversification ({'/'.join(send_positions)} \u2192 {'/'.join(receive_positions)})"

        injury_notes = []
        for p in send_players + receive_players:
            status = getattr(p, 'injuryStatus', None)
            if status and status not in ('ACTIVE', 'NORMAL'):
                injury_notes.append(f"Injury Risk: {p.name} is {status.replace('_', ' ').title()}")

        logos = await get_logos_by_url([team1_obj.logo_url])
        card_data = {
            'team1_name': team1_obj.team_name, 'team2_name': team2_obj.team_name,
            'team1_logo': logos.get(team1_obj.logo_url),
            'send': send_rows, 'receive': receive_rows,
            'send_total': send_total, 'receive_total': receive_total,
            'fairness_label': fairness_label, 'trade_type': trade_type,
            'injury_notes': injury_notes,
        }
        image_buf = render_trade_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="trade.png"))
    except Exception as e:
        print(f"Trade command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error analyzing trade: {e}", ephemeral=True)

@client.tree.command(name="menu", description="Interactive command menu for easy navigation.")
async def menu(interaction: discord.Interaction):
    """Main interactive menu for bot commands"""
    embed = discord.Embed(
        title="🏈 Fantasy Football Command Center",
        description="Select a category to explore available commands",
        color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
        # Recreate main menu directly
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=EMBED_COLOR_BRAND
        )

        embed.add_field(
            name="📊 Team Analytics",
            value="View individual team performance and roster analysis",
            inline=True
        )

        embed.add_field(
            name="🎯 Strategy Tools",
            value="Waiver wire, trades, and strategic insights",
            inline=True
        )

        embed.add_field(
            name="📈 League Data",
            value="Standings, statistics, and league-wide analysis",
            inline=True
        )

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

class StrategyToolsView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Waiver Wire", emoji="🎯", style=discord.ButtonStyle.secondary)
    async def waiver_wire(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎯 Waiver Wire Command",
            description="Analyze available free agents for pickup opportunities",
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
        # Recreate main menu directly
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=EMBED_COLOR_BRAND
        )

        embed.add_field(
            name="📊 Team Analytics",
            value="View individual team performance and roster analysis",
            inline=True
        )

        embed.add_field(
            name="🎯 Strategy Tools",
            value="Waiver wire, trades, and strategic insights",
            inline=True
        )

        embed.add_field(
            name="📈 League Data",
            value="Standings, statistics, and league-wide analysis",
            inline=True
        )

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

class LeagueDataView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Standings", emoji="🏅", style=discord.ButtonStyle.success)
    async def standings(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏅 League Standings",
            description="Current league standings and records",
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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
        # Recreate main menu directly instead of calling method on new instance
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=EMBED_COLOR_BRAND
        )

        embed.add_field(
            name="📊 Team Analytics",
            value="View individual team performance and roster analysis",
            inline=True
        )

        embed.add_field(
            name="🎯 Strategy Tools",
            value="Waiver wire, trades, and strategic insights",
            inline=True
        )

        embed.add_field(
            name="📈 League Data",
            value="Standings, statistics, and league-wide analysis",
            inline=True
        )

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

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
                color=EMBED_COLOR_BRAND
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
                color=EMBED_COLOR_BRAND
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
                color=EMBED_COLOR_BRAND
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
            color=EMBED_COLOR_BRAND
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

async def _build_scoreboard_card(league, user_id):
    """Shared by /scoreboard and its Refresh button."""
    current_week = getattr(league, 'current_week', 1)
    box_scores = league.box_scores(week=current_week)

    def record_str(t):
        r = f"{t.wins}-{t.losses}"
        return r + (f"-{t.ties}" if getattr(t, 'ties', 0) else '')

    def owner_name(t):
        owners = getattr(t, 'owners', None)
        if owners:
            first = owners[0]
            return (first.get('firstName') or first.get('displayName') or 'Unknown') if isinstance(first, dict) else str(first)
        return 'Unknown'

    logo_urls = [t.logo_url for t in league.teams]
    logos = await get_logos_by_url(logo_urls)

    matchups = []
    for m in box_scores:
        if not m.home_team or not m.away_team:
            continue  # bye week
        gp = [getattr(p, 'game_played', 0) for p in m.home_lineup]
        if gp and all(v == 100 for v in gp):
            status, score1, score2 = 'final', float(m.home_score), float(m.away_score)
        elif any(v > 0 for v in gp):
            status, score1, score2 = 'live', float(m.home_score), float(m.away_score)
        else:
            status, score1, score2 = 'tbd', None, None

        win1 = None if score1 is None else score1 > score2
        matchups.append({
            'left': {'name': m.home_team.team_name, 'owner': owner_name(m.home_team), 'record': record_str(m.home_team),
                     'score': score1, 'win': win1, 'logo_path': logos.get(m.home_team.logo_url)},
            'right': {'name': m.away_team.team_name, 'owner': owner_name(m.away_team), 'record': record_str(m.away_team),
                      'score': score2, 'win': None if win1 is None else not win1, 'logo_path': logos.get(m.away_team.logo_url)},
            'status': status, 'clock': None,  # real live-game clock isn't wired up yet -- see project memory
        })

    rec_rule = next((r for r in league.settings.scoring_format if r.get('abbr') == 'REC'), None)
    rec_pts = rec_rule['points'] if rec_rule else 0
    scoring_label = "Full PPR" if rec_pts == 1 else "Half PPR" if rec_pts == 0.5 else "Standard" if rec_pts == 0 else f"{rec_pts:g} PPR"

    return {
        'league_name': get_league_name(user_id=user_id),
        'matchup_count': len(matchups), 'scoring_label': scoring_label,
        'live_games_count': sum(1 for m in matchups if m['status'] == 'live'),
        'week_label': f"Week {current_week}", 'matchups': matchups,
    }


class ScoreboardRefreshView(View):
    def __init__(self, user_id):
        super().__init__(timeout=1800)  # 30 minutes -- a scoreboard from an hour ago isn't useful to refresh
        self.user_id = user_id

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        league = get_league(user_id=self.user_id)
        card_data = await _build_scoreboard_card(league, self.user_id)
        image_buf = render_scoreboard_card(card_data)
        await interaction.edit_original_response(attachments=[discord.File(fp=image_buf, filename="scoreboard.png")], view=self)


@client.tree.command(name="scoreboard", description="Visual scoreboard for all of this week's matchups.")
async def scoreboard(interaction: discord.Interaction):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        card_data = await _build_scoreboard_card(league, interaction.user.id)
        image_buf = render_scoreboard_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="scoreboard.png"), view=ScoreboardRefreshView(interaction.user.id))
    except Exception as e:
        print(f"Scoreboard command error: {e}")
        await safe_interaction_response(interaction, f"❌ Error creating scoreboard: {e}", ephemeral=True)

@client.tree.command(name="register_league", description="Register your ESPN Fantasy League with the bot.")
@app_commands.describe(
    league_id="Your ESPN League ID (found in the URL)",
    league_name="A name for your league",
    swid="Your SWID cookie (optional, for private leagues)",
    espn_s2="Your ESPN_S2 cookie (optional, for private leagues)"
)
async def register_league(interaction: discord.Interaction, league_id: str, league_name: str, swid: str = None, espn_s2: str = None):
    """Register a user's ESPN Fantasy League"""
    try:
        await interaction.response.defer(ephemeral=True)

        # Validate league_id is numeric
        try:
            league_id_int = int(league_id)
        except ValueError:
            await interaction.followup.send("❌ League ID must be a number.", ephemeral=True)
            return

        # Register the league
        try:
            league_key = league_manager.register_league(
                user_id=interaction.user.id,
                league_name=league_name,
                league_id=league_id_int,
                swid=swid,
                espn_s2=espn_s2
            )

            embed = discord.Embed(
                title="✅ League Registered!",
                description=f"Successfully registered **{league_name}**",
                color=EMBED_COLOR_SUCCESS
            )
            embed.add_field(name="League ID", value=league_id, inline=True)
            embed.add_field(name="Status", value="Set as default league", inline=True)
            embed.add_field(name="Next Steps", value="Use `/my_leagues` to view your leagues or `/switch_league` to change default", inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as e:
            await interaction.followup.send(f"❌ Registration failed: {str(e)}", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error registering league: {str(e)}", ephemeral=True)

@client.tree.command(name="my_leagues", description="View your registered leagues.")
async def my_leagues(interaction: discord.Interaction):
    """Display user's registered leagues"""
    try:
        await interaction.response.defer(ephemeral=True)

        user_leagues = league_manager.get_user_leagues(interaction.user.id)

        if not user_leagues:
            embed = discord.Embed(
                title="📋 My Leagues",
                description="You haven't registered any leagues yet.\n\nUse `/register_league` to add your ESPN Fantasy League!",
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🏈 My Fantasy Leagues",
            description=f"**{len(user_leagues)} League{'s' if len(user_leagues) != 1 else ''} Registered**",
            color=EMBED_COLOR_BRAND
        )

        # Get user's default league
        user_data = league_manager.data['users'].get(str(interaction.user.id), {})
        default_league_key = user_data.get('default_league')

        for i, league_info in enumerate(user_leagues, 1):
            league_key = f"{league_info['league_id']}_{league_info['owner_id']}"
            is_default = league_key == default_league_key

            # League name with default indicator
            if is_default:
                league_name = f"🌟 **{league_info['name']}**"
                name_suffix = " (Default)"
            else:
                league_name = f"**{league_info['name']}**"
                name_suffix = ""

            # Privacy indicator with better formatting
            privacy_status = "🔒 Private" if league_info['swid'] and league_info['espn_s2'] else "🌐 Public"

            field_value = f"{league_name}\n"
            field_value += f"🆔 **League ID:** `{league_info['league_id']}`\n"
            field_value += f"📅 **Year:** {league_info['year']}\n"
            field_value += f"{privacy_status}"

            embed.add_field(
                name=f"{i}. League Details{name_suffix}",
                value=field_value,
                inline=len(user_leagues) <= 2  # Use inline for 1-2 leagues, full width for more
            )

        embed.add_field(
            name="💡 Tips",
            value="• Use `/switch_league` to change your default league\n• Use `/remove_league` to remove a league\n• All commands will use your default league",
            inline=False
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching leagues: {str(e)}", ephemeral=True)

@client.tree.command(name="switch_league", description="Switch your default league.")
@app_commands.describe(league_name="Name of the league to switch to")
async def switch_league(interaction: discord.Interaction, league_name: str):
    """Switch user's default league"""
    try:
        # Use safe defer
        if not await safe_defer(interaction, ephemeral=True):
            return

        user_leagues = league_manager.get_user_leagues(interaction.user.id)

        if not user_leagues:
            await interaction.followup.send("❌ You haven't registered any leagues yet. Use `/register_league` first.", ephemeral=True)
            return

        # Find the league by name
        target_league = None
        target_league_key = None
        for league_info in user_leagues:
            if league_info['name'].lower() == league_name.lower():
                target_league = league_info
                target_league_key = f"{league_info['league_id']}_{league_info['owner_id']}"
                break

        if not target_league:
            available_leagues = ", ".join([league['name'] for league in user_leagues])
            await interaction.followup.send(f"❌ League '{league_name}' not found.\n\nAvailable leagues: {available_leagues}", ephemeral=True)
            return

        # Switch to the league
        success = league_manager.set_default_league(interaction.user.id, target_league_key)

        if success:
            embed = discord.Embed(
                title="🔄 League Switched!",
                description=f"Successfully switched to **{target_league['name']}**",
                color=EMBED_COLOR_SUCCESS
            )
            embed.add_field(name="League ID", value=target_league['league_id'], inline=True)
            embed.add_field(name="Status", value="Now your default league", inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to switch league.", ephemeral=True)

    except Exception as e:
        await safe_interaction_response(interaction, f"❌ Error switching league: {str(e)}", ephemeral=True)

@client.tree.command(name="remove_league", description="Remove a league from your registered leagues.")
@app_commands.describe(league_name="Name of the league to remove")
async def remove_league(interaction: discord.Interaction, league_name: str):
    """Remove a league from user's registered leagues"""
    try:
        await interaction.response.defer(ephemeral=True)

        user_leagues = league_manager.get_user_leagues(interaction.user.id)

        if not user_leagues:
            await interaction.followup.send("❌ You haven't registered any leagues yet.", ephemeral=True)
            return

        # Find the league by name
        target_league = None
        target_league_key = None
        for league_info in user_leagues:
            if league_info['name'].lower() == league_name.lower():
                target_league = league_info
                target_league_key = f"{league_info['league_id']}_{league_info['owner_id']}"
                break

        if not target_league:
            available_leagues = ", ".join([league['name'] for league in user_leagues])
            await interaction.followup.send(f"❌ League '{league_name}' not found.\n\nAvailable leagues: {available_leagues}", ephemeral=True)
            return

        # Remove the league
        success = league_manager.remove_league(interaction.user.id, target_league_key)

        if success:
            embed = discord.Embed(
                title="🗑️ League Removed!",
                description=f"Successfully removed **{target_league['name']}**",
                color=EMBED_COLOR_SUCCESS
            )

            remaining_leagues = league_manager.get_user_leagues(interaction.user.id)
            if remaining_leagues:
                embed.add_field(name="Default League", value=f"Now using: **{remaining_leagues[0]['name']}**", inline=False)
            else:
                embed.add_field(name="No Leagues", value="You have no registered leagues. Use `/register_league` to add one.", inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to remove league.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error removing league: {str(e)}", ephemeral=True)

@client.tree.command(name="league_status", description="Show your current default league and bot status.")
async def league_status(interaction: discord.Interaction):
    """Show current league status for the user"""
    try:
        await interaction.response.defer(ephemeral=True)

        # Get user's league info
        user_leagues = league_manager.get_user_leagues(interaction.user.id)
        user_data = league_manager.data['users'].get(str(interaction.user.id), {})
        default_league_key = user_data.get('default_league')

        embed = discord.Embed(
            title="🏈 League Status",
            color=EMBED_COLOR_BRAND
        )

        if not user_leagues:
            embed.description = "❌ **No leagues registered**\n\nUse `/register_league` to add your ESPN Fantasy League!"
            embed.add_field(
                name="📋 Available Commands",
                value="• `/register_league` - Add your league\n• `/my_leagues` - View your leagues\n• `/help` - Get help",
                inline=False
            )
        else:
            # Find default league info
            default_league_info = None
            if default_league_key:
                for league_info in user_leagues:
                    league_key = f"{league_info['league_id']}_{league_info['owner_id']}"
                    if league_key == default_league_key:
                        default_league_info = league_info
                        break

            if default_league_info:
                embed.description = f"✅ **Active League:** {default_league_info['name']}"
                embed.add_field(name="League ID", value=default_league_info['league_id'], inline=True)
                embed.add_field(name="Year", value=default_league_info['year'], inline=True)

                privacy_status = "🔒 Private" if default_league_info['swid'] and default_league_info['espn_s2'] else "🌐 Public"
                embed.add_field(name="Privacy", value=privacy_status, inline=True)

                # Test league connection
                try:
                    test_league = league_manager.get_league_connection(interaction.user.id)
                    if test_league:
                        embed.add_field(name="Connection", value="✅ Connected", inline=True)
                        embed.add_field(name="Teams", value=f"{len(test_league.teams)} teams", inline=True)
                        current_week = getattr(test_league, 'current_week', 'N/A')
                        embed.add_field(name="Current Week", value=current_week, inline=True)
                    else:
                        embed.add_field(name="Connection", value="❌ Failed to connect", inline=True)
                except Exception:
                    embed.add_field(name="Connection", value="❌ Connection error", inline=True)

                embed.add_field(
                    name="📋 Quick Commands",
                    value="• `/team <name>` - View team roster\n• `/standings` - League standings\n• `/switch_league` - Change active league",
                    inline=False
                )
            else:
                embed.description = "⚠️ **Default league not found**"

            embed.add_field(
                name="📊 Your Leagues",
                value=f"Total registered: **{len(user_leagues)}**\nUse `/my_leagues` to see all",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error checking league status: {str(e)}", ephemeral=True)

@client.tree.command(name="all_leagues", description="View all available leagues in the server.")
async def all_leagues(interaction: discord.Interaction):
    """Display all leagues available to everyone"""
    try:
        await interaction.response.defer()

        all_leagues = league_manager.get_all_leagues()

        if not all_leagues:
            embed = discord.Embed(
                title="📋 All Available Leagues",
                description="No leagues have been registered yet.\n\nAsk users to register their leagues with `/register_league`!",
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="🌐 All Available Leagues",
            description=f"**{len(all_leagues)} League{'s' if len(all_leagues) != 1 else ''} Available** for cross-league commands",
            color=EMBED_COLOR_BRAND
        )

        for i, league_info in enumerate(all_leagues, 1):
            # Get owner's username if possible
            owner_name = None
            try:
                owner = interaction.guild.get_member(int(league_info['owner_id']))
                if owner:
                    owner_name = owner.display_name
            except:
                pass

            # Privacy indicator
            privacy_status = "🔒 Private" if league_info.get('swid') and league_info.get('espn_s2') else "🌐 Public"

            field_value = f"🏈 **{league_info['name']}**\n"
            field_value += f"🆔 **League ID:** `{league_info['league_id']}`\n"
            field_value += f"📅 **Year:** {league_info['year']}\n"
            field_value += f"{privacy_status}"

            # Only show "Registered by" if we have a meaningful name
            if owner_name:
                field_value += f"\n👤 **Registered by:** {owner_name}"

            embed.add_field(
                name=f"{i}. League Details",
                value=field_value,
                inline=len(all_leagues) <= 2  # Use inline for 1-2 leagues, full width for more
            )

        embed.add_field(
            name="💡 How to Use",
            value="• Use league names in commands like `/compare_cross_league`\n• Everyone can access these leagues for comparisons\n• Private league credentials are securely stored",
            inline=False
        )

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching leagues: {str(e)}")

@client.tree.command(name="compare_cross_league", description="Visual comparison of teams from different leagues.")
@app_commands.describe(
    team1="First team name",
    league1="League name for first team (optional, uses your default)",
    team2="Second team name",
    league2="League name for second team (optional, uses your default)"
)
async def compare_cross_league(interaction: discord.Interaction, team1: str, team2: str, league1: str = None, league2: str = None):
    if not await safe_defer(interaction):
        return

    try:
        if league1:
            league1_matches = league_manager.find_leagues_by_name(league1)
            if not league1_matches:
                available_names = [l['name'] for l in league_manager.get_all_leagues()]
                await interaction.followup.send(f"\u274c League '{league1}' not found.\n\nAvailable leagues: {', '.join(available_names)}")
                return
            league1_obj = league_manager.get_league_by_key(league1_matches[0]['key'])
            if not league1_obj:
                await interaction.followup.send(f"\u274c Failed to connect to league '{league1_matches[0]['name']}'.")
                return
            league1_name = league1_matches[0]['name']
        else:
            league1_obj = get_league(user_id=interaction.user.id)
            if not league1_obj:
                await interaction.followup.send("\u274c No default league found. Register a league or specify league1 parameter.")
                return
            user_data = league_manager.data['users'].get(str(interaction.user.id), {})
            default_league_key = user_data.get('default_league')
            league1_name = league_manager.data['leagues'][default_league_key]['name'] if default_league_key in league_manager.data.get('leagues', {}) else "Your League"

        if league2:
            league2_matches = league_manager.find_leagues_by_name(league2)
            if not league2_matches:
                await interaction.followup.send(f"\u274c League '{league2}' not found. Use `/all_leagues` to see available leagues.")
                return
            league2_obj = league_manager.get_league_by_key(league2_matches[0]['key'])
            league2_name = league2_matches[0]['name']
        else:
            league2_obj, league2_name = league1_obj, league1_name

        if not league1_obj or not league2_obj:
            await interaction.followup.send("\u274c Failed to connect to one or both leagues.")
            return

        team1_obj = next((t for t in league1_obj.teams if t.team_name.lower() == team1.lower()), None)
        team2_obj = next((t for t in league2_obj.teams if t.team_name.lower() == team2.lower()), None)
        if not team1_obj:
            await interaction.followup.send(f"\u274c Team '{team1}' not found in {league1_name}.")
            return
        if not team2_obj:
            await interaction.followup.send(f"\u274c Team '{team2}' not found in {league2_name}.")
            return

        # _build_compare_card's this-week-proj math needs a single league's
        # current_week; league1's is used for both sides, which only matters
        # (rarely) if the two leagues are on different week numbers.
        card_data = await _build_compare_card(league1_obj, team1_obj, team2_obj, interaction.user.id,
                                               league1_name=league1_name, league2_name=league2_name)
        image_buf = render_compare_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="compare.png"))
    except Exception as e:
        print(f"Compare cross-league command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error comparing teams: {e}", ephemeral=True)


@client.tree.command(name="league_info", description="Display detailed league settings and configuration.")
async def league_info(interaction: discord.Interaction):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        settings = getattr(league, 'settings', None)
        current_week = getattr(league, 'current_week', 1)

        scoring_format = "Standard (No PPR)"
        rec_pts = 0.0
        passing_td_pts = None
        receiving_td_pts = None
        rules = getattr(settings, 'scoring_format', None) if settings else None
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                abbr = rule.get('abbr')
                points = rule.get('points', 0)
                if abbr == 'REC':
                    rec_pts = points
                    if points == 1.0:
                        scoring_format = "Full PPR"
                    elif points == 0.5:
                        scoring_format = "Half PPR"
                    elif points == 0:
                        scoring_format = "Standard (No PPR)"
                    else:
                        scoring_format = f"Custom PPR ({points} pts)"
                elif abbr == 'PTD':
                    passing_td_pts = points
                elif abbr == 'RETD':
                    receiving_td_pts = points

        reg_season_count = getattr(settings, 'reg_season_count', None) if settings else None
        playoff_teams = getattr(settings, 'playoff_team_count', None) if settings else None
        matchup_periods = getattr(settings, 'matchup_periods', None) if settings else None
        if playoff_teams and reg_season_count:
            playoff_start = reg_season_count + 1
            last_week = max((int(k) for k in matchup_periods.keys()), default=playoff_start) if isinstance(matchup_periods, dict) else playoff_start
            playoff_value = f"{playoff_teams} (Weeks {playoff_start}-{last_week})"
        elif playoff_teams:
            playoff_value = f"{playoff_teams} Teams"
        else:
            playoff_value = "TBD"

        total_points = sum(getattr(t, 'points_for', 0) for t in league.teams)
        top_team = max(league.teams, key=lambda t: getattr(t, 'points_for', 0))

        info_rows = [{'label': 'Scoring Format', 'value': scoring_format}]
        if playoff_value != "TBD":
            info_rows.append({'label': 'Playoff Teams', 'value': playoff_value})
        if reg_season_count:
            info_rows.append({'label': 'Regular Season', 'value': f"{reg_season_count} Weeks"})
        if passing_td_pts is not None and receiving_td_pts is not None:
            info_rows.append({'label': 'TD Pass / Reception', 'value': f"{passing_td_pts:g} pts / {receiving_td_pts:g} pts"})
        info_rows.append({'label': 'Reception', 'value': f"{rec_pts:g} pt"})
        info_rows.append({'label': 'League Total Points', 'value': f"{total_points:,.1f}"})
        info_rows.append({'label': 'Top Scoring Team', 'value': f"{top_team.team_name} \u00b7 {top_team.points_for:.1f}"})

        roster_composition = []
        slot_counts = getattr(settings, 'position_slot_counts', None) if settings else None
        if isinstance(slot_counts, dict):
            order = ['QB', 'RB', 'WR', 'TE', 'RB/WR/TE', 'RB/WR', 'WR/TE', 'OP', 'D/ST', 'DST', 'K', 'BE', 'IR']
            seen = set(order)
            full_order = order + [k for k in slot_counts if k not in seen]
            for slot in full_order:
                count = slot_counts.get(slot, 0)
                if not count or slot == 'IR':
                    continue
                label = 'FLEX (RB/WR/TE)' if slot == 'RB/WR/TE' else slot
                label = 'BE' if slot == 'BE' else label
                roster_composition.append(f"{count} {label}")

        card_data = {
            'league_name': get_league_name(user_id=interaction.user.id),
            'team_count': len(league.teams), 'season': league.year, 'week_label': f"Week {current_week}",
            'info_rows': info_rows, 'roster_composition': roster_composition,
        }
        image_buf = render_league_info_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="league_info.png"))
    except Exception as e:
        print(f"League info error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error getting league info: {e}", ephemeral=True)

@client.tree.command(name="insights", description="League pulse -- who's hot, who's cold, by season PPG.")
async def insights(interaction: discord.Interaction):
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "\u274c No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        current_week = getattr(league, 'current_week', 1)
        teams_data = []
        for team in league.teams:
            games = team.wins + team.losses
            ppg = team.points_for / games if games else 0
            teams_data.append({'name': team.team_name, 'ppg': ppg, 'total_points': team.points_for,
                                'wins': team.wins, 'losses': team.losses})

        league_avg_ppg = sum(t['ppg'] for t in teams_data) / len(teams_data)
        sorted_teams = sorted(teams_data, key=lambda t: t['ppg'], reverse=True)
        hot = [{'name': t['name'], 'ppg': t['ppg'], 'diff': t['ppg'] - league_avg_ppg} for t in sorted_teams[:3]]
        cold = [{'name': t['name'], 'ppg': t['ppg'], 'diff': t['ppg'] - league_avg_ppg} for t in sorted_teams[-3:]]

        season_leader = max(teams_data, key=lambda t: t['total_points'])

        card_data = {
            'league_name': get_league_name(user_id=interaction.user.id), 'week_label': f"Week {current_week}",
            'league_avg_ppg': league_avg_ppg, 'hot': hot, 'cold': cold,
            'footer_label': 'Season Points Leader', 'footer_value': f"{season_leader['name']} \u00b7 {season_leader['total_points']:.1f}",
        }
        image_buf = render_league_pulse_card(card_data)
        await interaction.followup.send(file=discord.File(fp=image_buf, filename="insights.png"))
    except Exception as e:
        print(f"Insights command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error generating insights: {e}", ephemeral=True)


@client.tree.command(name="welcome", description="Get started guide for using the Fantasy Football bot.")
async def welcome(interaction: discord.Interaction):
    """Comprehensive welcome and setup guide"""
    embed = discord.Embed(
        title="🏈 Welcome to Fantasy Football Bot!",
        description="**Your complete guide to dominating fantasy football with data-driven insights**",
        color=EMBED_COLOR_BRAND
    )

    # Quick Start Section
    embed.add_field(
        name="🚀 Quick Start (New Users)",
        value="**1.** Run `/register_league` with your ESPN League ID\n"
              "**2.** Try `/scoreboard` to see live scores\n"
              "**3.** Use `/menu` to explore all features\n"
              "**4.** Check out `/league_info` for your league details",
        inline=False
    )

    # Add spacing
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Finding League ID
    embed.add_field(
        name="🔍 How to Find Your ESPN League ID",
        value="**1.** Go to your ESPN Fantasy Football league\n"
              "**2.** Look at the URL: `fantasy.espn.com/football/league?leagueId=XXXXXX`\n"
              "**3.** Copy the numbers after `leagueId=`\n"
              "**4.** That's your League ID!",
        inline=True
    )

    # Private Leagues
    embed.add_field(
        name="🔒 Private Leagues",
        value="**Need SWID & ESPN_S2 cookies:**\n"
              "• Log into ESPN in your browser\n"
              "• Open Developer Tools (F12)\n"
              "• Go to Application → Cookies\n"
              "• Find `SWID` and `espn_s2` values\n"
              "• Use them in `/register_league`",
        inline=True
    )

    # Add spacing
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Popular Commands
    embed.add_field(
        name="⭐ Most Popular Commands",
        value="🏆 `/scoreboard` - Live weekly scores\n"
              "📊 `/standings` - League standings\n"
              "👥 `/team [name]` - Team roster & stats\n"
              "🔍 `/player [name]` - Player details\n"
              "⚔️ `/compare [team1] [team2]` - Team comparison\n"
              "📈 `/stats` - League analytics",
        inline=True
    )

    # Advanced Features
    embed.add_field(
        name="🎯 Advanced Features",
        value="🔄 `/trade` - Trade analyzer\n"
              "💎 `/sleeper` - Sleeper pick finder\n"
              "📋 `/waiver` - Waiver wire analysis\n"
              "🆚 `/matchup` - Weekly matchup preview\n"
              "📱 `/team` - Visual team roster card\n"
              "🌐 `/compare_cross_league` - Cross-league comparison",
        inline=True
    )

    # Add spacing
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Multiple Leagues
    embed.add_field(
        name="🔗 Multiple Leagues",
        value="• Register multiple leagues with `/register_league`\n"
              "• View all your leagues: `/my_leagues`\n"
              "• Switch active league: `/switch_league`\n"
              "• Remove leagues: `/remove_league`\n"
              "• Check current status: `/league_status`",
        inline=False
    )

    # Support
    embed.add_field(
        name="❓ Need Help?",
        value="• Use `/menu` for interactive command explorer\n"
              "• Run `/help` for quick command reference\n"
              "• All commands work with your registered league automatically\n"
              "• Bot updates live scores every 30 seconds during games",
        inline=False
    )

    embed.set_footer(text="💡 Pro tip: Pin this message for easy reference! Use /menu to explore all features.")

    await interaction.response.send_message(embed=embed)

@client.tree.command(name="help", description="Quick command reference and help.")
async def help_command(interaction: discord.Interaction):
    """Quick help and command reference"""
    embed = discord.Embed(
        title="🆘 Fantasy Football Bot Help",
        description="**Quick command reference - Use `/welcome` for the full setup guide**",
        color=EMBED_COLOR_BRAND
    )

    # Getting Started
    embed.add_field(
        name="🏁 Getting Started",
        value="**New users:** Run `/welcome` for complete setup guide\n"
              "**Register league:** `/register_league [league_id] [name]`\n"
              "**Need help finding League ID?** Check `/welcome`",
        inline=False
    )

    # Core Commands
    embed.add_field(
        name="📊 Core Commands",
        value="`/scoreboard` - Live scores & matchups\n"
              "`/standings` - League standings\n"
              "`/team [name]` - Team roster\n"
              "`/player [name]` - Player stats\n"
              "`/league_info` - League settings",
        inline=True
    )

    # Analysis Tools
    embed.add_field(
        name="🔍 Analysis Tools",
        value="`/compare [team1] [team2]` - Compare teams\n"
              "`/stats` - League analytics\n"
              "`/matchup` - Weekly preview\n"
              "`/trade` - Trade analyzer\n"
              "`/waiver` - Waiver recommendations",
        inline=True
    )

    # League Management
    embed.add_field(
        name="⚙️ League Management",
        value="`/my_leagues` - Your registered leagues\n"
              "`/switch_league [name]` - Change active league\n"
              "`/league_status` - Current settings\n"
              "`/all_leagues` - Available leagues\n"
              "`/menu` - Interactive command explorer",
        inline=False
    )

    embed.set_footer(text="💡 Use /welcome for detailed setup instructions and finding your ESPN League ID")

    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == '__main__':
    import time
    import traceback

    max_restarts = 5
    restart_count = 0

    while restart_count < max_restarts:
        try:
            print(f"Attempting to connect to Discord... (Attempt {restart_count + 1}/{max_restarts})")
            client.run(TOKEN)
        except KeyboardInterrupt:
            print("Bot stopped by user.")
            break
        except discord.errors.LoginFailure:
            print("Invalid Discord token. Bot cannot start.")
            break
        except Exception as e:
            restart_count += 1
            print(f"Bot crashed: {e}")
            traceback.print_exc()

            if restart_count < max_restarts:
                wait_time = min(30 * restart_count, 300)  # Wait 30s, 60s, 90s, up to 5min
                print(f"Restarting in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("Maximum restart attempts reached. Bot shutting down.")
                break
