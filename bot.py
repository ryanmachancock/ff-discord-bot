print("Starting Fantasy Football bot...")

import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Select
from dotenv import load_dotenv
from espn_api.football import League
from tabulate import tabulate
import json
import time
from typing import Dict, Any, Optional

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
            if hasattr(globals(), 'LEAGUE_ID') and LEAGUE_ID:
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
        self.color = 0x0099ff
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
        """Set embed color"""
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

class PaginatedView(discord.ui.View):
    """Reusable pagination view for standings and other large lists"""

    def __init__(self, data, page_size=8, embed_generator=None):
        super().__init__(timeout=300)
        self.data = data
        self.page_size = page_size
        self.embed_generator = embed_generator
        self.current_page = 0
        self.max_pages = (len(data) - 1) // page_size + 1

        # Disable buttons if only one page
        if self.max_pages <= 1:
            self.previous_button.disabled = True
            self.next_button.disabled = True
            self.previous_button.style = discord.ButtonStyle.secondary
            self.next_button.style = discord.ButtonStyle.secondary

    def get_page_data(self, page_num):
        """Get data for specific page"""
        start = page_num * self.page_size
        end = start + self.page_size
        return self.data[start:end]

    def update_buttons(self):
        """Update button states based on current page"""
        self.previous_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.max_pages - 1

    @discord.ui.button(label='⬅️ Previous', style=discord.ButtonStyle.primary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            embed = self.embed_generator(self.current_page, self.get_page_data(self.current_page))
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label='Next ➡️', style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages - 1:
            self.current_page += 1
            self.update_buttons()
            embed = self.embed_generator(self.current_page, self.get_page_data(self.current_page))
            await interaction.response.edit_message(embed=embed, view=self)

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

async def handle_command_error(interaction, error, command_name="command"):
    """Consistent error handling for Discord commands"""
    error_message = f"❌ Error executing {command_name}: {str(error)[:100]}"

    if isinstance(error, ConnectionError):
        error_message = "🌐 Unable to connect to ESPN Fantasy API. Please try again later."
    elif isinstance(error, ValueError):
        error_message = f"⚠️ Invalid input: {str(error)[:100]}"
    elif "timeout" in str(error).lower():
        error_message = "⏱️ Request timed out. ESPN servers may be slow. Please try again."

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(error_message, ephemeral=True)
        else:
            await interaction.followup.send(error_message, ephemeral=True)
    except Exception:
        # Fallback if Discord interaction fails
        print(f"Failed to send error message: {error_message}")

def command_error_handler(func):
    """Decorator for consistent command error handling"""
    async def wrapper(interaction, *args, **kwargs):
        try:
            return await func(interaction, *args, **kwargs)
        except Exception as e:
            await handle_command_error(interaction, e, func.__name__)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper

@client.event
async def on_ready():
    print(f'Bot is ready! Commands should be synced via setup_hook.')

    # Start background refresh for better performance
    background_refresh_manager.start_background_refresh()

@client.tree.command(name="sync_commands", description="Manually sync bot commands with Discord (admin only).")
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

@client.tree.command(name="debug_autocomplete", description="Test autocomplete functionality")
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

@client.tree.command(name="detailed_stats", description="Comprehensive league analytics with advanced metrics (desktop optimized).")
async def detailed_stats(interaction: discord.Interaction):
    """Desktop-optimized detailed statistics command"""
    try:
        await interaction.response.defer()

        league = get_league(user_id=interaction.user.id)
        if not league:
            await interaction.followup.send("❌ No league found. Use `/register_league` to add your ESPN Fantasy League first.", ephemeral=True)
            return

        current_week = getattr(league, 'current_week', 1)
        league_name = get_league_name(user_id=interaction.user.id)

        # Create comprehensive analytics embed
        embed = SafeEmbedBuilder.create()
        embed.set_title(f"📊 {league_name} - Advanced Analytics (Week {current_week})")
        embed.set_color(0x4CAF50)

        # Collect detailed team data
        teams_analysis = []
        for team in league.teams:
            # Calculate advanced metrics
            total_points = getattr(team, 'points_for', 0)
            games_played = getattr(team, 'wins', 0) + getattr(team, 'losses', 0)
            ppg = total_points / max(games_played, 1)

            # Get current week projection
            starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
            week_projection = 0
            for player in starters:
                proj = 0
                if hasattr(player, 'stats') and player.stats:
                    week_stats = player.stats.get(current_week, {})
                    proj = week_stats.get('projected_points', 0) or 0
                week_projection += proj

            teams_analysis.append({
                'name': team.team_name,
                'points_for': total_points,
                'points_against': getattr(team, 'points_against', 0),
                'wins': getattr(team, 'wins', 0),
                'losses': getattr(team, 'losses', 0),
                'ppg': ppg,
                'week_proj': week_projection
            })

        # Sort by total points
        teams_analysis.sort(key=lambda x: x['points_for'], reverse=True)

        # League overview
        total_points = sum(t['points_for'] for t in teams_analysis)
        avg_ppg = total_points / len(teams_analysis) / max(teams_analysis[0]['wins'] + teams_analysis[0]['losses'], 1)

        overview_text = f"**League Totals:** {total_points:.1f} points across {len(teams_analysis)} teams\n"
        overview_text += f"**Average PPG:** {avg_ppg:.1f} per team\n"
        overview_text += f"**Season Progress:** {games_played} games played"

        embed.add_field(name="🎯 League Overview", value=overview_text, inline=False)

        # Top performers analysis
        top_3_points = teams_analysis[:3]
        top_performers_text = ""
        for i, team in enumerate(top_3_points):
            rank_emoji = ["🥇", "🥈", "🥉"][i]
            efficiency = team['points_for'] / max(team['points_against'], 1)
            top_performers_text += f"{rank_emoji} **{team['name']}** - {team['points_for']:.1f} PF ({team['ppg']:.1f} PPG) • Efficiency: {efficiency:.2f}\n"

        embed.add_field(name="🏆 Points Leaders", value=top_performers_text, inline=True)

        # Efficiency leaders (points for vs points against ratio)
        efficiency_leaders = sorted(teams_analysis, key=lambda x: x['points_for'] / max(x['points_against'], 1), reverse=True)[:3]
        efficiency_text = ""
        for i, team in enumerate(efficiency_leaders):
            rank_emoji = ["🎯", "📈", "⚡"][i]
            ratio = team['points_for'] / max(team['points_against'], 1)
            efficiency_text += f"{rank_emoji} **{team['name']}** - {ratio:.2f} ratio ({team['points_for']:.1f}/{team['points_against']:.1f})\n"

        embed.add_field(name="⚡ Efficiency Leaders", value=efficiency_text, inline=True)

        # Weekly projections
        proj_leaders = sorted(teams_analysis, key=lambda x: x['week_proj'], reverse=True)[:3]
        proj_text = ""
        for i, team in enumerate(proj_leaders):
            rank_emoji = ["🔮", "✨", "💫"][i]
            proj_text += f"{rank_emoji} **{team['name']}** - {team['week_proj']:.1f} projected\n"

        embed.add_field(name=f"🔮 Week {current_week} Projections", value=proj_text, inline=False)

        # Power rankings (combination of record and points)
        power_rankings = []
        for team in teams_analysis:
            win_pct = team['wins'] / max(team['wins'] + team['losses'], 1)
            power_score = (win_pct * 0.6) + ((team['ppg'] / avg_ppg) * 0.4)  # 60% record, 40% scoring
            power_rankings.append((team['name'], power_score, team['wins'], team['losses']))

        power_rankings.sort(key=lambda x: x[1], reverse=True)

        power_text = ""
        for i, (name, score, wins, losses) in enumerate(power_rankings[:5]):
            rank_num = i + 1
            power_text += f"**{rank_num}.** {name} ({wins}-{losses}) - Power: {score:.3f}\n"

        embed.add_field(name="⚔️ Power Rankings", value=power_text, inline=False)

        embed.set_footer(text=f"📊 Advanced analytics • Updated Week {current_week} • Use /standings for simple view")

        await interaction.followup.send(embed=embed.build())

    except Exception as e:
        error_msg = f"❌ Error generating detailed stats: {str(e)[:100]}"
        print(f"Detailed stats error: {e}")
        await interaction.followup.send(error_msg, ephemeral=True)

@client.tree.command(name="test_new", description="Simple test command to verify new commands work.")
async def test_new(interaction: discord.Interaction):
    """Simple test command"""
    await interaction.response.send_message("✅ New commands are working! This means the bot code is updated and syncing properly.", ephemeral=True)

@client.tree.command(name="ping", description="Check if the bot is alive.")
async def ping(interaction: discord.Interaction):
    import time
    start_time = time.time()

    embed = discord.Embed(
        title="🏓 Pong!",
        description="**Bot is online and responding**",
        color=0x00ff00
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


@client.tree.command(name="team", description="Get the roster for a team by name.")
@app_commands.describe(team_name="The exact name of the team as it appears in ESPN.")
@app_commands.autocomplete(team_name=team_name_autocomplete)
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
            league = get_league(user_id=interaction.user.id)
            if not league:
                await interaction.followup.send("❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
                return
        except Exception as api_error:
            error_message = "⚠️ **ESPN API Issue**\n"
            if "timeout" in str(api_error).lower():
                error_message += "ESPN servers are slow. Try again in a moment."
            elif "401" in str(api_error) or "authentication" in str(api_error).lower():
                error_message += "League authentication failed. Check your league settings with `/league_status`."
            elif "404" in str(api_error):
                error_message += "League not found. Verify your league ID is correct."
            else:
                error_message += f"ESPN temporarily unavailable. Please try again.\n*Error: {str(api_error)[:100]}*"
            await interaction.followup.send(error_message)
            return
        team = next((t for t in league.teams if t.team_name.lower() == team_name.lower()), None)
        if not team:
            # Try partial match
            partial_matches = [t for t in league.teams if team_name.lower() in t.team_name.lower()]
            if partial_matches:
                suggestions = ", ".join([f"'{t.team_name}'" for t in partial_matches[:3]])
                await interaction.followup.send(f"❌ Team '{team_name}' not found.\n💡 Did you mean: {suggestions}?")
            else:
                team_list = ", ".join([f"'{t.team_name}'" for t in league.teams[:5]])
                await interaction.followup.send(f"❌ Team '{team_name}' not found.\n📋 Available teams: {team_list}...")
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

        def get_weekly_proj(player):
            """Get projected points for current week only"""
            current_week = getattr(league, 'current_week', 1)

            # Try to get current week projected points from stats
            if hasattr(player, 'stats') and player.stats:
                try:
                    week_stats = player.stats.get(current_week, {})
                    projected_points = week_stats.get('projected_points', None)
                    if projected_points is not None:
                        return projected_points
                except:
                    pass

            # Fallback: try to get weekly projection from player attributes
            try:
                # Some ESPN API versions store weekly projections differently
                if hasattr(player, 'projected_points') and hasattr(player, 'total_points'):
                    # If total_points exists, projected_points might be weekly
                    weekly_proj = getattr(player, 'projected_points', None)
                    if weekly_proj and weekly_proj < 50:  # Reasonable weekly max
                        return weekly_proj

                # Try alternative attribute names for weekly projections
                weekly_attrs = ['proj_points', 'projected_week_points', 'week_projected_points']
                for attr in weekly_attrs:
                    value = getattr(player, attr, None)
                    if value is not None:
                        return value

            except:
                pass

            return 'N/A'
        def get_status(player):
            # Don't show status for D/ST positions
            pos = getattr(player, 'position', '')
            if pos in ['D/ST', 'DST', 'DEF']:
                return ''

            status = getattr(player, 'injuryStatus', None)
            abbrev = status_abbrev.get(status, status_abbrev.get('NORMAL', ''))

            # Don't show status for Available players (A or N)
            if abbrev in ['A', 'N']:
                return ''

            return abbrev
        def get_actual_points(player):
            """Get actual points for current week"""
            current_week = getattr(league, 'current_week', 1)
            if hasattr(player, 'stats') and player.stats:
                try:
                    week_stats = player.stats.get(current_week, {})
                    actual_points = week_stats.get('points', None)
                    if actual_points is not None and actual_points > 0:
                        return actual_points
                    # Check applied stats for actual game performance
                    applied_stats = week_stats.get('appliedStats', {})
                    if applied_stats and len(applied_stats) > 0:
                        total_points = 0
                        for stat_id, value in applied_stats.items():
                            if isinstance(value, (int, float)) and value > 0:
                                total_points += value
                        if total_points > 0:
                            return total_points
                except:
                    pass
            return 0

        def player_row(player):
            pos = getattr(player, 'position', 'UNK')
            status = get_status(player)
            # Put status in parentheses after name
            name_with_status = f"{pos} {player.name} ({status})" if status else f"{pos} {player.name}"

            actual = get_actual_points(player)
            projected = get_proj(player)

            # Format actual points
            if actual == 0:
                actual_str = "0.0"
            else:
                actual_str = f"{float(actual):.1f}"

            # Format projected points
            if projected == 'N/A' or projected is None:
                proj_str = "N/A"
            else:
                proj_str = f"{float(projected):.1f}"

            return [name_with_status, f"{actual_str} pts", f"{proj_str} pts"]
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

        # Calculate total starter points (actual and projected)
        total_actual_points = sum(get_actual_points(p) for p in starters_sorted)
        total_projected_points = sum(float(get_weekly_proj(p)) for p in starters_sorted if get_weekly_proj(p) != 'N/A' and get_weekly_proj(p) is not None)

        # Create mobile-friendly starter display
        starter_lines = []
        for p in starters_sorted:
            pos = getattr(p, 'position', 'UNK')
            status = get_status(p)

            actual = get_actual_points(p)
            projected = get_weekly_proj(p)

            # Format projected points
            if projected == 'N/A' or projected is None:
                proj_str = "N/A"
            else:
                proj_str = f"{float(projected):.1f}"

            # Format actual points
            actual_str = f"{actual:.1f}" if actual > 0 else "0.0"

            # Enhanced position and status indicators
            pos_emojis = {
                'QB': '🏈', 'RB': '🏃‍♂️', 'WR': '🙌', 'TE': '🏈',
                'K': '🦶', 'D/ST': '🛡️', 'DST': '🛡️', 'DEF': '🛡️'
            }
            pos_emoji = pos_emojis.get(pos, '⚪')

            # Enhanced status indicator with emoji
            status_indicators = {
                'Q': '⚠️', 'QUESTIONABLE': '⚠️',
                'O': '❌', 'OUT': '❌',
                'D': '🔶', 'DOUBTFUL': '🔶',
                'IR': '🏥', 'INJURY_RESERVE': '🏥',
                'COV': '😷', 'COVID': '😷'
            }
            status_emoji = status_indicators.get(status, '')
            status_indicator = f" {status_emoji}" if status and status not in ['ACTIVE', 'N/A', ''] else ""

            # Enhanced format with emoji and better spacing
            line = f"{pos_emoji} **{pos}** {p.name}{status_indicator} `{proj_str}/{actual_str}`"
            starter_lines.append(line)

        starter_lines.append(f"\n**Total:** `{total_projected_points:.1f}/{total_actual_points:.1f}` pts")
        starters_text = "\n".join(starter_lines) if starters_sorted else "None"
        # Create mobile-friendly bench display
        if bench:
            bench_lines = []
            for p in bench:
                pos = getattr(p, 'position', 'UNK')
                status = get_status(p)

                actual = get_actual_points(p)
                projected = get_weekly_proj(p)

                # Format projected points
                if projected == 'N/A' or projected is None:
                    proj_str = "N/A"
                else:
                    proj_str = f"{float(projected):.1f}"

                # Format actual points
                actual_str = f"{actual:.1f}" if actual > 0 else "0.0"

                # Enhanced position and status indicators (same as starters)
                pos_emojis = {
                    'QB': '🏈', 'RB': '🏃‍♂️', 'WR': '🙌', 'TE': '🏈',
                    'K': '🦶', 'D/ST': '🛡️', 'DST': '🛡️', 'DEF': '🛡️'
                }
                pos_emoji = pos_emojis.get(pos, '⚪')

                # Enhanced status indicator with emoji
                status_indicators = {
                    'Q': '⚠️', 'QUESTIONABLE': '⚠️',
                    'O': '❌', 'OUT': '❌',
                    'D': '🔶', 'DOUBTFUL': '🔶',
                    'IR': '🏥', 'INJURY_RESERVE': '🏥',
                    'COV': '😷', 'COVID': '😷'
                }
                status_emoji = status_indicators.get(status, '')
                status_indicator = f" {status_emoji}" if status and status not in ['ACTIVE', 'N/A', ''] else ""

                # Enhanced format with emoji and better spacing
                line = f"{pos_emoji} **{pos}** {p.name}{status_indicator} `{proj_str}/{actual_str}`"
                bench_lines.append(line)

            bench_text = "\n".join(bench_lines)
        else:
            bench_text = "None"
        # Get current week
        current_week = getattr(league, 'current_week', 'Unknown')
        league_name = get_league_name(user_id=interaction.user.id)
        embed = discord.Embed(title=f"🏈 {team.team_name} ({league_name}) - Week {current_week}", color=discord.Color.blue())
        if hasattr(team, 'logo_url') and team.logo_url:
            embed.set_thumbnail(url=team.logo_url)
        else:
            embed.set_thumbnail(url="https://a.espncdn.com/i/espn/logos/nfl/NFL.png")
        embed.add_field(name="🚀 Starting Lineup", value=starters_text, inline=False)
        embed.add_field(name="🪑 Bench Players", value=bench_text, inline=False)
        
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
@app_commands.autocomplete(player_name=player_name_autocomplete)
async def player(interaction: discord.Interaction, player_name: str):
    try:
        await interaction.response.defer()
        # Use cached league for better performance
        league = get_league(user_id=interaction.user.id)
        if not league:
            if SWID and ESPN_S2:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
            else:
                league = League(league_id=LEAGUE_ID, year=SEASON_ID)
        # Search for player across all teams (optimized)
        found_player = None
        player_team = None
        player_name_lower = player_name.lower()  # Cache the lowercase conversion

        for team in league.teams:
            # Use next() with generator for early exit optimization
            found = next((p for p in team.roster if player_name_lower in p.name.lower()), None)
            if found:
                found_player = found
                player_team = team
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
        # Create detailed embed with improved formatting
        league_name = get_league_name(user_id=interaction.user.id)
        current_week = getattr(league, 'current_week', 'Unknown')

        embed = discord.Embed(
            title=f"🏈 {found_player.name}",
            description=f"**{league_name} - Week {current_week}**",
            color=0x0099ff
        )

        # Position and Team info
        embed.add_field(
            name="🎯 Position",
            value=f"**{position}**",
            inline=True
        )
        embed.add_field(
            name="🏆 NFL Team",
            value=f"**{nfl_team}**",
            inline=True
        )
        embed.add_field(
            name="⚔️ Opponent",
            value=f"**{opponent}**",
            inline=True
        )

        # Performance stats
        if actual_points == 'N/A':
            actual_formatted = "**N/A**"
        else:
            actual_formatted = f"**{float(actual_points):.1f}** pts"

        if proj_points == 'N/A':
            proj_formatted = "**N/A**"
        else:
            proj_formatted = f"**{float(proj_points):.1f}** pts"

        embed.add_field(
            name="📊 Week Points",
            value=actual_formatted,
            inline=True
        )
        embed.add_field(
            name="🎲 Projected",
            value=proj_formatted,
            inline=True
        )
        embed.add_field(
            name="🏁 Season Total",
            value=f"**{season_total}** pts" if season_total != 'N/A' else "**N/A**",
            inline=True
        )

        # Add spacing
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # Team and status info
        embed.add_field(
            name="🏈 Fantasy Team",
            value=f"**{player_team.team_name}**",
            inline=True
        )

        # Format injury status with proper emoji
        status_emoji = {
            'ACTIVE': '✅', 'QUESTIONABLE': '⚠️', 'OUT': '❌',
            'INJURY_RESERVE': '🏥', 'DOUBTFUL': '🔶', 'N/A': '🟢'
        }
        status_display = status_emoji.get(injury_status, '🟢')
        status_text = injury_status if injury_status != 'N/A' else 'Healthy'

        embed.add_field(
            name="🩺 Status",
            value=f"{status_display} **{status_text}**",
            inline=True
        )

        # Add empty field for layout
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.set_footer(text="💡 Try /team to see full roster breakdown")

        await interaction.followup.send(embed=embed)
    except discord.errors.NotFound as e:
        print(f"Player command timed out: {e}")
        return
    except Exception as e:
        try:
            await interaction.followup.send(f"Error fetching player: {e}")
        except discord.errors.NotFound:
            print(f"Could not send player error - interaction expired")

@client.tree.command(name="compare", description="Compare two teams side-by-side.")
@app_commands.describe(team1="First team name", team2="Second team name")
@app_commands.autocomplete(team1=team_name_autocomplete, team2=team_name_autocomplete)
async def compare(interaction: discord.Interaction, team1: str, team2: str):
    try:
        await interaction.response.defer()
        # Use cached league for better performance
        league = get_league(user_id=interaction.user.id)
        if not league:
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
        league_name = get_league_name(user_id=interaction.user.id)
        embed = discord.Embed(title=f"⚔️ {league_name} Team Comparison - Week {current_week}", color=discord.Color.purple())

        # Mobile-friendly comparison using individual fields
        embed.add_field(
            name=f"📊 {team1_obj.team_name}",
            value=f"**Record:** {team1_wins}-{team1_losses}-{team1_ties}\n**Season PF:** {team1_season_points:.1f}\n**This Week:** {team1_weekly:.1f}",
            inline=True
        )

        embed.add_field(
            name=f"📊 {team2_obj.team_name}",
            value=f"**Record:** {team2_wins}-{team2_losses}-{team2_ties}\n**Season PF:** {team2_season_points:.1f}\n**This Week:** {team2_weekly:.1f}",
            inline=True
        )

        # Add spacing field
        embed.add_field(name="\u200b", value="\u200b", inline=True)

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
    except discord.errors.NotFound as e:
        print(f"Compare command timed out: {e}")
        return
    except Exception as e:
        try:
            await interaction.followup.send(f"Error comparing teams: {e}")
        except discord.errors.NotFound:
            print(f"Could not send compare error - interaction expired")

@client.tree.command(name="standings", description="Show league standings with records and points.")
async def standings(interaction: discord.Interaction):
    try:
        await interaction.response.defer()

        # Initialize league
        try:
            league = get_league(user_id=interaction.user.id)
            if not league:
                await interaction.followup.send("❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
                return
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
        league_name = get_league_name(user_id=interaction.user.id)
        embed = SafeEmbedBuilder.create().set_title(f"🏆 {league_name} Standings - Week {current_week}").set_color(discord.Color.gold())

        # Create embed generator function for pagination
        def create_standings_embed(page_num, page_teams):
            rank_emojis = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟', '1️⃣1️⃣', '1️⃣2️⃣', '1️⃣3️⃣', '1️⃣4️⃣', '1️⃣5️⃣', '1️⃣6️⃣']

            page_embed = SafeEmbedBuilder.create()
            page_embed.set_title(f"🏆 {league_name} Standings - Week {current_week}")
            page_embed.set_color(discord.Color.gold())

            max_pages = (len(teams_data) - 1) // 8 + 1
            if max_pages > 1:
                page_embed.set_description(f"**Page {page_num + 1} of {max_pages}**")

            # Add teams for this page
            for i, team in enumerate(page_teams):
                actual_rank = (page_num * 8) + i + 1
                rank_emoji = rank_emojis[actual_rank - 1] if actual_rank <= len(rank_emojis) else f"{actual_rank}."
                team_name = team['name'][:15]  # Truncate for field name
                record = f"{team['wins']}-{team['losses']}"
                if team['ties'] > 0:
                    record += f"-{team['ties']}"

                field_name = f"{rank_emoji} {actual_rank}. {team_name}"
                field_value = f"**{record}** • {team['points_for']:.1f} PF • {team['points_against']:.1f} PA"

                page_embed.add_field(name=field_name, value=field_value, inline=True)

            return page_embed.build()

        # Use pagination if more than 8 teams
        if len(teams_data) > 8:
            view = PaginatedView(teams_data, page_size=8, embed_generator=create_standings_embed)
            view.update_buttons()
            initial_embed = create_standings_embed(0, view.get_page_data(0))
        else:
            # Single page - no pagination needed
            embed = create_standings_embed(0, teams_data)
            view = None

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

        # Send appropriate response based on pagination
        if len(teams_data) > 8:
            await interaction.followup.send(embed=initial_embed, view=view)
        else:
            # Add league stats to single page
            stats_lines = []
            if highest_scoring:
                stats_lines.append(f"🔥 **Highest Scoring**: {highest_scoring['name']} ({highest_scoring['points_for']:.1f} pts)")
            if lowest_scoring:
                stats_lines.append(f"🧊 **Lowest Scoring**: {lowest_scoring['name']} ({lowest_scoring['points_for']:.1f} pts)")
            if highest_weekly_team and highest_weekly_score > 0:
                stats_lines.append(f"💥 **Best Weekly Score**: {highest_weekly_team} - {highest_weekly_score:.1f} pts (Week {highest_weekly_week})")
            else:
                stats_lines.append("💥 **Best Weekly Score**: Not available")

            embed_builder = SafeEmbedBuilder.create()
            embed_builder.set_title(f"🏆 {league_name} Standings - Week {current_week}")
            embed_builder.set_color(discord.Color.gold())

            # Re-add teams
            rank_emojis = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣']
            for rank, team in enumerate(teams_data, 1):
                rank_emoji = rank_emojis[rank - 1] if rank <= len(rank_emojis) else f"{rank}."
                team_name = team['name'][:15]
                record = f"{team['wins']}-{team['losses']}"
                if team['ties'] > 0:
                    record += f"-{team['ties']}"

                field_name = f"{rank_emoji} {rank}. {team_name}"
                field_value = f"**{record}** • {team['points_for']:.1f} PF • {team['points_against']:.1f} PA"
                embed_builder.add_field(name=field_name, value=field_value, inline=True)

            embed_builder.add_field(name="📈 League Stats", value="\n".join(stats_lines), inline=False)
            await interaction.followup.send(embed=embed_builder.build())

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
            # Use cached league for better performance
            league = get_league(user_id=interaction.user.id)
            if not league:
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
        league_name = get_league_name(user_id=interaction.user.id)
        embed = discord.Embed(title=f"📈 {league_name} Analytics - Week {current_week}", color=discord.Color.blue())

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
    # Use safe defer first
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
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
        league_name = get_league_name(user_id=interaction.user.id)
        embed = discord.Embed(title=f"💤 {league_name} Sleeper Picks{pos_filter}", color=discord.Color.green())

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
        error_msg = f"❌ Error finding sleepers: {e}"
        print(f"Sleeper command error: {e}")
        await safe_interaction_response(interaction, error_msg, ephemeral=True)

@client.tree.command(name="matchup", description="Detailed player-by-player matchup analysis for this week.")
@app_commands.describe(team1="First team name", team2="Second team name (optional - will try to find current matchup)")
async def matchup(interaction: discord.Interaction, team1: str, team2: str = None):
    try:
        await interaction.response.defer()

        # Use multi-league system
        league = get_league(user_id=interaction.user.id)
        if not league:
            await interaction.followup.send("❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        # Find first team
        team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
        if not team1_obj:
            await interaction.followup.send(f"Team '{team1}' not found.")
            return

        team2_obj = None
        current_week = getattr(league, 'current_week', 1)

        # Find second team (improved opponent detection using scoreboard)
        if not team2:
            # Try to find current week opponent via scoreboard
            try:
                scoreboard = league.scoreboard(week=current_week)
                for matchup in scoreboard:
                    if hasattr(matchup, 'home_team') and hasattr(matchup, 'away_team'):
                        if matchup.home_team.team_id == team1_obj.team_id:
                            team2_obj = matchup.away_team
                            break
                        elif matchup.away_team.team_id == team1_obj.team_id:
                            team2_obj = matchup.home_team
                            break
            except Exception as e:
                print(f"Scoreboard opponent detection failed: {e}")
                # Fallback to schedule method
                try:
                    if hasattr(team1_obj, 'schedule') and team1_obj.schedule and len(team1_obj.schedule) >= current_week:
                        current_matchup = team1_obj.schedule[current_week - 1]
                        if hasattr(current_matchup, 'home_team') and hasattr(current_matchup, 'away_team'):
                            team2_obj = current_matchup.away_team if current_matchup.home_team == team1_obj else current_matchup.home_team
                except:
                    pass

        if not team2_obj:
            if team2:
                team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
                if not team2_obj:
                    await interaction.followup.send(f"Team '{team2}' not found.")
                    return
            else:
                await interaction.followup.send(f"Could not find current opponent for {team1}. Please specify both teams: `/matchup {team1} TeamName`")
                return

        # Helper functions for data extraction
        def get_actual_points(player, league_ref):
            """Get actual points for current week"""
            try:
                current_week = getattr(league_ref, 'current_week', 1)
                if hasattr(player, 'stats') and player.stats:
                    week_stats = player.stats.get(current_week, {})
                    actual_points = week_stats.get('points', None)
                    if actual_points is not None and actual_points > 0:
                        return actual_points
            except:
                pass
            return 0

        def get_weekly_projected(player, league_ref):
            """Get weekly projected points"""
            try:
                current_week = getattr(league_ref, 'current_week', 1)
                if hasattr(player, 'stats') and player.stats:
                    week_stats = player.stats.get(current_week, {})
                    projected = week_stats.get('projected_points', None)
                    if projected is not None:
                        return projected
                # Fallback to other projection attributes
                for attr in ['proj_points', 'projected_points']:
                    value = getattr(player, attr, None)
                    if value is not None:
                        return value
            except:
                pass
            return 0

        def get_player_status(player):
            """Get injury status"""
            status_abbrev = {
                'ACTIVE': '', 'QUESTIONABLE': 'Q', 'OUT': 'O', 'INJURY_RESERVE': 'IR', 'NORMAL': '', None: ''
            }
            # Don't show status for D/ST
            pos = getattr(player, 'position', '')
            if pos in ['D/ST', 'DST', 'DEF']:
                return ''

            status = getattr(player, 'injuryStatus', None)
            abbrev = status_abbrev.get(status, '')
            return f" ({abbrev})" if abbrev else ''

        # Get lineup data for both teams
        def get_lineup_with_scores(team):
            starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]

            # Group by position with proper ordering
            position_order = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'D/ST', 'DST']
            lineup_data = []

            # Sort players by lineup slot order
            def get_position_priority(player):
                pos = getattr(player, 'position', 'FLEX')
                return position_order.index(pos) if pos in position_order else 99

            starters_sorted = sorted(starters, key=get_position_priority)

            for player in starters_sorted:
                pos = getattr(player, 'position', 'FLEX')
                actual = get_actual_points(player, league)
                projected = get_weekly_projected(player, league)
                status = get_player_status(player)

                lineup_data.append({
                    'position': pos,
                    'name': player.name + status,
                    'actual': actual,
                    'projected': projected
                })

            return lineup_data

        team1_lineup = get_lineup_with_scores(team1_obj)
        team2_lineup = get_lineup_with_scores(team2_obj)

        # Calculate totals
        team1_actual_total = sum(p['actual'] for p in team1_lineup)
        team1_proj_total = sum(p['projected'] for p in team1_lineup)
        team2_actual_total = sum(p['actual'] for p in team2_lineup)
        team2_proj_total = sum(p['projected'] for p in team2_lineup)

        # Create simple side-by-side comparison: "QB Dak Prescott 0.0 | 0.0 Lamar Jackson QB"
        matchup_lines = []

        # Ensure both lineups have same length for alignment
        max_length = max(len(team1_lineup), len(team2_lineup))

        for i in range(max_length):
            # Team 1 player data
            if i < len(team1_lineup):
                p1 = team1_lineup[i]
                pos1 = p1['position'][:3]
                name1 = p1['name'][:15]  # Shorten to prevent overflow
                actual1 = f"{p1['actual']:.1f}"
            else:
                pos1 = name1 = actual1 = ""

            # Team 2 player data
            if i < len(team2_lineup):
                p2 = team2_lineup[i]
                pos2 = p2['position'][:3]
                name2 = p2['name'][:15]  # Shorten to prevent overflow
                actual2 = f"{p2['actual']:.1f}"
            else:
                pos2 = name2 = actual2 = ""

            # Create properly spaced format like scoreboard design
            if pos1 or pos2:  # Only add line if at least one player exists
                # Left side: Position Player_Name Score (total width 24: 3+1+15+1+4)
                if pos1:
                    left_side = f"{pos1:<3} {name1:<15} {actual1:>5}"
                else:
                    left_side = " " * 24

                # Right side: Score Player_Name Position (total width 24: 5+1+15+3)
                if pos2:
                    right_side = f"{actual2:<5} {name2:<15} {pos2:>3}"
                else:
                    right_side = " " * 24

                # No extra padding needed - should be exactly right width
                line = f"{left_side} | {right_side}"
                matchup_lines.append(line)

        # Add totals row with proper spacing - create divider by measuring actual content
        if matchup_lines:
            # Create a sample line to measure exact positioning
            sample_left = f"{'XXX':<3} {'XXXXXXXXXXXXXXX':<15} {'99.9':>5}"
            sample_right = f"{'99.9':<5} {'XXXXXXXXXXXXXXX':<15} {'XXX':>3}"
            sample_line = f"{sample_left} | {sample_right}"

            # Find pipe position in sample line
            pipe_pos = sample_line.find(' | ')

            # Create divider that matches exactly
            divider_left = '-' * pipe_pos
            divider_right = '-' * (len(sample_line) - pipe_pos - 3)  # -3 for ' | '
            divider_line = f"{divider_left} | {divider_right}"
            matchup_lines.append(divider_line)

            # Left total: formatted exactly like player rows
            left_total = f"{'TOT':<3} {'TOTAL':<15} {team1_actual_total:>5.1f}"
            # Right total: formatted exactly like player rows
            right_total = f"{team2_actual_total:<5.1f} {'TOTAL':<15} {'TOT':>3}"
            total_line = f"{left_total} | {right_total}"
            matchup_lines.append(total_line)

        # Create embed
        embed = discord.Embed(
            title=f"⚔️ {get_league_name(user_id=interaction.user.id)} Matchup - Week {current_week}",
            color=0xff6b35
        )

        # Team headers
        embed.add_field(name=f"🔵 {team1_obj.team_name}", value=f"**{team1_actual_total:.1f}** actual | **{team1_proj_total:.1f}** projected", inline=True)
        embed.add_field(name="VS", value="⚔️", inline=True)
        embed.add_field(name=f"🔴 {team2_obj.team_name}", value=f"**{team2_actual_total:.1f}** actual | **{team2_proj_total:.1f}** projected", inline=True)

        # Player comparison table - split into chunks to avoid Discord 1024 char limit
        full_table = chr(10).join(matchup_lines)

        # Split into manageable chunks (Discord limit is 1024 chars per field)
        chunk_size = 900  # Leave room for code block formatting
        table_chunks = []

        if len(full_table) <= chunk_size:
            table_chunks.append(full_table)
        else:
            # Split by lines to keep formatting intact
            lines = matchup_lines
            current_chunk = []
            current_length = 0

            for line in lines:
                line_length = len(line) + 1  # +1 for newline
                if current_length + line_length > chunk_size and current_chunk:
                    table_chunks.append(chr(10).join(current_chunk))
                    current_chunk = [line]
                    current_length = line_length
                else:
                    current_chunk.append(line)
                    current_length += line_length

            if current_chunk:
                table_chunks.append(chr(10).join(current_chunk))

        # Add table chunks as separate fields with team headers
        for i, chunk in enumerate(table_chunks):
            if i == 0:
                # First chunk gets team headers
                team1_short = team1_obj.team_name[:12]
                team2_short = team2_obj.team_name[:12]
                field_name = f"📊 {team1_short} vs {team2_short}"
            else:
                field_name = f"📊 Continued ({i+1})"
            embed.add_field(name=field_name, value=f"```\n{chunk}\n```", inline=False)

        # Quick analysis
        analysis = []
        actual_diff = abs(team1_actual_total - team2_actual_total)
        proj_diff = abs(team1_proj_total - team2_proj_total)

        if team1_actual_total > team2_actual_total:
            analysis.append(f"🏆 **Current Leader**: {team1_obj.team_name} by {actual_diff:.1f} pts")
        elif team2_actual_total > team1_actual_total:
            analysis.append(f"🏆 **Current Leader**: {team2_obj.team_name} by {actual_diff:.1f} pts")
        else:
            analysis.append("🏆 **Current**: Tied game!")

        if team1_proj_total > team2_proj_total:
            analysis.append(f"📈 **Projected Winner**: {team1_obj.team_name} by {proj_diff:.1f} pts")
        elif team2_proj_total > team1_proj_total:
            analysis.append(f"📈 **Projected Winner**: {team2_obj.team_name} by {proj_diff:.1f} pts")
        else:
            analysis.append("📈 **Projected**: Even matchup!")

        embed.add_field(name="🔍 Quick Analysis", value="\n".join(analysis), inline=False)

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
        # Use cached league for better performance
        league = get_league(user_id=interaction.user.id)
        if not league:
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
        league_name = get_league_name(user_id=interaction.user.id)
        embed = discord.Embed(
            title=f"🎯 {league_name} Waiver Wire",
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

    except discord.errors.NotFound as e:
        print(f"Waiver command timed out: {e}")
        return
    except Exception as e:
        error_msg = f"Error analyzing waiver wire: {e}"
        print(f"Waiver error: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
        except discord.errors.NotFound:
            print(f"Could not send waiver error - interaction expired")

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
        # Use existing league function to avoid timeout
        league = get_league(user_id=interaction.user.id)
        if not league:
            # Fallback to direct creation
            league = League(league_id=LEAGUE_ID, year=SEASON_ID, espn_s2=ESPN_S2, swid=SWID)

        # Find teams (optimized with early exit)
        team1_obj = None
        team2_obj = None
        team1_lower = team1.lower()
        team2_lower = team2.lower()

        for team in league.teams:
            # Try different owner attribute names
            owner_name = getattr(team, 'owner', '') or getattr(team, 'owners', '') or ''
            if isinstance(owner_name, list) and owner_name:
                owner_name = owner_name[0] if owner_name else ''

            team_name_lower = team.team_name.lower()
            owner_name_lower = str(owner_name).lower() if owner_name else ''

            if not team1_obj and (team1_lower in team_name_lower or (owner_name and team1_lower in owner_name_lower)):
                team1_obj = team
            if not team2_obj and (team2_lower in team_name_lower or (owner_name and team2_lower in owner_name_lower)):
                team2_obj = team

            # Early exit if both teams found
            if team1_obj and team2_obj:
                break

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
        league_name = get_league_name(user_id=interaction.user.id)
        embed = discord.Embed(
            title=f"🤝 {league_name} Trade Analysis",
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

    except discord.errors.NotFound as e:
        # Handle interaction timeout (Discord error 10062)
        print(f"Trade command timed out: {e}")
        # Can't respond since interaction is expired
        return
    except Exception as e:
        error_msg = f"Error analyzing trade: {e}"
        print(f"Trade error: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
        except discord.errors.NotFound:
            # Interaction expired, can't respond
            print("Could not send error message - interaction expired")

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
        # Recreate main menu directly
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=0x32CD32
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
        # Recreate main menu directly
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=0x32CD32
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
        # Recreate main menu directly instead of calling method on new instance
        embed = discord.Embed(
            title="🏈 Fantasy Football Command Center",
            description="Select a category to explore available commands",
            color=0x32CD32
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

@client.tree.command(name="card", description="Generate a visual team card with key stats and graphics.")
@app_commands.describe(team_name="Team name to generate card for")
@app_commands.autocomplete(team_name=team_name_autocomplete)
async def card(interaction: discord.Interaction, team_name: str):
    # Use safe defer first
    if not await safe_defer(interaction):
        return

    try:
        # Helper function to ensure field values don't exceed 1024 characters
        def safe_field_value(text, max_length=1024):
            if len(text) <= max_length:
                return text
            return text[:max_length-3] + "..."

        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        # Find the team
        team = None
        for t in league.teams:
            if team_name.lower() in t.team_name.lower():
                team = t
                break

        if not team:
            await safe_interaction_response(interaction, f"Team '{team_name}' not found. Available teams: {', '.join(t.team_name for t in league.teams)}", ephemeral=True)
            return

        # Get current week
        current_week = getattr(league, 'current_week', 1)

        # Calculate team stats
        def calculate_team_stats(team):
            total_points = 0
            games_played = 0
            weekly_scores = []

            # Get weekly scores
            for week in range(1, current_week):
                try:
                    week_score = team.scores[week - 1] if len(team.scores) >= week else 0
                    if week_score > 0:
                        weekly_scores.append(week_score)
                        total_points += week_score
                        games_played += 1
                except (IndexError, AttributeError):
                    pass

            avg_points = total_points / max(games_played, 1)

            # Calculate consistency (lower std dev = more consistent)
            if len(weekly_scores) > 1:
                mean_score = sum(weekly_scores) / len(weekly_scores)
                variance = sum((score - mean_score) ** 2 for score in weekly_scores) / len(weekly_scores)
                std_dev = variance ** 0.5
                consistency = max(0, 100 - (std_dev / mean_score) * 100) if mean_score > 0 else 0
            else:
                consistency = 100

            return {
                'total_points': total_points,
                'avg_points': avg_points,
                'games_played': games_played,
                'weekly_scores': weekly_scores,
                'consistency': consistency,
                'high_score': max(weekly_scores) if weekly_scores else 0,
                'low_score': min(weekly_scores) if weekly_scores else 0
            }

        stats = calculate_team_stats(team)

        # Get current roster strength
        starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
        total_projected = sum(get_current_week_points(p, league) for p in starters if get_current_week_points(p, league) != 'N/A')

        # Find team's best players
        all_players = []
        for player in team.roster:
            proj = get_current_week_points(player, league)
            if proj != 'N/A' and proj > 0:
                all_players.append({
                    'name': player.name,
                    'position': player.position,
                    'projected': proj,
                    'player': player
                })

        all_players.sort(key=lambda x: x['projected'], reverse=True)
        top_3_players = all_players[:3]

        # Create rich embed with visual elements
        embed = discord.Embed(
            title="🏈 Team Card",
            color=0x4169E1
        )

        # Team header with record
        wins = getattr(team, 'wins', 0)
        losses = getattr(team, 'losses', 0)
        record = f"{wins}-{losses}"

        # Calculate rank
        sorted_teams = sorted(league.teams, key=lambda t: (getattr(t, 'wins', 0), getattr(t, 'points_for', 0)), reverse=True)
        rank = next((i + 1 for i, t in enumerate(sorted_teams) if t.team_id == team.team_id), 0)

        # Get owner name properly
        owner_data = getattr(team, 'owner', None)
        owner_name = "N/A"

        if owner_data:
            if isinstance(owner_data, dict):
                # Extract display name from dictionary
                owner_name = (owner_data.get('displayName') or
                             f"{owner_data.get('firstName', '')} {owner_data.get('lastName', '')}".strip() or
                             owner_data.get('id', 'N/A'))
            elif isinstance(owner_data, str):
                owner_name = owner_data
            elif isinstance(owner_data, list) and owner_data:
                # Handle list of owners - get the first one
                first_owner = owner_data[0]
                if isinstance(first_owner, dict):
                    owner_name = (first_owner.get('displayName') or
                                 f"{first_owner.get('firstName', '')} {first_owner.get('lastName', '')}".strip() or
                                 first_owner.get('id', 'N/A'))
                else:
                    owner_name = str(first_owner)

        # Try alternative owner attributes if still no name
        if owner_name == "N/A" or not owner_name:
            # Check if owner is actually stored as a string representation of the data
            owner_str = str(getattr(team, 'owner', ''))
            if 'displayName' in owner_str:
                # Try to extract displayName from string representation
                import re
                display_match = re.search(r"'displayName': '([^']+)'", owner_str)
                if display_match:
                    owner_name = display_match.group(1)
                else:
                    # Try without quotes
                    display_match = re.search(r"'displayName': ([^,}]+)", owner_str)
                    if display_match:
                        owner_name = display_match.group(1).strip("'\"")

        # Clean up email addresses to show just the name part
        if owner_name and '@' in owner_name and '.' in owner_name:
            owner_name = owner_name.split('@')[0]

        # Ensure we have a clean name
        if not owner_name or owner_name == "N/A" or len(str(owner_name)) > 50:
            owner_name = "Unknown Owner"

        team_info_text = f"**Record:** {record} (#{rank})\n**Owner:** {owner_name}\n**Division:** {getattr(team, 'division_name', 'N/A')}"
        embed.add_field(
            name=f"📊 {team.team_name}",
            value=safe_field_value(team_info_text),
            inline=False
        )

        # Performance metrics with visual bars
        def create_progress_bar(value, max_value, length=10):
            filled = int((value / max_value) * length) if max_value > 0 else 0
            bar = "=" * filled + "-" * (length - filled)
            return f"`[{bar}]` {value:.1f}"

        # Find league max for scaling bars
        league_max_avg = max(calculate_team_stats(t)['avg_points'] for t in league.teams)
        league_max_proj = max(sum(get_current_week_points(p, league) for p in t.roster if getattr(p, 'lineupSlot', None) != "BE" and get_current_week_points(p, league) != 'N/A') for t in league.teams)

        performance_text = f"**Average Points:** {create_progress_bar(stats['avg_points'], league_max_avg)}\n"
        performance_text += f"**Projected (Week {current_week}):** {create_progress_bar(total_projected, league_max_proj)}\n"
        performance_text += f"**Consistency:** {create_progress_bar(stats['consistency'], 100)} %\n"
        performance_text += f"**High Score:** {stats['high_score']:.1f} | **Low Score:** {stats['low_score']:.1f}"

        embed.add_field(
            name="📈 Performance Metrics",
            value=safe_field_value(performance_text),
            inline=False
        )

        # Star players section
        if top_3_players:
            stars_text = ""
            star_emojis = ["⭐", "🌟", "✨"]
            for i, player in enumerate(top_3_players):
                emoji = star_emojis[i] if i < len(star_emojis) else "🔸"
                stars_text += f"{emoji} **{player['name']}** ({player['position']}) - {player['projected']:.1f} pts\n"

            embed.add_field(
                name="🌟 Star Players",
                value=safe_field_value(stars_text.strip()),
                inline=True
            )

        # Recent form (last 3 games)
        recent_scores = stats['weekly_scores'][-3:] if len(stats['weekly_scores']) >= 3 else stats['weekly_scores']
        if recent_scores:
            form_text = ""
            for i, score in enumerate(recent_scores):
                week_num = len(stats['weekly_scores']) - len(recent_scores) + i + 1
                form_text += f"Week {week_num}: {score:.1f}\n"

            # Calculate trend
            if len(recent_scores) >= 2:
                trend = "📈" if recent_scores[-1] > recent_scores[-2] else "📉" if recent_scores[-1] < recent_scores[-2] else "➡️"
                form_text += f"\nTrend: {trend}"

            embed.add_field(
                name="📊 Recent Form",
                value=safe_field_value(form_text),
                inline=True
            )

        # League context
        total_teams = len(league.teams)
        points_rank = sorted(league.teams, key=lambda t: calculate_team_stats(t)['avg_points'], reverse=True)
        points_position = next((i + 1 for i, t in enumerate(points_rank) if t.team_id == team.team_id), 0)

        context_text = f"**League Position:** #{rank} of {total_teams}\n"
        context_text += f"**Scoring Rank:** #{points_position} of {total_teams}\n"
        context_text += f"**Games Played:** {stats['games_played']}"

        embed.add_field(
            name="🏆 League Context",
            value=safe_field_value(context_text),
            inline=False
        )

        # Power ranking calculation
        record_score = (wins / max(wins + losses, 1)) * 40  # 40% weight
        points_score = (stats['avg_points'] / league_max_avg) * 40 if league_max_avg > 0 else 0  # 40% weight
        consistency_score = (stats['consistency'] / 100) * 20  # 20% weight
        power_rating = record_score + points_score + consistency_score

        rating_text = f"**Power Rating:** {power_rating:.1f}/100\n"

        # Rating description
        if power_rating >= 80:
            rating_text += "🔥 **Elite** - Championship contender"
        elif power_rating >= 65:
            rating_text += "💪 **Strong** - Playoff bound"
        elif power_rating >= 50:
            rating_text += "⚖️ **Average** - In the mix"
        elif power_rating >= 35:
            rating_text += "⚠️ **Struggling** - Needs improvement"
        else:
            rating_text += "🆘 **Rebuilding** - Long season ahead"

        embed.add_field(
            name="⚡ Power Rating",
            value=safe_field_value(rating_text),
            inline=False
        )

        # Set team thumbnail (you could customize this with actual team logos)
        embed.set_thumbnail(url="https://a.espncdn.com/i/espn/logos/nfl/NFL.png")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"❌ Error creating team card: {e}"
        print(f"Card error: {e}")
        await safe_interaction_response(interaction, error_msg, ephemeral=True)

@client.tree.command(name="scoreboard", description="Live updating scoreboard for current week matchups.")
@app_commands.describe(auto_refresh="Enable auto-refresh every 30 seconds (default: True)")
async def scoreboard(interaction: discord.Interaction, auto_refresh: bool = True):
    # Use safe defer first, before any API calls
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        # Debug: Log which league is being used
        league_name = get_league_name(user_id=interaction.user.id)
        print(f"DEBUG: Scoreboard for user {interaction.user.id} using league: {league_name}")

        current_week = getattr(league, 'current_week', 1)

        def create_scoreboard_embeds():
            # Get matchups for current week
            matchups = []

            # Create team pairings based on current week schedule
            teams_in_matchups = set()

            for team in league.teams:
                if team.team_id in teams_in_matchups:
                    continue

                # Find this team's opponent for current week
                opponent = None
                if hasattr(team, 'schedule') and len(team.schedule) >= current_week:
                    try:
                        week_opponent = team.schedule[current_week - 1]
                        if hasattr(week_opponent, 'team_id'):
                            opponent = week_opponent
                        elif hasattr(week_opponent, 'opponent'):
                            opponent = week_opponent.opponent
                    except (IndexError, AttributeError):
                        pass

                # Alternative method: check box scores
                if not opponent:
                    try:
                        box_score = league.box_scores(current_week)
                        for matchup in box_score:
                            if hasattr(matchup, 'home_team') and hasattr(matchup, 'away_team'):
                                if matchup.home_team.team_id == team.team_id:
                                    opponent = matchup.away_team
                                    break
                                elif matchup.away_team.team_id == team.team_id:
                                    opponent = matchup.home_team
                                    break
                    except Exception:
                        pass

                if opponent and opponent.team_id not in teams_in_matchups:
                    # Try multiple methods to get current scores
                    team_score = 0
                    opponent_score = 0

                    # Method 1: Try box scores API
                    try:
                        box_scores = league.box_scores(current_week)
                        for box_score in box_scores:
                            if hasattr(box_score, 'home_team') and hasattr(box_score, 'away_team'):
                                if box_score.home_team.team_id == team.team_id:
                                    team_score = getattr(box_score.home_score, 'total_points', 0) or getattr(box_score, 'home_score', 0)
                                    opponent_score = getattr(box_score.away_score, 'total_points', 0) or getattr(box_score, 'away_score', 0)
                                    break
                                elif box_score.away_team.team_id == team.team_id:
                                    team_score = getattr(box_score.away_score, 'total_points', 0) or getattr(box_score, 'away_score', 0)
                                    opponent_score = getattr(box_score.home_score, 'total_points', 0) or getattr(box_score, 'home_score', 0)
                                    break
                    except Exception as e:
                        print(f"Box score method failed: {e}")
                        pass

                    # Method 2: Try team.scores if box scores didn't work
                    if team_score == 0 and opponent_score == 0:
                        try:
                            if hasattr(team, 'scores') and len(team.scores) >= current_week:
                                team_score = team.scores[current_week - 1] or 0
                            if hasattr(opponent, 'scores') and len(opponent.scores) >= current_week:
                                opponent_score = opponent.scores[current_week - 1] or 0
                        except (IndexError, AttributeError):
                            pass

                    # Method 3: Calculate actual points only (no projected scores)
                    if team_score == 0 and opponent_score == 0:
                        def get_actual_points_only(player, league_ref):
                            """Get only actual points, not projected"""
                            current_week = getattr(league_ref, 'current_week', 1)
                            if hasattr(player, 'stats') and player.stats:
                                try:
                                    week_stats = player.stats.get(current_week, {})
                                    actual_points = week_stats.get('points', None)
                                    if actual_points is not None and actual_points > 0:
                                        return actual_points

                                    # Check applied stats for actual game performance
                                    applied_stats = week_stats.get('appliedStats', {})
                                    if applied_stats and len(applied_stats) > 0:
                                        # Calculate points from actual stats if available
                                        total_points = 0
                                        for stat_id, value in applied_stats.items():
                                            if isinstance(value, (int, float)) and value > 0:
                                                total_points += value
                                        if total_points > 0:
                                            return total_points
                                except:
                                    pass
                            return 0

                        starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                        team_score = sum(get_actual_points_only(p, league) for p in starters)

                        opp_starters = [p for p in opponent.roster if getattr(p, 'lineupSlot', None) != "BE"]
                        opponent_score = sum(get_actual_points_only(p, league) for p in opp_starters)

                    # Calculate projected scores for both teams
                    def get_weekly_projected_points(player, league_ref):
                        """Get weekly projected points for a player"""
                        current_week = getattr(league_ref, 'current_week', 1)
                        if hasattr(player, 'stats') and player.stats:
                            try:
                                week_stats = player.stats.get(current_week, {})
                                projected = week_stats.get('projected_points', None)
                                if projected is not None:
                                    return projected
                            except:
                                pass
                        # Fallback to other projection attributes
                        for attr in ['proj_points', 'projected_points']:
                            value = getattr(player, attr, None)
                            if value is not None:
                                return value
                        return 0

                    starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    team_projected = sum(get_weekly_projected_points(p, league) for p in starters)

                    opp_starters = [p for p in opponent.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    opponent_projected = sum(get_weekly_projected_points(p, league) for p in opp_starters)

                    matchups.append({
                        'team1': team,
                        'team2': opponent,
                        'score1': team_score,
                        'score2': opponent_score,
                        'proj1': team_projected,
                        'proj2': opponent_projected
                    })

                    teams_in_matchups.add(team.team_id)
                    teams_in_matchups.add(opponent.team_id)

            # Sort matchups by total points (most exciting games first)
            matchups.sort(key=lambda m: m['score1'] + m['score2'], reverse=True)

            # Create individual embeds for each matchup
            embeds = []

            if matchups:
                # Create single consolidated embed that maximizes horizontal space
                league_name = get_league_name(user_id=interaction.user.id)

                # Calculate summary stats
                total_points = sum(m['score1'] + m['score2'] for m in matchups)
                num_teams = len(matchups) * 2 if matchups else 0
                avg_team_score = total_points / num_teams if num_teams > 0 else 0
                highest_score = max(max(m['score1'], m['score2']) for m in matchups) if matchups else 0
                closest_game = min(abs(m['score1'] - m['score2']) for m in matchups) if matchups else 0

                main_embed = discord.Embed(
                    title=f"🏈 {league_name} - Week {current_week}",
                    description=f"🔄 Auto-refresh {'ON' if auto_refresh else 'OFF'} • 🎯 Total: {total_points:.1f} • 📈 Avg: {avg_team_score:.1f} • 🔥 High: {highest_score:.1f}",
                    color=0xFF6B35
                )

                # Add refresh timestamp
                import datetime
                now = datetime.datetime.now()
                main_embed.set_footer(text=f"Last updated: {now.strftime('%I:%M:%S %p')}")

                # Get remaining players info function
                def get_remaining_players(team, league_ref):
                    starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    total_starters = len(starters)
                    yet_to_play = 0

                    def has_actual_points(player, league):
                        """Check if player has actual points (not just projected)"""
                        current_week = getattr(league, 'current_week', 1)

                        # Check player stats for actual points
                        if hasattr(player, 'stats') and player.stats:
                            try:
                                week_stats = player.stats.get(current_week, {})
                                # Look for actual points - ESPN uses different keys
                                actual_points = week_stats.get('points', None)
                                if actual_points is not None and actual_points > 0:
                                    return True

                                # Check applied stats (actual game stats)
                                applied_stats = week_stats.get('appliedStats', {})
                                if applied_stats and len(applied_stats) > 0:
                                    # If there are applied stats, player has played
                                    return True

                            except Exception:
                                pass

                        # Check if player has game-specific attributes indicating they played
                        if hasattr(player, 'game_played'):
                            game_played = getattr(player, 'game_played', 0)
                            if game_played > 0:
                                return True

                        return False

                    try:
                        for player in starters:
                            player_yet_to_play = True

                            try:
                                # Check injury status first - injured players don't count as "yet to play"
                                injury_status = getattr(player, 'injuryStatus', '')
                                if injury_status in ['OUT', 'IR', 'SUSPENDED']:
                                    player_yet_to_play = False

                                # Check if player has ACTUAL points (not projected)
                                elif has_actual_points(player, league_ref):
                                    player_yet_to_play = False

                            except Exception:
                                # If we can't determine status, assume yet to play
                                yet_to_play += 1

                            if player_yet_to_play:
                                yet_to_play += 1

                    except Exception:
                        # If anything fails, fall back to showing all players
                        yet_to_play = total_starters

                    return f"{yet_to_play}/{total_starters}"

                # Create simple table format like the old version
                def format_team_name_inline(name, max_length=7):
                    """Format team name for inline display"""
                    words = name.split()
                    if len(words) == 1:
                        return name + "  " if len(name) <= max_length else name[:max_length-2] + "  "
                    result = f"{words[0]} {words[1][0]}."
                    return result if len(result) <= max_length else words[0][:max_length-3] + " " + words[1][0] + "."

                # Create compact table
                table_lines = []
                for matchup in matchups:
                    team1 = matchup['team1']
                    team2 = matchup['team2']
                    score1 = matchup['score1']
                    score2 = matchup['score2']
                    proj1 = matchup.get('proj1', 0)
                    proj2 = matchup.get('proj2', 0)

                    # Get remaining players
                    team1_remaining = get_remaining_players(team1, league)
                    team2_remaining = get_remaining_players(team2, league)

                    # Format team names
                    base_name1 = format_team_name_inline(team1.team_name)
                    base_name2 = format_team_name_inline(team2.team_name)

                    # Create team + remaining formatted strings
                    team1_full = f"{base_name1} ({team1_remaining})"
                    team2_full = f"{base_name2} ({team2_remaining})"

                    # Format scores
                    score1_str = f"{score1:4.1f}|{proj1:4.1f}"
                    score2_str = f"{score2:4.1f}|{proj2:4.1f}"

                    # Determine winner indicator
                    if score1 > score2:
                        vs_symbol = "◀"  # Arrow points to winner (left team)
                    elif score2 > score1:
                        vs_symbol = "▶"  # Arrow points to winner (right team)
                    else:
                        vs_symbol = "="   # Tie

                    # Create table row
                    line = f"{team1_full:<14} {score1_str:<9} {vs_symbol} {score2_str:>9} {team2_full:>14}"
                    table_lines.append(line)

                # Mobile-friendly matchup display using individual fields
                for i, matchup in enumerate(matchups):
                    team1 = matchup['team1']
                    team2 = matchup['team2']
                    score1 = matchup['score1']
                    score2 = matchup['score2']
                    proj1 = matchup.get('proj1', 0)
                    proj2 = matchup.get('proj2', 0)

                    # Get remaining players
                    team1_remaining = get_remaining_players(team1, league)
                    team2_remaining = get_remaining_players(team2, league)

                    # Determine winner/status
                    if score1 > score2:
                        status_emoji = "🔥"
                        status_text = f"{team1.team_name} leading"
                    elif score2 > score1:
                        status_emoji = "🔥"
                        status_text = f"{team2.team_name} leading"
                    else:
                        status_emoji = "⚖️"
                        status_text = "Tied game"

                    # Create mobile-friendly field
                    field_name = f"{status_emoji} Matchup {i+1}: {team1.team_name} vs {team2.team_name}"
                    field_value = (
                        f"**{team1.team_name}**: `{score1:.1f}` pts (proj: `{proj1:.1f}`) • {team1_remaining} left\n"
                        f"**{team2.team_name}**: `{score2:.1f}` pts (proj: `{proj2:.1f}`) • {team2_remaining} left\n"
                        f"*{status_text}*"
                    )

                    main_embed.add_field(name=field_name, value=field_value, inline=False)

                # Add summary info to the same embed
                closest_game = min(abs(m['score1'] - m['score2']) for m in matchups) if matchups else 0

                summary_lines = []
                summary_lines.append(f"🎯 **Total Points Scored**: {total_points:.1f}")
                summary_lines.append(f"📈 **Average Game Total**: {avg_team_score:.1f}")
                summary_lines.append(f"🔥 **Highest Individual Score**: {highest_score:.1f}")
                summary_lines.append(f"⚡ **Closest Game**: {closest_game:.1f} point difference")

                main_embed.add_field(name="📋 Week Summary", value="\n".join(summary_lines), inline=False)
                main_embed.add_field(name="Stats", value="🔄 **Status:** Live Updates", inline=False)

                embeds = [main_embed]
                return embeds

                # Old code below - keeping for reference but not executed
                # Get remaining players info function
                def get_remaining_players(team, league_ref):
                    starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    total_starters = len(starters)
                    yet_to_play = 0

                    def has_actual_points(player, league):
                        """Check if player has actual points (not just projected)"""
                        current_week = getattr(league, 'current_week', 1)

                        # Check player stats for actual points
                        if hasattr(player, 'stats') and player.stats:
                            try:
                                week_stats = player.stats.get(current_week, {})
                                # Look for actual points - ESPN uses different keys
                                actual_points = week_stats.get('points', None)
                                if actual_points is not None and actual_points > 0:
                                    return True

                                # Check applied stats (actual game stats)
                                applied_stats = week_stats.get('appliedStats', {})
                                if applied_stats and len(applied_stats) > 0:
                                    # If there are applied stats, player has played
                                    return True

                            except Exception:
                                pass

                        # Check if player has game-specific attributes indicating they played
                        if hasattr(player, 'game_played'):
                            game_played = getattr(player, 'game_played', 0)
                            if game_played > 0:
                                return True

                        return False

                    try:
                        for player in starters:
                            player_yet_to_play = True

                            try:
                                # Check injury status first - injured players don't count as "yet to play"
                                injury_status = getattr(player, 'injuryStatus', '')
                                if injury_status in ['OUT', 'IR', 'SUSPENDED']:
                                    player_yet_to_play = False

                                # Check if player has ACTUAL points (not projected)
                                elif has_actual_points(player, league_ref):
                                    player_yet_to_play = False

                                if player_yet_to_play:
                                    yet_to_play += 1

                            except Exception:
                                # If we can't determine status, assume yet to play
                                yet_to_play += 1

                    except Exception:
                        # If anything fails, fall back to showing all players
                        yet_to_play = total_starters

                    return f"{yet_to_play}/{total_starters}"

                # Helper function to format team names
                def format_team_name(name, max_length=7):
                    words = name.split()

                    if len(words) == 1:
                        # Single word: pad with spaces to match multi-word format length
                        if len(name) <= max_length:
                            return name + "  "  # Add 2 spaces to match " X." format
                        else:
                            return name[:max_length-2] + "  "

                    # Two or more words: first word + first letter of second word + period
                    result = f"{words[0]} {words[1][0]}."
                    if len(result) <= max_length:
                        return result
                    else:
                        # Truncate first word if needed to fit format
                        return words[0][:max_length-3] + " " + words[1][0] + "."

                # Build simple vs-style lines
                all_table_lines = []

                # First pass: calculate the longest team name to determine optimal spacing
                max_name_length = 0
                formatted_matchups = []

                for matchup in matchups:
                    team1 = matchup['team1']
                    team2 = matchup['team2']
                    score1 = matchup['score1']
                    score2 = matchup['score2']
                    proj1 = matchup['proj1']
                    proj2 = matchup['proj2']

                    # Get remaining players for each team first
                    team1_remaining = get_remaining_players(team1, league)
                    team2_remaining = get_remaining_players(team2, league)

                    # Format team names with shorter length to prevent overflow
                    base_name1 = format_team_name(team1.team_name, 7)
                    base_name2 = format_team_name(team2.team_name, 7)

                    # Ensure consistent formatting by padding remaining player counts
                    # This handles both single digit (8/9) and double digit (11/11) counts
                    name1 = f"{base_name1} ({team1_remaining})"
                    name2 = f"{base_name2} ({team2_remaining})"

                    # Pad names to ensure consistent alignment - reduced to prevent overflow
                    name1 = f"{name1:<14}"
                    name2 = f"{name2:<14}"

                    formatted_matchups.append({
                        'name1': name1,
                        'name2': name2,
                        'score1': score1,
                        'score2': score2,
                        'proj1': proj1,
                        'proj2': proj2
                    })

                    # Track max length for dynamic spacing
                    max_name_length = max(max_name_length, len(name1), len(name2))

                # Use fixed spacing for consistent alignment across all leagues
                # Account for double digit player counts: team names get 14 characters
                left_spacing = 14

                # Second pass: format with consistent spacing
                for matchup_data in formatted_matchups:
                    name1 = matchup_data['name1']
                    name2 = matchup_data['name2']
                    score1 = matchup_data['score1']
                    score2 = matchup_data['score2']
                    # Handle missing projected scores gracefully
                    proj1 = matchup_data.get('proj1', 0)
                    proj2 = matchup_data.get('proj2', 0)

                    # Format scores with better spacing and readability
                    score1_str = f"{score1:>4.1f}|{proj1:<5.1f}"
                    score2_str = f"{score2:>4.1f}|{proj2:<5.1f}"

                    # Add winner arrows pointing toward the winning team - compact format
                    score1_compact = f"{score1:>4.1f}|{proj1:<5.1f}"
                    score2_compact = f"{score2:>4.1f}|{proj2:<5.1f}"

                    if score1 > score2:
                        # Team 1 winning - arrow points left toward winning team
                        line = f"{name1:<{left_spacing}} {score1_compact:<11} ◀ {score2_compact:<11} {name2}"
                    elif score2 > score1:
                        # Team 2 winning - arrow points right toward winning team
                        line = f"{name1:<{left_spacing}} {score1_compact:<11} ▶ {score2_compact:<11} {name2}"
                    else:
                        # Tied
                        line = f"{name1:<{left_spacing}} {score1_compact:<11} = {score2_compact:<11} {name2}"

                    all_table_lines.append(line)

                # Add header to explain format with proper alignment
                if all_table_lines:
                    header_line = f"{'Team (Rem.)':<{left_spacing}} {'Act|Proj':<11} {'VS'} {'Act|Proj':<11} {'Team (Rem.)'}"
                    separator_line = "─" * (len(header_line) - 10)  # Adjust for visual length
                    all_table_lines.insert(0, separator_line)
                    all_table_lines.insert(0, header_line)

                # Create mobile-friendly matchup display instead of table
                # Create main matchups embed using SafeEmbedBuilder
                matchups_embed = SafeEmbedBuilder.create()
                matchups_embed.set_title("📊 Live Matchups")
                matchups_embed.set_color(0x32CD32)

                # Add each matchup as a field - mobile friendly
                for i, matchup in enumerate(matchups):
                    team1 = matchup['team1']
                    team2 = matchup['team2']
                    score1 = matchup['score1']
                    score2 = matchup['score2']
                    proj1 = matchup.get('proj1', 0)
                    proj2 = matchup.get('proj2', 0)

                    # Determine status
                    if score1 > score2:
                        status_emoji = "🔥"
                        status_text = f"{team1.team_name} leading"
                    elif score2 > score1:
                        status_emoji = "🔥"
                        status_text = f"{team2.team_name} leading"
                    else:
                        status_emoji = "⚖️"
                        status_text = "Tied game"

                    field_name = f"{status_emoji} {team1.team_name} vs {team2.team_name}"
                    field_value = (
                        f"**{team1.team_name}**: `{score1:.1f}` pts (proj: `{proj1:.1f}`)\n"
                        f"**{team2.team_name}**: `{score2:.1f}` pts (proj: `{proj2:.1f}`)\n"
                        f"*{status_text}*"
                    )

                    matchups_embed.add_field(name=field_name, value=field_value, inline=False)

                embeds.append(matchups_embed.build())

                # Create summary embed
                total_points = sum(m['score1'] + m['score2'] for m in matchups)
                avg_game_total = total_points / len(matchups) if matchups else 0
                highest_score = max(max(m['score1'], m['score2']) for m in matchups) if matchups else 0
                closest_game = min(abs(m['score1'] - m['score2']) for m in matchups) if matchups else 0

                summary_embed = discord.Embed(
                    title="📋 Week Summary",
                    color=0x9932CC
                )

                summary_lines = []
                summary_lines.append(f"🎯 **Total Points Scored**: {total_points:.1f}")
                summary_lines.append(f"📈 **Average Game Total**: {avg_game_total:.1f}")
                summary_lines.append(f"🔥 **Highest Individual Score**: {highest_score:.1f}")
                summary_lines.append(f"⚡ **Closest Game**: {closest_game:.1f} point difference")

                summary_embed.add_field(name="Stats", value="\n".join(summary_lines), inline=False)
                embeds.append(summary_embed)

            else:
                league_name = get_league_name(user_id=self.user_id) if self.user_id else "Fantasy League"
                error_embed = discord.Embed(
                    title=f"🏈 {league_name} Live Scoreboard",
                    description="❌ No matchups found for this week.",
                    color=0xFF0000
                )
                embeds.append(error_embed)

            return embeds

        # Create initial embeds
        embeds = create_scoreboard_embeds()

        if auto_refresh:
            view = ScoreboardView(league, current_week, auto_refresh, user_id=interaction.user.id)
            message = await interaction.followup.send(embeds=embeds, view=view)
            view._message = message  # Store message reference for auto-refresh
        else:
            await interaction.followup.send(embeds=embeds)

    except Exception as e:
        error_msg = f"Error creating scoreboard: {e}"
        print(f"Scoreboard error: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(error_msg, ephemeral=True)
        else:
            await interaction.followup.send(error_msg, ephemeral=True)

class ScoreboardView(View):
    def __init__(self, league, current_week, auto_refresh=True, user_id=None, all_embeds=None):
        super().__init__(timeout=None)  # No timeout - make it persistent
        self.league = league
        self.current_week = current_week
        self.auto_refresh = auto_refresh
        self.user_id = user_id  # Store user ID to get current league on refresh
        self.last_refresh = None
        self.all_embeds = all_embeds or []
        self.current_page = 0
        self.max_embeds_per_page = 4  # Show header + 2 matchup embeds + summary per page

        if auto_refresh:
            self.refresh_task = asyncio.create_task(self.auto_refresh_loop())

    async def auto_refresh_loop(self):
        """Auto-refresh the scoreboard every 30 seconds"""
        consecutive_errors = 0
        max_consecutive_errors = 10

        try:
            while self.auto_refresh and consecutive_errors < max_consecutive_errors:
                await asyncio.sleep(30)  # Wait 30 seconds

                if not self.auto_refresh:
                    break

                try:
                    # Create updated embeds with error handling
                    embeds = self.create_updated_embeds()
                    if not embeds:
                        print("No embeds created, skipping update")
                        continue

                    # Update timestamp in header
                    import datetime
                    now = datetime.datetime.now()
                    if embeds:
                        embeds[0].set_footer(text=f"Last updated: {now.strftime('%I:%M:%S %p')}")

                    consecutive_errors = 0  # Reset error count on success
                except Exception as e:
                    consecutive_errors += 1
                    print(f"Error updating scoreboard (attempt {consecutive_errors}): {e}")
                    # Continue the loop, skip this update
                    continue

                # Try to edit the message with enhanced error handling
                try:
                    if hasattr(self, '_message') and self._message:
                        await self._message.edit(embeds=embeds, view=self)
                        print(f"Auto-refresh successful at {datetime.datetime.now().strftime('%I:%M:%S %p')}")
                except discord.errors.NotFound:
                    print("Auto-refresh stopped: Message was deleted")
                    break
                except discord.errors.Forbidden:
                    print("Auto-refresh stopped: No permission to edit message")
                    break
                except discord.errors.HTTPException as e:
                    consecutive_errors += 1
                    print(f"Auto-refresh HTTP error (attempt {consecutive_errors}): {e}")
                    if consecutive_errors >= max_consecutive_errors:
                        print("Too many consecutive errors, stopping auto-refresh")
                        self.auto_refresh = False
                        break
                    continue
                except Exception as e:
                    consecutive_errors += 1
                    print(f"Auto-refresh unexpected error (attempt {consecutive_errors}): {e}")
                    if consecutive_errors >= max_consecutive_errors:
                        print("Too many consecutive errors, stopping auto-refresh")
                        self.auto_refresh = False
                        break
                    continue

        except asyncio.CancelledError:
            print("Auto-refresh loop cancelled")
        except Exception as e:
            print(f"Auto-refresh loop fatal error: {e}")
        finally:
            print("Auto-refresh loop ended")

    def create_updated_embeds(self):
        """Create updated embeds with current scores"""
        try:
            # Refresh league data using user's current default league
            if self.user_id:
                league = get_league(user_id=self.user_id)
                if not league:
                    # Fallback to stored league if user has no default
                    league = self.league
            else:
                # Fallback for backwards compatibility
                if ESPN_S2 and SWID:
                    league = League(league_id=LEAGUE_ID, year=SEASON_ID, swid=SWID, espn_s2=ESPN_S2)
                else:
                    league = League(league_id=LEAGUE_ID, year=SEASON_ID)

            # Update the instance variable
            self.league = league

            # Get updated matchups (same logic as main function)
            matchups = []
            teams_in_matchups = set()

            for team in self.league.teams:
                if team.team_id in teams_in_matchups:
                    continue

                # Find opponent
                opponent = None
                if hasattr(team, 'schedule') and len(team.schedule) >= self.current_week:
                    try:
                        week_opponent = team.schedule[self.current_week - 1]
                        if hasattr(week_opponent, 'team_id'):
                            opponent = week_opponent
                        elif hasattr(week_opponent, 'opponent'):
                            opponent = week_opponent.opponent
                    except (IndexError, AttributeError):
                        pass

                # Alternative method: check box scores
                if not opponent:
                    try:
                        box_score = self.league.box_scores(self.current_week)
                        for matchup in box_score:
                            if hasattr(matchup, 'home_team') and hasattr(matchup, 'away_team'):
                                if matchup.home_team.team_id == team.team_id:
                                    opponent = matchup.away_team
                                    break
                                elif matchup.away_team.team_id == team.team_id:
                                    opponent = matchup.home_team
                                    break
                    except Exception:
                        pass

                if opponent and opponent.team_id not in teams_in_matchups:
                    # Try multiple methods to get current scores
                    team_score = 0
                    opponent_score = 0

                    # Method 1: Try box scores API
                    try:
                        box_scores = self.league.box_scores(self.current_week)
                        for box_score in box_scores:
                            if hasattr(box_score, 'home_team') and hasattr(box_score, 'away_team'):
                                if box_score.home_team.team_id == team.team_id:
                                    team_score = getattr(box_score.home_score, 'total_points', 0) or getattr(box_score, 'home_score', 0)
                                    opponent_score = getattr(box_score.away_score, 'total_points', 0) or getattr(box_score, 'away_score', 0)
                                    break
                                elif box_score.away_team.team_id == team.team_id:
                                    team_score = getattr(box_score.away_score, 'total_points', 0) or getattr(box_score, 'away_score', 0)
                                    opponent_score = getattr(box_score.home_score, 'total_points', 0) or getattr(box_score, 'home_score', 0)
                                    break
                    except Exception as e:
                        print(f"Box score refresh method failed: {e}")
                        pass

                    # Method 2: Try team.scores if box scores didn't work
                    if team_score == 0 and opponent_score == 0:
                        try:
                            if hasattr(team, 'scores') and len(team.scores) >= self.current_week:
                                team_score = team.scores[self.current_week - 1] or 0
                            if hasattr(opponent, 'scores') and len(opponent.scores) >= self.current_week:
                                opponent_score = opponent.scores[self.current_week - 1] or 0
                        except (IndexError, AttributeError):
                            pass

                    # Method 3: Calculate actual points only (no projected scores)
                    if team_score == 0 and opponent_score == 0:
                        def get_actual_points_only(player, league_ref):
                            """Get only actual points, not projected"""
                            current_week = getattr(league_ref, 'current_week', 1)
                            if hasattr(player, 'stats') and player.stats:
                                try:
                                    week_stats = player.stats.get(current_week, {})
                                    actual_points = week_stats.get('points', None)
                                    if actual_points is not None and actual_points > 0:
                                        return actual_points

                                    # Check applied stats for actual game performance
                                    applied_stats = week_stats.get('appliedStats', {})
                                    if applied_stats and len(applied_stats) > 0:
                                        # Calculate points from actual stats if available
                                        total_points = 0
                                        for stat_id, value in applied_stats.items():
                                            if isinstance(value, (int, float)) and value > 0:
                                                total_points += value
                                        if total_points > 0:
                                            return total_points
                                except:
                                    pass
                            return 0

                        starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                        team_score = sum(get_actual_points_only(p, self.league) for p in starters)

                        opp_starters = [p for p in opponent.roster if getattr(p, 'lineupSlot', None) != "BE"]
                        opponent_score = sum(get_actual_points_only(p, self.league) for p in opp_starters)

                    # Calculate projected scores for both teams
                    def get_weekly_projected_points(player, league_ref):
                        """Get weekly projected points for a player"""
                        current_week = getattr(league_ref, 'current_week', 1)
                        if hasattr(player, 'stats') and player.stats:
                            try:
                                week_stats = player.stats.get(current_week, {})
                                projected = week_stats.get('projected_points', None)
                                if projected is not None:
                                    return projected
                            except:
                                pass
                        # Fallback to other projection attributes
                        for attr in ['proj_points', 'projected_points']:
                            value = getattr(player, attr, None)
                            if value is not None:
                                return value
                        return 0

                    starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    team_projected = sum(get_weekly_projected_points(p, self.league) for p in starters)

                    opp_starters = [p for p in opponent.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    opponent_projected = sum(get_weekly_projected_points(p, self.league) for p in opp_starters)

                    matchups.append({
                        'team1': team,
                        'team2': opponent,
                        'score1': team_score,
                        'score2': opponent_score,
                        'proj1': team_projected,
                        'proj2': opponent_projected
                    })

                    teams_in_matchups.add(team.team_id)
                    teams_in_matchups.add(opponent.team_id)

            # Sort matchups by total points (most exciting games first)
            matchups.sort(key=lambda m: m['score1'] + m['score2'], reverse=True)

            # Create individual embeds for each matchup
            embeds = []

            if matchups:
                # Create header embed
                league_name = get_league_name(user_id=self.user_id)
                header_embed = discord.Embed(
                    title=f"🏈 {league_name} Live Scoreboard",
                    description=f"Week {self.current_week} Matchups • {('🔄 Auto-refresh ON' if self.auto_refresh else '📊 Static view')}",
                    color=0xFF6B35
                )

                # Add refresh timestamp
                import datetime
                now = datetime.datetime.now()
                header_embed.set_footer(text=f"Last updated: {now.strftime('%I:%M:%S %p')}")
                embeds.append(header_embed)

                # Get remaining players info function
                def get_remaining_players(team, league_ref):
                    starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                    still_playing = 0
                    total_starters = len(starters)

                    for player in starters:
                        proj_points = get_current_week_points(player, league_ref)
                        if proj_points != 'N/A' and proj_points > 0:
                            still_playing += 1

                    # For demo, randomize a bit to show realistic variations
                    import random
                    if still_playing == total_starters:
                        still_playing = random.randint(max(1, total_starters-3), total_starters)

                    return f"{still_playing}/{total_starters}"

                # Helper function to format team names
                def format_team_name(name, max_length=7):
                    words = name.split()

                    if len(words) == 1:
                        # Single word: pad with spaces to match multi-word format length
                        if len(name) <= max_length:
                            return name + "  "  # Add 2 spaces to match " X." format
                        else:
                            return name[:max_length-2] + "  "

                    # Two or more words: first word + first letter of second word + period
                    result = f"{words[0]} {words[1][0]}."
                    if len(result) <= max_length:
                        return result
                    else:
                        # Truncate first word if needed to fit format
                        return words[0][:max_length-3] + " " + words[1][0] + "."

                # Build simple vs-style lines
                all_table_lines = []

                # First pass: calculate the longest team name to determine optimal spacing
                max_name_length = 0
                formatted_matchups = []

                for matchup in matchups:
                    team1 = matchup['team1']
                    team2 = matchup['team2']
                    score1 = matchup['score1']
                    score2 = matchup['score2']
                    proj1 = matchup['proj1']
                    proj2 = matchup['proj2']

                    # Get remaining players for each team first
                    team1_remaining = get_remaining_players(team1, league)
                    team2_remaining = get_remaining_players(team2, league)

                    # Format team names with shorter length to prevent overflow
                    base_name1 = format_team_name(team1.team_name, 7)
                    base_name2 = format_team_name(team2.team_name, 7)

                    # Ensure consistent formatting by padding remaining player counts
                    # This handles both single digit (8/9) and double digit (11/11) counts
                    name1 = f"{base_name1} ({team1_remaining})"
                    name2 = f"{base_name2} ({team2_remaining})"

                    # Pad names to ensure consistent alignment - reduced to prevent overflow
                    name1 = f"{name1:<14}"
                    name2 = f"{name2:<14}"

                    formatted_matchups.append({
                        'name1': name1,
                        'name2': name2,
                        'score1': score1,
                        'score2': score2,
                        'proj1': proj1,
                        'proj2': proj2
                    })

                    # Track max length for dynamic spacing
                    max_name_length = max(max_name_length, len(name1), len(name2))

                # Use fixed spacing for consistent alignment across all leagues
                # Account for double digit player counts: team names get 14 characters
                left_spacing = 14

                # Second pass: format with consistent spacing
                for matchup_data in formatted_matchups:
                    name1 = matchup_data['name1']
                    name2 = matchup_data['name2']
                    score1 = matchup_data['score1']
                    score2 = matchup_data['score2']
                    # Handle missing projected scores gracefully
                    proj1 = matchup_data.get('proj1', 0)
                    proj2 = matchup_data.get('proj2', 0)

                    # Format scores with better spacing and readability
                    score1_str = f"{score1:>4.1f}|{proj1:<5.1f}"
                    score2_str = f"{score2:>4.1f}|{proj2:<5.1f}"

                    # Add winner arrows pointing toward the winning team - compact format
                    score1_compact = f"{score1:>4.1f}|{proj1:<5.1f}"
                    score2_compact = f"{score2:>4.1f}|{proj2:<5.1f}"

                    if score1 > score2:
                        # Team 1 winning - arrow points left toward winning team
                        line = f"{name1:<{left_spacing}} {score1_compact:<11} ◀ {score2_compact:<11} {name2}"
                    elif score2 > score1:
                        # Team 2 winning - arrow points right toward winning team
                        line = f"{name1:<{left_spacing}} {score1_compact:<11} ▶ {score2_compact:<11} {name2}"
                    else:
                        # Tied
                        line = f"{name1:<{left_spacing}} {score1_compact:<11} = {score2_compact:<11} {name2}"

                    all_table_lines.append(line)

                # Add header to explain format with proper alignment
                if all_table_lines:
                    header_line = f"{'Team (Rem.)':<{left_spacing}} {'Act|Proj':<11} {'VS'} {'Act|Proj':<11} {'Team (Rem.)'}"
                    separator_line = "─" * (len(header_line) - 10)  # Adjust for visual length
                    all_table_lines.insert(0, separator_line)
                    all_table_lines.insert(0, header_line)

                # Create mobile-friendly matchup display instead of table
                # Create main matchups embed using SafeEmbedBuilder
                matchups_embed = SafeEmbedBuilder.create()
                matchups_embed.set_title("📊 Live Matchups")
                matchups_embed.set_color(0x32CD32)

                # Add each matchup as a field - mobile friendly
                for i, matchup in enumerate(matchups):
                    team1 = matchup['team1']
                    team2 = matchup['team2']
                    score1 = matchup['score1']
                    score2 = matchup['score2']
                    proj1 = matchup.get('proj1', 0)
                    proj2 = matchup.get('proj2', 0)

                    # Determine status
                    if score1 > score2:
                        status_emoji = "🔥"
                        status_text = f"{team1.team_name} leading"
                    elif score2 > score1:
                        status_emoji = "🔥"
                        status_text = f"{team2.team_name} leading"
                    else:
                        status_emoji = "⚖️"
                        status_text = "Tied game"

                    field_name = f"{status_emoji} {team1.team_name} vs {team2.team_name}"
                    field_value = (
                        f"**{team1.team_name}**: `{score1:.1f}` pts (proj: `{proj1:.1f}`)\n"
                        f"**{team2.team_name}**: `{score2:.1f}` pts (proj: `{proj2:.1f}`)\n"
                        f"*{status_text}*"
                    )

                    matchups_embed.add_field(name=field_name, value=field_value, inline=False)

                embeds.append(matchups_embed.build())

                # Create summary embed
                total_points = sum(m['score1'] + m['score2'] for m in matchups)
                avg_game_total = total_points / len(matchups) if matchups else 0
                highest_score = max(max(m['score1'], m['score2']) for m in matchups) if matchups else 0
                closest_game = min(abs(m['score1'] - m['score2']) for m in matchups) if matchups else 0

                summary_embed = discord.Embed(
                    title="📋 Week Summary",
                    color=0x9932CC
                )

                summary_lines = []
                summary_lines.append(f"🎯 **Total Points Scored**: {total_points:.1f}")
                summary_lines.append(f"📈 **Average Game Total**: {avg_game_total:.1f}")
                summary_lines.append(f"🔥 **Highest Individual Score**: {highest_score:.1f}")
                summary_lines.append(f"⚡ **Closest Game**: {closest_game:.1f} point difference")

                summary_embed.add_field(name="Stats", value="\n".join(summary_lines), inline=False)
                embeds.append(summary_embed)

            else:
                league_name = get_league_name(user_id=self.user_id) if self.user_id else "Fantasy League"
                error_embed = discord.Embed(
                    title=f"🏈 {league_name} Live Scoreboard",
                    description="❌ No matchups found for this week.",
                    color=0xFF0000
                )
                embeds.append(error_embed)

        except Exception as e:
            league_name = get_league_name(user_id=self.user_id) if self.user_id else "Fantasy League"
            error_embed = discord.Embed(
                title=f"🏈 {league_name} Live Scoreboard",
                description=f"❌ Failed to refresh: {e}",
                color=0xFF0000
            )
            embeds = [error_embed]

        return embeds

    def get_current_page_embeds(self):
        """Get header + current matchup embed"""
        if not self.all_embeds:
            return []

        # Always include header (first embed)
        header_embed = self.all_embeds[0]

        # Get matchup embeds (exclude header and summary)
        matchup_embeds = self.all_embeds[1:-1] if len(self.all_embeds) > 2 else []
        summary_embed = self.all_embeds[-1] if len(self.all_embeds) > 1 else None

        if not matchup_embeds:
            # If no matchup embeds, show header + summary
            return [header_embed, summary_embed] if summary_embed else [header_embed]

        # Show header + current matchup page
        if self.current_page < len(matchup_embeds):
            current_matchup = matchup_embeds[self.current_page]

            # If this is the last matchup page, also include summary
            if self.current_page == len(matchup_embeds) - 1 and summary_embed:
                return [header_embed, current_matchup, summary_embed]
            else:
                return [header_embed, current_matchup]
        else:
            # Fallback to first matchup
            return [header_embed, matchup_embeds[0]]

    def get_total_pages(self):
        """Calculate total number of pages (matchup embeds only)"""
        if len(self.all_embeds) <= 2:  # Just header + summary
            return 1

        matchup_embeds = len(self.all_embeds) - 2  # Exclude header and summary
        return max(1, matchup_embeds)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            page_embeds = self.get_current_page_embeds()

            # Update embed footer to show page info
            if page_embeds:
                page_embeds[0].set_footer(
                    text=f"Page {self.current_page + 1}/{self.get_total_pages()} • " +
                         (page_embeds[0].footer.text.split(" • ", 1)[1] if page_embeds[0].footer.text else "")
                )

            await interaction.response.edit_message(embeds=page_embeds, view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to next page"""
        if self.current_page < self.get_total_pages() - 1:
            self.current_page += 1
            page_embeds = self.get_current_page_embeds()

            # Update embed footer to show page info
            if page_embeds:
                page_embeds[0].set_footer(
                    text=f"Page {self.current_page + 1}/{self.get_total_pages()} • " +
                         (page_embeds[0].footer.text.split(" • ", 1)[1] if page_embeds[0].footer.text else "")
                )

            await interaction.response.edit_message(embeds=page_embeds, view=self)

    @discord.ui.button(label="🔄 Refresh Now", style=discord.ButtonStyle.primary, row=1)
    async def manual_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Manual refresh button"""
        try:
            await interaction.response.defer()

            embeds = self.create_updated_embeds()
            if not embeds:
                await interaction.followup.send("❌ Failed to create updated scoreboard", ephemeral=True)
                return

            # Update timestamp in header
            import datetime
            now = datetime.datetime.now()
            if embeds:
                embeds[0].set_footer(text=f"Last updated: {now.strftime('%I:%M:%S %p')}")

            await interaction.edit_original_response(embeds=embeds, view=self)
            print(f"Manual refresh successful at {now.strftime('%I:%M:%S %p')}")

        except discord.errors.InteractionResponded:
            # Interaction already responded to
            try:
                embeds = self.create_updated_embeds()
                if embeds:
                    await interaction.edit_original_response(embeds=embeds, view=self)
            except Exception as e:
                await interaction.followup.send(f"❌ Refresh failed: {str(e)[:100]}", ephemeral=True)
        except Exception as e:
            print(f"Manual refresh error: {e}")
            try:
                await interaction.followup.send(f"❌ Refresh failed: {str(e)[:100]}", ephemeral=True)
            except:
                pass

    @discord.ui.button(label="⏸️ Stop Auto-Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Toggle auto-refresh on/off"""
        await interaction.response.defer()

        if self.auto_refresh:
            self.auto_refresh = False
            if hasattr(self, 'refresh_task'):
                self.refresh_task.cancel()
            button.label = "▶️ Start Auto-Refresh"
            button.style = discord.ButtonStyle.success
        else:
            self.auto_refresh = True
            self.refresh_task = asyncio.create_task(self.auto_refresh_loop())
            button.label = "⏸️ Stop Auto-Refresh"
            button.style = discord.ButtonStyle.secondary

        # Update embeds
        embeds = self.create_updated_embeds()
        # Update header embed description
        if embeds:
            embeds[0].description = f"Week {self.current_week} Matchups • {('🔄 Auto-refresh ON' if self.auto_refresh else '📊 Static view')}"

        await interaction.edit_original_response(embeds=embeds, view=self)

    async def on_timeout(self):
        """Handle view timeout - should not occur with timeout=None"""
        print("ScoreboardView timed out unexpectedly")
        if hasattr(self, 'refresh_task'):
            self.refresh_task.cancel()
        self.auto_refresh = False

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
            # Don't show status for D/ST positions
            pos = getattr(player, 'position', '')
            if pos in ['D/ST', 'DST', 'DEF']:
                return ''

            status = getattr(player, 'injuryStatus', None)
            abbrev = status_abbrev.get(status, status_abbrev.get('NORMAL', ''))

            # Don't show status for Available players (A or N)
            if abbrev in ['A', 'N']:
                return ''

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
                color=0x00ff00
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
                color=0xffa500
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🏈 My Fantasy Leagues",
            description=f"**{len(user_leagues)} League{'s' if len(user_leagues) != 1 else ''} Registered**",
            color=0x0099ff
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
                color=0x00ff00
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
                color=0xff6b6b
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
            color=0x0099ff
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
                color=0xffa500
            )
            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="🌐 All Available Leagues",
            description=f"**{len(all_leagues)} League{'s' if len(all_leagues) != 1 else ''} Available** for cross-league commands",
            color=0x0099ff
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

@client.tree.command(name="compare_cross_league", description="Compare teams from different leagues.")
@app_commands.describe(
    team1="First team name",
    league1="League name for first team (optional, uses your default)",
    team2="Second team name",
    league2="League name for second team (optional, uses your default)"
)
async def compare_cross_league(interaction: discord.Interaction, team1: str, team2: str, league1: str = None, league2: str = None):
    """Compare teams from potentially different leagues"""
    try:
        await interaction.response.defer()

        # Get leagues for comparison
        if league1:
            # Find league by name
            league1_matches = league_manager.find_leagues_by_name(league1)
            if not league1_matches:
                all_leagues = league_manager.get_all_leagues()
                available_names = [l['name'] for l in all_leagues]
                await interaction.followup.send(f"❌ League '{league1}' not found.\n\nAvailable leagues: {', '.join(available_names)}")
                return
            league1_obj = league_manager.get_league_by_key(league1_matches[0]['key'])
            if not league1_obj:
                await interaction.followup.send(f"❌ Failed to connect to league '{league1_matches[0]['name']}'.")
                return
            league1_name = league1_matches[0]['name']
        else:
            # Use user's default league
            league1_obj = get_league(user_id=interaction.user.id)
            if not league1_obj:
                await interaction.followup.send("❌ No default league found. Register a league or specify league1 parameter.")
                return
            # Get league name
            user_leagues = league_manager.get_user_leagues(interaction.user.id)
            user_data = league_manager.data['users'].get(str(interaction.user.id), {})
            default_league_key = user_data.get('default_league')
            if default_league_key and default_league_key in league_manager.data['leagues']:
                league1_name = league_manager.data['leagues'][default_league_key]['name']
            else:
                league1_name = "Your League"

        if league2:
            # Find league by name
            league2_matches = league_manager.find_leagues_by_name(league2)
            if not league2_matches:
                await interaction.followup.send(f"❌ League '{league2}' not found. Use `/all_leagues` to see available leagues.")
                return
            league2_obj = league_manager.get_league_by_key(league2_matches[0]['key'])
            league2_name = league2_matches[0]['name']
        else:
            # Use user's default league (same as league1 if not specified)
            league2_obj = league1_obj
            league2_name = league1_name

        if not league1_obj or not league2_obj:
            await interaction.followup.send("❌ Failed to connect to one or both leagues.")
            return

        # Find teams
        team1_obj = next((t for t in league1_obj.teams if t.team_name.lower() == team1.lower()), None)
        team2_obj = next((t for t in league2_obj.teams if t.team_name.lower() == team2.lower()), None)

        if not team1_obj:
            await interaction.followup.send(f"❌ Team '{team1}' not found in {league1_name}.")
            return
        if not team2_obj:
            await interaction.followup.send(f"❌ Team '{team2}' not found in {league2_name}.")
            return

        # Create comparison embed
        embed = discord.Embed(
            title="⚔️ Cross-League Team Comparison",
            color=0xff6b35
        )

        # Team 1 info
        team1_record = f"{team1_obj.wins}-{team1_obj.losses}"
        team1_points = team1_obj.points_for
        embed.add_field(
            name=f"🔵 {team1_obj.team_name}",
            value=f"**League:** {league1_name}\n**Record:** {team1_record}\n**Points For:** {team1_points:.1f}",
            inline=True
        )

        # Team 2 info
        team2_record = f"{team2_obj.wins}-{team2_obj.losses}"
        team2_points = team2_obj.points_for
        embed.add_field(
            name=f"🔴 {team2_obj.team_name}",
            value=f"**League:** {league2_name}\n**Record:** {team2_record}\n**Points For:** {team2_points:.1f}",
            inline=True
        )

        # Comparison stats
        comparison_text = []
        if team1_points > team2_points:
            comparison_text.append(f"🔵 {team1_obj.team_name} leads in total points (+{team1_points - team2_points:.1f})")
        elif team2_points > team1_points:
            comparison_text.append(f"🔴 {team2_obj.team_name} leads in total points (+{team2_points - team1_points:.1f})")
        else:
            comparison_text.append("🟡 Teams tied in total points")

        # Win percentage comparison
        team1_win_pct = team1_obj.wins / (team1_obj.wins + team1_obj.losses) if (team1_obj.wins + team1_obj.losses) > 0 else 0
        team2_win_pct = team2_obj.wins / (team2_obj.wins + team2_obj.losses) if (team2_obj.wins + team2_obj.losses) > 0 else 0

        if team1_win_pct > team2_win_pct:
            comparison_text.append(f"🔵 {team1_obj.team_name} has better win rate ({team1_win_pct:.1%} vs {team2_win_pct:.1%})")
        elif team2_win_pct > team1_win_pct:
            comparison_text.append(f"🔴 {team2_obj.team_name} has better win rate ({team2_win_pct:.1%} vs {team1_win_pct:.1%})")
        else:
            comparison_text.append(f"🟡 Teams have same win rate ({team1_win_pct:.1%})")

        # Points per game
        team1_ppg = team1_points / max(team1_obj.wins + team1_obj.losses, 1)
        team2_ppg = team2_points / max(team2_obj.wins + team2_obj.losses, 1)

        comparison_text.append(f"📊 Points per game: {team1_obj.team_name} ({team1_ppg:.1f}) vs {team2_obj.team_name} ({team2_ppg:.1f})")

        embed.add_field(
            name="📈 Comparison",
            value="\n".join(comparison_text),
            inline=False
        )

        # Helper functions for player data (reused from other commands)
        def get_actual_points(player, league_ref):
            """Get actual points for current week"""
            try:
                current_week = getattr(league_ref, 'current_week', 1)
                if hasattr(player, 'stats') and player.stats:
                    week_stats = player.stats.get(current_week, {})
                    actual_points = week_stats.get('points', None)
                    if actual_points is not None and actual_points > 0:
                        return actual_points
            except:
                pass
            return 0

        def get_player_status(player):
            """Get injury status"""
            status_abbrev = {
                'ACTIVE': '', 'QUESTIONABLE': 'Q', 'OUT': 'O', 'INJURY_RESERVE': 'IR', 'NORMAL': '', None: ''
            }
            # Don't show status for D/ST
            pos = getattr(player, 'position', '')
            if pos in ['D/ST', 'DST', 'DEF']:
                return ''

            status = getattr(player, 'injuryStatus', None)
            abbrev = status_abbrev.get(status, '')
            return f" ({abbrev})" if abbrev else ''

        def create_team_roster_text(team, league_ref, team_name):
            """Create roster text with weekly points"""
            starters = [p for p in team.roster if getattr(p, 'lineupSlot', None) != "BE"]

            # Group by position with proper ordering
            position_order = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'D/ST', 'DST']

            def get_position_priority(player):
                pos = getattr(player, 'position', 'FLEX')
                return position_order.index(pos) if pos in position_order else 99

            starters_sorted = sorted(starters, key=get_position_priority)

            lines = []
            lines.append(f"{'Pos':<3} {'Player':<18} {'Pts':<4}")
            lines.append(f"{'-'*3} {'-'*18} {'-'*4}")

            total_points = 0
            for player in starters_sorted:
                pos = getattr(player, 'position', 'FLEX')[:3]
                status = get_player_status(player)
                name = (player.name + status)[:18]  # Truncate with status
                actual = get_actual_points(player, league_ref)
                total_points += actual

                lines.append(f"{pos:<3} {name:<18} {actual:<4.1f}")

            lines.append(f"{'-'*3} {'-'*18} {'-'*4}")
            lines.append(f"{'TOT':<3} {'TOTAL':<18} {total_points:<4.1f}")

            return f"```\n{chr(10).join(lines)}\n```", total_points

        # Get current week rosters with points
        team1_roster_text, team1_week_total = create_team_roster_text(team1_obj, league1_obj, team1_obj.team_name)
        team2_roster_text, team2_week_total = create_team_roster_text(team2_obj, league2_obj, team2_obj.team_name)

        # Add roster comparisons
        current_week = getattr(league1_obj, 'current_week', getattr(league2_obj, 'current_week', 'Unknown'))

        embed.add_field(
            name=f"📋 {team1_obj.team_name} - Week {current_week} Lineup",
            value=team1_roster_text,
            inline=True
        )

        embed.add_field(
            name=f"📋 {team2_obj.team_name} - Week {current_week} Lineup",
            value=team2_roster_text,
            inline=True
        )

        # Weekly scoring comparison
        weekly_comparison = []
        if team1_week_total > team2_week_total:
            weekly_comparison.append(f"🔵 {team1_obj.team_name} leading this week (+{team1_week_total - team2_week_total:.1f} pts)")
        elif team2_week_total > team1_week_total:
            weekly_comparison.append(f"🔴 {team2_obj.team_name} leading this week (+{team2_week_total - team1_week_total:.1f} pts)")
        else:
            weekly_comparison.append("🟡 Teams tied this week")

        embed.add_field(
            name=f"⚡ Week {current_week} Battle",
            value="\n".join(weekly_comparison),
            inline=False
        )

        # Note about cross-league comparison
        if league1_name != league2_name:
            embed.add_field(
                name="ℹ️ Note",
                value="This is a cross-league comparison. Different leagues may have different scoring systems, rules, and competition levels.",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ Error comparing teams: {str(e)}")

@client.tree.command(name="league_info", description="Display detailed league settings and configuration.")
async def league_info(interaction: discord.Interaction):
    """Show comprehensive league information and settings"""
    # Use safe defer first
    if not await safe_defer(interaction):
        return

    try:
        league = get_league(user_id=interaction.user.id)
        if not league:
            await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
            return

        league_name = get_league_name(user_id=interaction.user.id)
        current_week = getattr(league, 'current_week', 'Unknown')

        # Create main embed
        embed = discord.Embed(
            title=f"🏈 {league_name}",
            description=f"**League Configuration & Settings**",
            color=0x0099ff
        )

        # Get league settings first
        settings = getattr(league, 'settings', None)
        scoring_format = "Unknown"

        if settings:
            # Scoring format - determine PPR/Half-PPR/Standard
            try:
                if hasattr(settings, 'scoring_format') and isinstance(settings.scoring_format, list):
                    # Look for reception scoring in the detailed rules
                    for rule in settings.scoring_format:
                        if isinstance(rule, dict) and rule.get('abbr') == 'REC':
                            points = rule.get('points', 0)
                            if points == 1.0:
                                scoring_format = "PPR (Full Point)"
                            elif points == 0.5:
                                scoring_format = "Half-PPR"
                            elif points == 0:
                                scoring_format = "Standard (No PPR)"
                            else:
                                scoring_format = f"Custom PPR ({points} pts)"
                            break
                    else:
                        # If no REC rule found, assume Standard
                        scoring_format = "Standard (No PPR)"
                elif hasattr(settings, 'scoring_type'):
                    scoring_format = str(settings.scoring_type)
            except:
                scoring_format = "Unknown"

        # Basic Info - using inline fields for better layout
        embed.add_field(
            name="🆔 League ID",
            value=f"`{league.league_id}`",
            inline=True
        )
        embed.add_field(
            name="📅 Season",
            value=f"**{league.year}**",
            inline=True
        )
        embed.add_field(
            name="📍 Current Week",
            value=f"**Week {current_week}**",
            inline=True
        )

        embed.add_field(
            name="👥 Teams",
            value=f"**{len(league.teams)} Teams**",
            inline=True
        )
        embed.add_field(
            name="🏈 Scoring Format",
            value=f"**{scoring_format}**",
            inline=True
        )

        # Add playoff info if available
        playoff_info = "TBD"
        if settings:
            if hasattr(settings, 'playoff_team_count'):
                playoff_info = f"**{settings.playoff_team_count} Teams**"
                if hasattr(settings, 'playoff_week_start'):
                    playoff_info += f"\n*Starts Week {settings.playoff_week_start}*"

        embed.add_field(
            name="🏆 Playoffs",
            value=playoff_info,
            inline=True
        )

        # Add spacing with empty inline field
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # Roster Settings
        if hasattr(league, 'teams') and league.teams:
            # Analyze roster composition from first team
            sample_team = league.teams[0]
            if hasattr(sample_team, 'roster'):
                starters = [p for p in sample_team.roster if getattr(p, 'lineupSlot', None) != "BE"]
                bench = [p for p in sample_team.roster if getattr(p, 'lineupSlot', None) == "BE"]

                # Total roster info
                embed.add_field(
                    name="📊 Total Roster",
                    value=f"**{len(sample_team.roster)}** Players",
                    inline=True
                )
                embed.add_field(
                    name="🏃 Starting Lineup",
                    value=f"**{len(starters)}** Players",
                    inline=True
                )
                embed.add_field(
                    name="🪑 Bench",
                    value=f"**{len(bench)}** Players",
                    inline=True
                )

                # Count positions in starting lineup
                position_counts = {}
                for player in starters:
                    pos = getattr(player, 'position', 'UNKNOWN')
                    position_counts[pos] = position_counts.get(pos, 0) + 1

                if position_counts:
                    # Format position breakdown with better spacing
                    pos_breakdown = []
                    for pos, count in sorted(position_counts.items()):
                        if pos in ['D/ST', 'DST']:
                            pos_breakdown.append(f"**{pos}:** {count}")
                        else:
                            pos_breakdown.append(f"**{pos}:** {count}")

                    positions_text = "\n".join(pos_breakdown)
                    if len(positions_text) > 1024:
                        positions_text = positions_text[:1020] + "..."

                    embed.add_field(
                        name="🎯 Starting Positions",
                        value=positions_text,
                        inline=False
                    )

        # Add spacing
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # Scoring & Settings Details
        if settings:
            # Key scoring highlights
            scoring_details = []
            try:
                if hasattr(settings, 'scoring_format') and isinstance(settings.scoring_format, list):
                    # Look for key scoring rules
                    key_scores = {}
                    for rule in settings.scoring_format:
                        if isinstance(rule, dict):
                            abbr = rule.get('abbr', '')
                            points = rule.get('points', 0)
                            if abbr == 'RTD':  # Rushing TD
                                key_scores['🏃 Rushing TD'] = f"**{points}** pts"
                            elif abbr == 'RETD':  # Receiving TD
                                key_scores['🤲 Receiving TD'] = f"**{points}** pts"
                            elif abbr == 'PTD':  # Passing TD
                                key_scores['🎯 Passing TD'] = f"**{points}** pts"

                    if key_scores:
                        for score_type, points in key_scores.items():
                            scoring_details.append(f"{score_type}: {points}")
            except:
                pass

            if scoring_details:
                scoring_text = "\n".join(scoring_details)
                if len(scoring_text) > 1024:
                    scoring_text = scoring_text[:1020] + "..."

                embed.add_field(
                    name="⚡ Key Scoring Rules",
                    value=scoring_text,
                    inline=True
                )

            # League rules
            league_rules = []

            # Regular season length
            if hasattr(settings, 'reg_season_count'):
                league_rules.append(f"📅 **Regular Season:** {settings.reg_season_count} weeks")

            # Trade settings
            if hasattr(settings, 'trade_deadline'):
                # Convert timestamp to week number if needed
                trade_deadline = settings.trade_deadline
                if isinstance(trade_deadline, (int, float)) and trade_deadline > 1000000000:
                    # This is a timestamp, convert to a readable format
                    import datetime
                    try:
                        date = datetime.datetime.fromtimestamp(trade_deadline / 1000)
                        trade_deadline_str = f"{date.strftime('%b %d, %Y')}"
                    except:
                        trade_deadline_str = "Unknown"
                elif isinstance(trade_deadline, (int, float)) and trade_deadline < 20:
                    # This is likely a week number
                    trade_deadline_str = f"Week {int(trade_deadline)}"
                else:
                    trade_deadline_str = str(trade_deadline)

                league_rules.append(f"🔄 **Trade Deadline:** {trade_deadline_str}")

            # Waiver settings
            if hasattr(settings, 'waiver_order_type'):
                league_rules.append(f"📋 **Waivers:** {settings.waiver_order_type}")

            if league_rules:
                rules_text = "\n".join(league_rules)
                if len(rules_text) > 1024:
                    rules_text = rules_text[:1020] + "..."

                embed.add_field(
                    name="📜 League Rules",
                    value=rules_text,
                    inline=True
                )

            # Add empty field for spacing if only one column filled
            if scoring_details and not league_rules:
                embed.add_field(name="\u200b", value="\u200b", inline=True)
            elif league_rules and not scoring_details:
                embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Add spacing before season stats
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        # Current Season Stats
        if hasattr(league, 'teams') and league.teams:
            total_points = sum(team.points_for for team in league.teams if hasattr(team, 'points_for'))
            avg_points = total_points / len(league.teams)

            # Total points
            embed.add_field(
                name="🎯 Total Points Scored",
                value=f"**{total_points:,.1f}** points",
                inline=True
            )

            # Average score
            embed.add_field(
                name="📈 Average Team Score",
                value=f"**{avg_points:.1f}** points",
                inline=True
            )

            # Find highest scoring team
            top_team = max(league.teams, key=lambda t: getattr(t, 'points_for', 0))
            embed.add_field(
                name="🏆 Top Scoring Team",
                value=f"**{top_team.team_name}**\n{top_team.points_for:.1f} points",
                inline=True
            )

        # Add footer with additional info
        embed.set_footer(text="💡 Try /standings, /stats, or /team for detailed analysis")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        error_msg = f"❌ Error getting league info: {str(e)}"
        print(f"League info error: {e}")
        await safe_interaction_response(interaction, error_msg, ephemeral=True)

@client.tree.command(name="insights", description="Weekly analytics dashboard with league trends and insights.")
async def insights(interaction: discord.Interaction):
    """Generate weekly analytics and insights for the league"""
    try:
        await interaction.response.defer()

        # Get league data
        league = get_league(user_id=interaction.user.id)
        if not league:
            await interaction.followup.send("❌ No league found. Use `/register_league` to add your ESPN Fantasy League first.", ephemeral=True)
            return

        current_week = getattr(league, 'current_week', 1)
        league_name = get_league_name(user_id=interaction.user.id)

        # Create analytics embed
        embed = SafeEmbedBuilder.create()
        embed.set_title(f"📊 {league_name} Analytics Dashboard - Week {current_week}")
        embed.set_color(0x1f8b4c)  # Green color

        # Collect team data
        teams_data = []
        total_points_this_week = 0
        highest_scorer = None
        lowest_scorer = None
        highest_score = 0
        lowest_score = float('inf')

        for team in league.teams:
            try:
                # Use simpler data that's already available
                total_points = getattr(team, 'points_for', 0)
                wins = getattr(team, 'wins', 0)
                losses = getattr(team, 'losses', 0)

                # Use a simple estimation for this week's score (total/games played)
                games_played = wins + losses
                estimated_week_score = total_points / max(games_played, 1) if games_played > 0 else 0

                total_points_this_week += estimated_week_score

                if estimated_week_score > highest_score:
                    highest_score = estimated_week_score
                    highest_scorer = team.team_name

                if estimated_week_score < lowest_score:
                    lowest_score = estimated_week_score
                    lowest_scorer = team.team_name

                teams_data.append({
                    'name': team.team_name,
                    'week_score': estimated_week_score,
                    'total_points': total_points,
                    'wins': wins,
                    'losses': losses
                })
            except Exception as e:
                print(f"Error processing team {team.team_name}: {e}")
                # Add basic team data even if there's an error
                teams_data.append({
                    'name': team.team_name,
                    'week_score': 0,
                    'total_points': getattr(team, 'points_for', 0),
                    'wins': getattr(team, 'wins', 0),
                    'losses': getattr(team, 'losses', 0)
                })

        # League overview
        avg_score = total_points_this_week / len(teams_data) if teams_data else 0
        total_teams = len(teams_data)

        overview_text = f"**📈 League Overview**\n"
        if highest_scorer and lowest_scorer:
            overview_text += f"🏆 Top Team: **{highest_scorer}** ({highest_score:.1f} avg)\n"
            overview_text += f"📉 Bottom Team: **{lowest_scorer}** ({lowest_score:.1f} avg)\n"
        overview_text += f"📊 League Average: **{avg_score:.1f}** pts per game\n"
        overview_text += f"👥 Active Teams: **{total_teams}**"

        embed.add_field(name="🎯 League Pulse", value=overview_text, inline=False)

        # Performance tiers
        if teams_data:
            # Sort by this week's performance
            sorted_teams = sorted(teams_data, key=lambda x: x['week_score'], reverse=True)

            # Top performers (top 25%)
            top_count = max(1, len(sorted_teams) // 4)
            top_performers = sorted_teams[:top_count]

            # Struggling teams (bottom 25%)
            bottom_count = max(1, len(sorted_teams) // 4)
            bottom_performers = sorted_teams[-bottom_count:]

            top_text = "\n".join([f"🔥 **{team['name']}** - {team['week_score']:.1f} pts"
                                for team in top_performers[:3]])  # Show top 3

            bottom_text = "\n".join([f"📉 **{team['name']}** - {team['week_score']:.1f} pts"
                                   for team in bottom_performers[:3]])  # Show bottom 3

            embed.add_field(name="🚀 Hot This Week", value=top_text, inline=True)
            embed.add_field(name="❄️ Cold This Week", value=bottom_text, inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)  # Spacing

        # Season trends
        if teams_data:
            season_leader = max(teams_data, key=lambda x: x['total_points'])
            most_wins = max(teams_data, key=lambda x: x['wins'])

            trends_text = f"👑 **Season Points Leader**\n{season_leader['name']} ({season_leader['total_points']:.1f} pts)\n\n"
            trends_text += f"🏆 **Most Wins**\n{most_wins['name']} ({most_wins['wins']}-{most_wins['losses']})"

            embed.add_field(name="📈 Season Trends", value=trends_text, inline=False)

        # Quick tips
        tips = [
            "💡 Check waiver wire for breakout players",
            "📊 Monitor player target share trends",
            "🏥 Keep an eye on injury reports",
            "📅 Plan ahead for bye weeks"
        ]

        embed.add_field(name="💡 Pro Tips", value="\n".join(tips), inline=False)
        embed.set_footer(text=f"🔄 Last updated: Week {current_week} • Use /standings for detailed rankings")

        await interaction.followup.send(embed=embed.build())

    except Exception as e:
        error_msg = f"❌ Error generating insights: {str(e)[:100]}"
        print(f"Insights command error: {e}")
        await interaction.followup.send(error_msg, ephemeral=True)

@client.tree.command(name="welcome", description="Get started guide for using the Fantasy Football bot.")
async def welcome(interaction: discord.Interaction):
    """Comprehensive welcome and setup guide"""
    embed = discord.Embed(
        title="🏈 Welcome to Fantasy Football Bot!",
        description="**Your complete guide to dominating fantasy football with data-driven insights**",
        color=0xFF6B35
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
              "📱 `/card` - Visual team cards\n"
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
        color=0x0099ff
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
