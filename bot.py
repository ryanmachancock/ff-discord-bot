print("Starting Fantasy Football bot...")

import os
import re
import asyncio
import functools
import inspect
import statistics
from datetime import datetime
import discord
from discord import app_commands
from dotenv import load_dotenv
from espn_api.football import League
import json
import time
from typing import Dict, Any, Optional
from image_cache import get_images, get_logos_by_url
from config.discord_display import CODE_BLOCK_MAX_CHARS

# Shared visual style so every command's embeds look like one bot instead of
# ~15 unrelated ad-hoc colors. BRAND = normal content, the rest are for
# state-confirmation moments only.
EMBED_COLOR_BRAND = 0x2F6B3A  # brand green used across every command's embed/Container accent
EMBED_COLOR_SUCCESS = 0x2ECC71
EMBED_COLOR_WARNING = 0xF1C40F

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
        """Refresh commonly accessed data.

        League(...) construction plus the `.teams` access that follows it
        is a real, blocking ESPN network call (same as get_league and
        _safe_box_scores elsewhere in this file) -- called directly, every
        league in this loop would freeze the WHOLE bot's event loop for
        its round trip, every 3 minutes, regardless of whether anyone was
        actively running a command. asyncio.to_thread runs each fetch on a
        worker thread instead, same fix as get_league."""
        def fetch_league_sync(league_id, year, swid=None, espn_s2=None):
            league = League(league_id=league_id, year=year, swid=swid, espn_s2=espn_s2) if swid and espn_s2 \
                else League(league_id=league_id, year=year)
            _ = league.teams  # trigger data loading
            return league

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
                    league = await asyncio.to_thread(
                        fetch_league_sync, league_info['league_id'], league_info['year'],
                        league_info.get('swid'), league_info.get('espn_s2'))

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
                        default_league = await asyncio.to_thread(fetch_league_sync, LEAGUE_ID, SEASON_ID, SWID, ESPN_S2)
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

# Team names change essentially never mid-season, so autocomplete gets its
# own long-lived cache instead of riding on espn_cache's 5-minute TTL --
# that TTL is sized for live score/roster data, and every expiry was forcing
# a full synchronous ESPN network call inline in the autocomplete handler
# (blocking the bot's whole event loop) just to re-read 12 team names.
_team_names_cache = {}
TEAM_NAMES_CACHE_TTL = 3600

async def team_name_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete function for team names"""
    try:
        cache_key = f"team_names_{interaction.user.id}"
        cached = _team_names_cache.get(cache_key)
        if cached and time.time() - cached[1] < TEAM_NAMES_CACHE_TTL:
            team_names = cached[0]
        else:
            league = await asyncio.to_thread(get_league, user_id=interaction.user.id)
            if not league:
                return []
            team_names = [team.team_name for team in league.teams]
            _team_names_cache[cache_key] = (team_names, time.time())

        if not current:
            return [app_commands.Choice(name=name, value=name) for name in team_names[:25]]

        current_lower = current.lower()
        starts_with = [name for name in team_names if name.lower().startswith(current_lower)]
        contains = [name for name in team_names if current_lower in name.lower() and name not in starts_with]
        return [app_commands.Choice(name=name, value=name) for name in (starts_with + contains)[:25]]

    except Exception as e:
        print(f"Team autocomplete error: {e}")
        return []

async def player_name_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete function for player names"""
    try:
        print(f"Player autocomplete called with input: '{current}'")  # Debug log

        # Try to get league (use cache if available)
        league = await asyncio.to_thread(get_league, user_id=interaction.user.id)
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

class ScoreHistory:
    """Lightweight persisted score-over-time log, used to draw sparklines on
    /matchup and /scoreboard. This bot has no cron/scheduled-task infra, so
    rather than add a background loop, samples are recorded opportunistically
    inside _safe_box_scores -- any command that already fetches box scores
    (matchup, scoreboard, team, compare) contributes a data point for free.
    A per-team/week throttle keeps repeated command calls from flooding the
    log with near-duplicate samples."""
    SAMPLE_INTERVAL_SECONDS = 600  # ponytail: fixed 10-min throttle, make configurable if that's ever wrong
    MAX_SAMPLES = 50  # a week's worth of 10-min samples is well under this

    def __init__(self):
        self.data_file = 'score_history.json'
        self.load_data()

    def load_data(self):
        try:
            with open(self.data_file, 'r') as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def save_data(self):
        with open(self.data_file, 'w') as f:
            json.dump(self.data, f)

    def record(self, league_id, year, week, team_id, score):
        key = f"{league_id}_{year}_{week}"
        samples = self.data.setdefault(key, {}).setdefault(str(team_id), [])
        now = time.time()
        if samples and now - samples[-1][0] < self.SAMPLE_INTERVAL_SECONDS:
            return
        samples.append([now, score])
        del samples[:-self.MAX_SAMPLES]
        self.save_data()

    def get(self, league_id, year, week, team_id):
        key = f"{league_id}_{year}_{week}"
        return [s for _, s in self.data.get(key, {}).get(str(team_id), [])]


score_history = ScoreHistory()

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values):
    """Tiny Unicode-block trend chart -- same character family as the
    existing block-bar progress meters, so it's already proven to render
    correctly in Discord's code-block font. Needs at least 2 points to show
    a shape; returns '' otherwise so callers can just omit it.

    An all-zero pregame score history (every sample taken before kickoff)
    would otherwise render as a row of the lowest bar character, which is
    visually indistinguishable from blank underscores -- reads as broken
    UI, not data. Suppress that case; a genuine flat *non-zero* trend still
    shows, using a mid-height bar instead of the lowest one so it reads as
    "flat" rather than "empty"."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return "" if hi == 0 else _SPARK_CHARS[3] * len(values)
    span = hi - lo
    return "".join(_SPARK_CHARS[min(7, int((v - lo) / span * 7))] for v in values)


# Initialize league manager
league_manager = LeagueManager()

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
LEAGUE_ID = int(os.getenv('ESPN_LEAGUE_ID'))
SEASON_ID = int(os.getenv('ESPN_SEASON_ID'))
SWID = os.getenv('ESPN_SWID')
ESPN_S2 = os.getenv('ESPN_S2')

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


def with_league(error_label):
    """Wraps a single-league slash command with the four steps every one
    of them repeated by hand: defer the interaction, fetch the caller's
    league off the event loop (asyncio.to_thread -- get_league does a
    blocking ESPN call on a cache miss), bail out with the standard "no
    league registered" message if there isn't one, and log+report any
    other exception the command body raises. ~15 commands had all four
    steps duplicated verbatim before this.

    The decorated function must take `league` as its second parameter,
    right after `interaction` (e.g. `async def compare(interaction,
    league, team1, team2)`) -- the decorator calls it directly with the
    fetched league. Discord never sees that parameter: __signature__ is
    rebuilt without it below, so app_commands' schema (and autocomplete/
    describe, which read that same signature) only ever reflects the
    real user-facing options, exactly as if `league` weren't there.

    Doesn't fit /compare_cross_league (resolves two independent leagues,
    not one) or the admin/debug commands (different message) -- those
    keep doing it by hand."""
    def decorator(func):
        visible_sig = inspect.signature(func).replace(
            parameters=[p for name, p in inspect.signature(func).parameters.items() if name != 'league'])

        @functools.wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            if not await safe_defer(interaction):
                return
            try:
                league = await asyncio.to_thread(get_league, user_id=interaction.user.id)
                if not league:
                    await safe_interaction_response(interaction, "❌ No league found. Use `/register_league` to add your ESPN Fantasy League first, or contact an admin if you want to use the default league.", ephemeral=True)
                    return
                await func(interaction, league, *args, **kwargs)
            except Exception as e:
                log_name = func.__name__.replace('_', ' ').capitalize()
                print(f"{log_name} command error: {e}")
                await safe_interaction_response(interaction, f"❌ Error {error_label}: {e}", ephemeral=True)

        wrapper.__signature__ = visible_sig
        return wrapper
    return decorator


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
        league = await asyncio.to_thread(get_league, user_id=interaction.user.id)
        if not league:
            await interaction.followup.send("❌ No league found for autocomplete testing", ephemeral=True)
            return

        team_count = len(league.teams)
        total_players = sum(len(team.roster) for team in league.teams)

        await interaction.followup.send(f"✅ Autocomplete data available:\n- {team_count} teams\n- {total_players} total players\n\nIf autocomplete still doesn't work, try restarting the bot.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Autocomplete debug failed: {str(e)[:100]}", ephemeral=True)

@client.tree.command(name="detailed_stats", description="Power rankings and league-wide scoring analytics.")
@with_league("generating power rankings")
async def detailed_stats(interaction: discord.Interaction, league):
    current_week = getattr(league, 'current_week', 1)
    box_scores = await _safe_box_scores(league, current_week)

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

    # Components V2 Container, not a classic discord.Embed, so this
    # budgets against the wider CODE_BLOCK_MAX_CHARS (65) instead of
    # EMBED_CODE_BLOCK_MAX_CHARS (56) -- see config/discord_display.py.
    FIXED = (3, 5, 5, 4)  # RK, REC, PPG, PWR
    name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *FIXED), max(len(r['name']) for r in rankings))
    header = _table_row([("RK", 3), ("TEAM", name_w), ("REC", 5), ("PPG", 5), ("PWR", 4)])[0]
    rows = []
    for r in rankings:
        rows.extend(_table_row([
            (str(r['rank']), 3),
            (r['name'], name_w, '<', True),
            (r['record'], 5),
            (f"{r['ppg']:.1f}", 5),
            (f"{r['power']:.2f}", 4),
        ]))
    table = _frame_table([header], rows)

    footer = f"-# Top Week {current_week} Proj: {top_proj['name']} \u00b7 {top_proj['week_proj']:.1f}  \u00b7  League Total {total_points:,.1f}  \u00b7  Avg {avg_ppg:.1f} PPG"
    body = f"**Power Rankings**  \u00b7  Top 5\n{table}\n{footer}"

    class DetailedStatsView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(body))
            self.add_item(container)

    await interaction.followup.send(view=DetailedStatsView())


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


_box_scores_cache = {}

async def _safe_box_scores(league, week):
    """league.box_scores() indexes ESPN's raw JSON with data[team]['rosterForCurrentScoringPeriod']
    (espn_api/football/box_score.py) instead of .get() -- early in a new season,
    before ESPN has populated a scoring-period roster for every team (e.g. before
    Week 1 lineups lock), that key is simply absent and the whole call raises a
    bare KeyError. Every caller here already has a real fallback for "no box
    score found" (season averages, a friendly not-found message, an empty
    scoreboard), so swallowing this one specific early-season failure into an
    empty list lets that existing degradation take over instead of crashing.

    Also cached -- box_scores() is a synchronous ESPN network call with no
    caching of its own, so every card render (and every /team nav-button
    click, which can fire several in quick succession) was re-fetching the
    same week's data fresh. A completed week's scores never change again, so
    those are cached indefinitely; the current/live week gets a short window
    (scores update mid-game) long enough to absorb a burst of clicks without
    going stale for more than half a minute.

    league.box_scores() itself calls espn_api's `requests`-based (not
    aiohttp) HTTP client -- a real, blocking network call with no async
    story of its own. Called directly, that freezes the ENTIRE bot's
    event loop for the whole round trip: not just this one interaction,
    but every other command, in every other server, and Discord's own
    gateway heartbeat, until ESPN responds. That's what actually made
    /team's nav feel slow on a cache miss (e.g. switching to a week
    nobody's viewed yet) -- not just this one click waiting, but the
    whole bot stalling. asyncio.to_thread runs it on a worker thread
    instead, so the event loop stays free to keep handling everything
    else while ESPN responds."""
    cache_key = f"box_scores_{league.league_id}_{league.year}_{week}"
    cached = _box_scores_cache.get(cache_key)
    if cached:
        data, timestamp, is_final = cached
        if is_final or time.time() - timestamp < 20:
            return data
    try:
        data = await asyncio.to_thread(league.box_scores, week=week)
    except KeyError:
        data = []
    is_final = week < getattr(league, 'current_week', 1)
    _box_scores_cache[cache_key] = (data, time.time(), is_final)
    for m in data:
        if m.home_team:
            score_history.record(league.league_id, league.year, week, m.home_team.team_id, float(m.home_score or 0))
        if m.away_team:
            score_history.record(league.league_id, league.year, week, m.away_team.team_id, float(m.away_score or 0))
    return data


async def _build_roster_rows(league, team, current_week):
    """Real per-week actual/projected points, slotting, and injury status for
    one team's full roster -- /team shows both halves in one card.
    Returns (starter_rows, bench_rows).

    team.roster's player.stats dict comes back empty once the season isn't
    "live" in ESPN's eyes (e.g. completed weeks) -- box_scores() is the
    reliable source for this week's actual/projected points AND gives a real
    pro_opponent field the plain roster objects don't have."""
    box_score = next(
        (m for m in await _safe_box_scores(league, current_week)
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

    def build_row(player):
        if box_score:
            actual = float(getattr(player, 'points', 0) or 0)
            proj = float(getattr(player, 'projected_points', 0) or 0)
        else:
            # No box score for this team/week -- plain roster Player objects
            # don't expose per-week points at all, so fall back to season
            # average rather than showing a fake 0.0 for every player.
            actual = 0.0
            proj = float(getattr(player, 'avg_points', 0) or 0)
        return {
            'slot': display_slot(player),
            'position': player.position,
            'name': player.name,
            'team_abbr': player.proTeam or '',
            'opp_abbr': getattr(player, 'pro_opponent', '') or '',
            'status': get_status(player),
            'actual': actual,
            'proj': proj,
            'live': None,  # live scoreboard data isn't wired up yet -- see project memory
        }

    return [build_row(p) for p in starters], [build_row(p) for p in bench]


async def _build_team_card_data(league, team, week):
    """Same card-building logic /team uses, pulled out so the TeamNavView
    buttons can re-render for a different team/week without duplicating it."""
    starter_rows, bench_rows = await _build_roster_rows(league, team, week)

    wins, losses = getattr(team, 'wins', 0), getattr(team, 'losses', 0)
    sorted_teams = sorted(league.teams, key=lambda t: (getattr(t, 'wins', 0), getattr(t, 'points_for', 0)), reverse=True)
    rank = next((i + 1 for i, t in enumerate(sorted_teams) if t.team_id == team.team_id), 0)

    logos = await get_logos_by_url([team.logo_url])
    logo_path = logos.get(team.logo_url)

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

    games_played = wins + losses + getattr(team, 'ties', 0)
    season_scores = list(getattr(team, 'scores', [])[:games_played])

    return {
        'team_name': team.team_name,
        'owner_name': owner_name,
        'record': f"{wins}-{losses}",
        'rank': rank,
        'total_teams': len(league.teams),
        'current_week': week,
        'proj_record': None,
        'proj_record_note': None,
        'live_games_count': 0,
        'starters': starter_rows,
        'bench': bench_rows,
        'logo_path': logo_path,
        'season_scores': season_scores,
        'starters_total_actual': sum(r['actual'] for r in starter_rows),
        'starters_total_proj': sum(r['proj'] for r in starter_rows),
    }


# Every bordered table in the bot shares this frame: a full ─/│ box with
# no corner glyphs (┌┐└┘├┤ don't render in Discord's code-block font -- a
# box built with real corner characters came out with missing edges, see
# _matchup_table's original discovery). The "corners" are just a dash line
# and a pipe column meeting at the same character position on adjacent
# rows, which reads as a solid frame anyway.
TABLE_BORDER_OVERHEAD = 4  # "│ " + " │" added to every line by _frame_table

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _visible_len(s):
    """Length ignoring ANSI color escapes -- an ```ansi block's colored
    rows (see standings' streak column) are longer in raw characters than
    they are on screen, so padding/width math must measure this instead
    of len() or colored rows drift out of alignment with plain ones."""
    return len(_ANSI_RE.sub('', s))


def _frame_table(*groups, lang=""):
    """Wrap groups of already-formatted, equal-purpose table lines (e.g.
    [header], [row, row, ...], [total]) in the shared bordered-table frame,
    with a plain dash rule inserted between each group. Every table-
    building function in the bot should size its own columns to leave
    room for TABLE_BORDER_OVERHEAD before calling this. Pass lang="ansi"
    for a table using ANSI color escapes (e.g. standings' streak column)."""
    all_lines = [l for g in groups for l in g]
    content_width = max(_visible_len(l) for l in all_lines)
    rule = "─" * content_width
    border = "─" * (content_width + 4)

    def frame(line):
        pad = " " * (content_width - _visible_len(line))
        return f"│ {line}{pad} │"

    body = []
    for i, g in enumerate(groups):
        if i > 0:
            body.append(frame(rule))
        body.extend(frame(l) for l in g)
    return f"```{lang}\n" + "\n".join([border, *body, border]) + "\n```"


# --- Standardized row layout -------------------------------------------
#
# Every table in the bot renders rows through _table_row instead of each
# command hand-rolling its own column-width arithmetic and wrap-to-a-
# second-line logic. That hand-rolling is what caused this session's real
# bugs, twice over:
#   1. scoreboard's border wrapped because a hand-counted FIXED_OVERHEAD
#      comment (`1 + 6 + 1 + SPARK_W + ...`) silently undercounted the
#      real format string by one character -- a comment and the code it
#      describes can drift apart with no error. _flex_width below sums
#      real column widths instead, so there's nothing to drift.
#   2. compare's header (and the first version of _wrap_name_row) glued
#      two columns together with zero gap ("Dak PrescottNYG") whenever a
#      column's content was longer than its own padding width -- `:<{w}}`
#      adds no space once the content already exceeds w. _table_row joins
#      every column with an explicit separator, inserted regardless of
#      overflow, so gluing is structurally impossible.
# Both bug classes are eliminated by construction here, not by being more
# careful next time.

def _table_row(cells, sep=" "):
    """One table row as 1+ physical lines. `cells` is a list of
    (text, width), (text, width, align), (text, width, align, flex), or
    (text, width, align, flex, repeat) tuples -- align defaults to '<',
    flex and repeat to False. Every column boundary is joined by `sep`,
    inserted explicitly between cells, never folded into a column's own
    padding width, so two columns can never glue together no matter
    which ones overflow.

    Each flex column that overflows its width gets its OWN dedicated
    physical line, with every other column blank on that line -- never
    ellipsized (per explicit "there shouldn't be any truncation"
    feedback), and never sharing a line with another column's content.
    This matters when TWO flex cells in the same row overflow at once
    (e.g. both teams' names in one scoreboard row): putting both raw
    names on one shared line made that line far wider than the table's
    normal rows, long enough to blow past Discord's own code-block
    width cap and get hard-wrapped mid-row by Discord itself -- which
    is what a broken-looking border in production actually was.

    Fixing just that isn't enough on its own, though: _frame_table sizes
    the WHOLE table's border to the single widest line among every row
    (`content_width = max(_visible_len(l) for l in all_lines)`), so even
    ONE overflow line that's too wide drags every border line in the
    whole table past Discord's cap and wraps the border itself, not
    just that row. Padding an overflow line out to the OTHER columns'
    normal widths (blank filler before/after the name, to keep it
    lined up under its column) was exactly what made these lines too
    wide -- a name of e.g. 22 chars sitting in a column budgeted for
    14 could still end up on a padded line topping 65 real characters
    even though the name itself was nowhere near that long. So an
    overflow line carries ONLY the overflowing cell's raw text plus any
    repeat=True cells (see below) -- no filler for the columns that
    have nothing to say on that line. That line's width is then bounded
    by the name's own length, not by how many empty columns happen to
    sit next to it, which is what actually keeps it under the cap for
    any realistic name.

    A short, never-wrapping cell (e.g. a "│" divider between two
    independent flex columns) needs to look continuous down every
    physical line or the border reads as broken exactly on the rows
    where a name overflowed. Pass repeat=True for that: it's rendered
    on every physical line, overflow lines included, instead of only
    the last one -- its own width is negligible so it doesn't reopen
    the wrapping problem above."""
    parsed = [(c[0], c[1], c[2] if len(c) > 2 else '<', c[3] if len(c) > 3 else False, c[4] if len(c) > 4 else False) for c in cells]
    overflow_idxs = [i for i, (t, w, a, f, r) in enumerate(parsed) if f and len(t) > w]

    def blank(w, a):
        return f"{'':{a}{w}}"

    def normal(t, w, a):
        return f"{t:{a}{w}}"

    lines = []
    for oi in overflow_idxs:
        lines.append(sep.join(
            t for i, (t, w, a, f, r) in enumerate(parsed) if i == oi or r
        ))
    lines.append(sep.join(
        normal(t, w, a) if r else (blank(w, a) if i in overflow_idxs else normal(t, w, a))
        for i, (t, w, a, f, r) in enumerate(parsed)
    ))
    return lines


def _flex_width(budget, *fixed_widths, sep=" ", flex_cols=1):
    """Characters available for the flex column(s) in a row, given the
    table's total character budget (CODE_BLOCK_MAX_CHARS or one of its
    embed-context siblings, config/discord_display.py) and the OTHER
    columns' actual widths -- summed here from real numbers, never
    hand-counted in a comment. Subtracts TABLE_BORDER_OVERHEAD and one
    `sep`-width gap for every column boundary automatically."""
    n_cols = len(fixed_widths) + flex_cols
    n_seps = max(n_cols - 1, 0)
    return budget - sum(fixed_widths) - n_seps * len(sep) - TABLE_BORDER_OVERHEAD


def _test_table_row():
    """Smoke test for the bug classes _table_row exists to prevent. Run
    at bot startup (see __main__) so a regression fails loudly instead
    of silently shipping."""
    long_name = "Swift Nation (Travis version)"
    # a repeat=True divider must appear on EVERY physical line, even the
    # one where a flex column overflowed -- the default (blank until the
    # last line) is right for row data, but a "│" divider that vanishes
    # on exactly the rows where a name overflowed reads as a broken
    # border (this was a real bug: matchup's divider disappeared on any
    # row where either team's player name was too long).
    divider_lines = _table_row([("Short", 6, '<', True), ("│", 1, '<', False, True), (long_name, 6, '<', True)])
    assert len(divider_lines) == 2, "one side overflows, so this row should be 2 physical lines"
    assert all("│" in l for l in divider_lines), f"divider must repeat on every line, got {divider_lines!r}"
    # never truncates
    lines = _table_row([(long_name, 6, '<', True), ("22.1", 6, '>')])
    joined = "".join(lines)
    assert long_name in joined, "full name must appear somewhere"
    assert "…" not in joined, "must never ellipsize"
    # never glues: the overflow line holds nothing but the name and
    # trailing blank padding from the other (non-overflowing) column --
    # never another column's real content butted up against it
    overflow_line = next(l for l in lines if long_name in l)
    assert overflow_line.rstrip() == long_name, f"overflow line should be just the name, got {overflow_line!r}"
    # short content still gets a real gap, not fused padding
    assert _table_row([("AB", 6, '<', True), ("1.0", 4, '>')]) == ["AB      1.0"]
    # _flex_width sums real widths, not a hand-counted constant
    assert _flex_width(65, 5, 6, 6, sep=" ", flex_cols=1) == 65 - 5 - 6 - 6 - 3 - TABLE_BORDER_OVERHEAD
    # two flex cells overflowing in the SAME row (e.g. both scoreboard
    # teams have long names) must each get their own line, not share one
    # -- sharing produced a single line long enough to blow past
    # Discord's own code-block width cap and get hard-wrapped mid-row,
    # which is what a broken scoreboard border in production actually
    # was (looked fine locally since nothing here enforces the real
    # per-line char budget, only that the two names never land together)
    other_long = "Tyler's Mediocre team"
    dual_lines = _table_row([(long_name, 6, '<', True), ("│", 1, '<', False, True), (other_long, 6, '<', True)])
    assert len(dual_lines) == 3, f"two independent overflows should be 3 physical lines, got {dual_lines!r}"
    assert not any(long_name in l and other_long in l for l in dual_lines), \
        "two overflowing names must never share a physical line"
    assert all("│" in l for l in dual_lines), f"divider must repeat on every line, got {dual_lines!r}"


def _roster_table(rows):
    """SLOT/PLAYER/OPP/PTS/PROJ/ST table shared by the starters and bench
    sections of /team -- same row shape either way. Name column width is
    measured from CODE_BLOCK_MAX_CHARS, not a guessed cap, same fix
    _matchup_table got: the truncation length should come from real
    display-width measurements (config/discord_display.py), so a name
    only overflows onto its own line when it's a genuine outlier, not a
    completely ordinary NFL name.

    /team used to be a classic discord.Embed with BOTH a thumbnail (team
    logo) and a field (bench), which only gets a 44-char budget
    (EMBED_THUMBNAIL_CODE_BLOCK_MAX_CHARS) -- too tight for a 6-column
    table to leave real player names any room; "Tetairoa McMillan" and
    "Michael Pittman Jr." (17-19 chars, nothing exotic) were wrapping
    onto their own line on nearly every real roster, not just genuine
    outliers. /team is a Components V2 Container now (same fix /trade
    got earlier this session), so this budgets against the wider
    CODE_BLOCK_MAX_CHARS (65) instead.

    Column widths sized to their REAL content, not round numbers: SLOT
    tops out at "FLEX"/"D/ST" (4), OPP at 3-letter team codes, PTS/PROJ
    at "999.9" (5), ST at 2-letter status codes.
    """
    if not rows:
        return "_Empty_"
    FIXED = (4, 3, 5, 5, 2)  # SLOT, OPP, PTS, PROJ, ST
    name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *FIXED), max(len(r['name']) for r in rows))
    header = _table_row([("SLOT", 4), ("PLAYER", name_w), ("OPP", 3), ("PTS", 5), ("PROJ", 5), ("ST", 2)])[0]
    lines = []
    for r in rows:
        lines.extend(_table_row([
            (r['slot'], 4),
            (r['name'], name_w, '<', True),
            (r['opp_abbr'], 3),
            (f"{r['actual']:.1f}", 5),
            (f"{r['proj']:.1f}", 5),
            (r['status'] or '', 2),
        ]))
    return _frame_table([header], lines)


def _build_team_container(data, accent=EMBED_COLOR_BRAND):
    """Builds /team's Components V2 Container -- shared by the initial
    /team command and every TeamNavView re-render (Prev/Next team/week).
    Replaces the old classic discord.Embed + set_thumbnail + add_field
    version: a Container gets CODE_BLOCK_MAX_CHARS (65) instead of the
    embed-with-thumbnail-and-field context's 44, which is what actually
    keeps ordinary player names from wrapping (see _roster_table)."""
    starters_table = _roster_table(data['starters'])
    bench = data['bench']

    season_spark = _sparkline(data['season_scores'])
    week_line = f"-# Week {data['current_week']} Roster"
    if season_spark:
        week_line += f"   ·   Season {season_spark}"

    body = (
        f"**{data['team_name']}**  ·  {data['owner_name']}  ·  {data['record']}  ·  Rank {data['rank']} of {data['total_teams']}\n"
        f"{week_line}\n"
        f"{starters_table}"
    )
    footer = f"-# Starters Total: {data['starters_total_actual']:.1f} (proj {data['starters_total_proj']:.1f})"

    container = discord.ui.Container(accent_colour=accent)
    logo_file = None
    if data['logo_path']:
        logo_file = discord.File(data['logo_path'], filename=os.path.basename(data['logo_path']))
        section = discord.ui.Section(discord.ui.TextDisplay(body), accessory=discord.ui.Thumbnail(f"attachment://{logo_file.filename}"))
        container.add_item(section)
    else:
        container.add_item(discord.ui.TextDisplay(body))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"**Bench ({len(bench)})**\n{_roster_table(bench)}"))
    container.add_item(discord.ui.TextDisplay(footer))
    return container, logo_file


async def _prefetch_all_team_logos(league):
    """Warm the local logo cache for every team in the league, fired in
    the background from /team's initial call (asyncio.create_task, not
    awaited) -- so clicking TeamNavView's arrows to a team you haven't
    viewed yet doesn't pay for a fresh image download+upload on that
    click, which is what made nav feel slow. Best-effort: any failure
    here just means that team's logo gets fetched on demand later
    instead, same as before this existed."""
    try:
        await get_logos_by_url([t.logo_url for t in league.teams if getattr(t, 'logo_url', None)])
    except Exception:
        pass


class TeamNavView(discord.ui.LayoutView):
    """Prev/Next arrows for team (ordered best-to-worst by record, mirroring
    the visible RANK X OF Y already on the card) and week (capped at the
    league's current week -- future weeks aren't reliably populated yet).

    Just 4 plain arrow buttons, one row each for team/week -- no middle
    "Rank 2 / 12" / "Wk 1 / 1" label buttons. Those used to exist, but
    they were pure duplication (the card's own body text already says
    "Rank 2 of 12") and made the control twice as bulky as it needed to
    be. Boundary arrows disable themselves rather than wrapping around.

    A Components V2 LayoutView doesn't support the classic
    @discord.ui.button decorator pattern (that's a plain-View-only
    feature) -- every button here is built manually and wired to a
    callback in __init__, same as every other LayoutView in this bot
    (MatchupView, WaiverView, etc.). Clicking Team/Week builds a whole
    new TeamNavView (fresh container, fresh buttons) rather than mutating
    this one in place, since the container's content has to be rebuilt
    from the new team/week's data anyway."""

    def __init__(self, user_id, league, team_order, team_idx, week, container):
        super().__init__(timeout=1800)
        self.user_id = user_id
        self.league = league
        self.team_order = team_order
        self.team_idx = team_idx
        self.week = week
        self.max_week = getattr(league, 'current_week', 1)

        async def go(interaction: discord.Interaction, d_team=0, d_week=0):
            await interaction.response.defer()
            new_idx, new_week = self.team_idx + d_team, self.week + d_week
            team = next(t for t in self.league.teams if t.team_name == self.team_order[new_idx])
            card_data = await _build_team_card_data(self.league, team, new_week)
            new_container, logo_file = _build_team_container(card_data)
            new_view = TeamNavView(self.user_id, self.league, self.team_order, new_idx, new_week, new_container)
            attachments = [logo_file] if logo_file else []
            await interaction.edit_original_response(attachments=attachments, view=new_view)

        async def on_prev_team(i: discord.Interaction):
            await go(i, d_team=-1)

        async def on_next_team(i: discord.Interaction):
            await go(i, d_team=1)

        async def on_prev_week(i: discord.Interaction):
            await go(i, d_week=-1)

        async def on_next_week(i: discord.Interaction):
            await go(i, d_week=1)

        row1, row2 = discord.ui.ActionRow(), discord.ui.ActionRow()
        prev_team_btn = discord.ui.Button(label="◀ Team", style=discord.ButtonStyle.secondary, disabled=team_idx == 0)
        next_team_btn = discord.ui.Button(label="Team ▶", style=discord.ButtonStyle.secondary, disabled=team_idx == len(team_order) - 1)
        prev_week_btn = discord.ui.Button(label="◀ Week", style=discord.ButtonStyle.secondary, disabled=week <= 1)
        next_week_btn = discord.ui.Button(label="Week ▶", style=discord.ButtonStyle.secondary, disabled=week >= self.max_week)
        prev_team_btn.callback, next_team_btn.callback = on_prev_team, on_next_team
        prev_week_btn.callback, next_week_btn.callback = on_prev_week, on_next_week
        row1.add_item(prev_team_btn)
        row1.add_item(next_team_btn)
        row2.add_item(prev_week_btn)
        row2.add_item(next_week_btn)

        container.add_item(discord.ui.Separator())
        container.add_item(row1)
        container.add_item(row2)
        self.add_item(container)


@client.tree.command(name="team", description="Generate a visual roster card for a team.")
@app_commands.describe(team_name="The exact name of the team as it appears in ESPN.")
@app_commands.autocomplete(team_name=team_name_autocomplete)
@with_league("creating team card")
async def team(interaction: discord.Interaction, league, team_name: str):
    asyncio.create_task(_prefetch_all_team_logos(league))

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

    if not team.roster:
        await safe_interaction_response(interaction, f"❌ {team.team_name} has no roster yet -- this league hasn't drafted for the {league.year} season.", ephemeral=True)
        return

    current_week = getattr(league, 'current_week', 1)
    card_data = await _build_team_card_data(league, team, current_week)
    container, logo_file = _build_team_container(card_data)

    ranked_teams = sorted(league.teams, key=lambda t: (getattr(t, 'wins', 0), getattr(t, 'points_for', 0)), reverse=True)
    team_order = [t.team_name for t in ranked_teams]
    view = TeamNavView(interaction.user.id, league, team_order, team_order.index(team.team_name), current_week, container)

    if logo_file:
        await interaction.followup.send(view=view, file=logo_file)
    else:
        await interaction.followup.send(view=view)


# ============================================================================
# Shared helpers for the native Discord embeds/Components V2 commands below
# (discord.py 2.6 -- LayoutView/Container/TextDisplay/Section/ActionRow are
# all real message components, not an image, so buttons on them genuinely
# work).
# ============================================================================

def _record_str(t):
    r = f"{t.wins}-{t.losses}"
    return r + (f"-{t.ties}" if getattr(t, 'ties', 0) else '')


async def _scoreboard_container(league, week, user_id):
    box_scores = await _safe_box_scores(league, week)
    container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
    container.add_item(discord.ui.TextDisplay(f"🔴 **LIVE**  ·  Week {week} Scoreboard  ·  updated {datetime.now().strftime('%I:%M %p')}"))
    container.add_item(discord.ui.Separator())

    games = []
    for m in box_scores:
        if not m.home_team or not m.away_team:
            continue
        games.append({
            'name1': m.home_team.team_name, 'score1': float(m.home_score),
            'name2': m.away_team.team_name, 'score2': float(m.away_score),
        })

    if games:
        # Stacked one-team-per-line layout, not side-by-side: cramming
        # two team names PLUS both scores onto one line left so little
        # room per name that any real outlier (a long joke team name)
        # either had to truncate -- ruled out -- or get special-cased
        # overflow handling that kept producing a visually broken-looking
        # table no matter how it was tuned (names glued to dividers,
        # rows losing their blank-line spacing, two names splitting
        # across lines with no visual grouping). One name per line gives
        # each name the whole row's budget instead of splitting it two
        # ways, so a real name essentially never needs special handling
        # at all -- simpler and structurally safer, not just prettier.
        name_w = _flex_width(CODE_BLOCK_MAX_CHARS, 6, flex_cols=1)  # 6 = score column
        groups = []
        for g in games:
            groups.append(
                _table_row([(g['name1'], name_w, '<', True), (f"{g['score1']:.1f}", 6, '>')]) +
                _table_row([(g['name2'], name_w, '<', True), (f"{g['score2']:.1f}", 6, '>')])
            )
        container.add_item(discord.ui.TextDisplay(_frame_table(*groups)))
    else:
        container.add_item(discord.ui.TextDisplay("_No matchups found for this week (bye week or off-season)._"))

    row = discord.ui.ActionRow()
    refresh_btn = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
    row.add_item(refresh_btn)
    container.add_item(row)
    return container, refresh_btn


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
            {'value': f"{g('receivingYardsAfterCatch') / games_played:.1f}", 'label': 'YAC/G'},
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
@with_league("creating player card")
async def player(interaction: discord.Interaction, league, player_name: str):
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

    status_map = {'QUESTIONABLE': 'Questionable', 'OUT': 'Out', 'DOUBTFUL': 'Doubtful', 'INJURY_RESERVE': 'Injury Reserve'}
    status = status_map.get(getattr(found_player, 'injuryStatus', None), 'Active')

    current_week = getattr(league, 'current_week', 1)
    highlight_label = highlight_text = None
    try:
        box_score = next((m for m in await _safe_box_scores(league, current_week)
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
    stats = _player_position_stats(found_player)
    stat_line1 = "  ·  ".join(f"{s['label']} **{s['value']}**" for s in stats[:3])
    stat_line2 = "  ·  ".join(f"{s['label']} **{s['value']}**" for s in stats[3:])

    body = (
        f"### {found_player.name}\n"
        f"{found_player.position} · {found_player.proTeam or 'FA'} · {status}\n\n"
        f"**PPG:** {found_player.avg_points:.1f}   **Total:** {found_player.total_points:.1f}   **Games:** {games_played}\n"
    )
    if highlight_text:
        body += f"\n**{highlight_label}:** {highlight_text}\n"
    if stat_line1:
        body += f"\n{stat_line1}"
    if stat_line2:
        body += f"\n{stat_line2}"
    body += f"\n\n-# {player_team.team_name}"

    thumb_file = discord.File(headshot_path, filename=os.path.basename(headshot_path)) if headshot_path else None

    class PlayerView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            if thumb_file:
                section = discord.ui.Section(discord.ui.TextDisplay(body), accessory=discord.ui.Thumbnail(f"attachment://{thumb_file.filename}"))
                container.add_item(section)
            else:
                container.add_item(discord.ui.TextDisplay(body))
            self.add_item(container)

    if thumb_file:
        await interaction.followup.send(view=PlayerView(), file=thumb_file)
    else:
        await interaction.followup.send(view=PlayerView())

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
    box_scores = await _safe_box_scores(league, current_week)

    def week_proj(team_obj):
        bs = next(
            (m for m in box_scores
             if (m.home_team and m.home_team.team_id == team_obj.team_id) or
                (m.away_team and m.away_team.team_id == team_obj.team_id)),
            None
        )
        if not bs:
            return sum(float(getattr(p, 'avg_points', 0) or 0) for p in team_obj.roster if getattr(p, 'lineupSlot', None) != 'BE')
        lineup = bs.home_lineup if bs.home_team.team_id == team_obj.team_id else bs.away_lineup
        return sum(float(getattr(p, 'projected_points', 0) or 0) for p in lineup if getattr(p, 'slot_position', None) != 'BE')

    proj1 = week_proj(team1_obj)
    proj2 = week_proj(team2_obj)

    games1 = team1_obj.wins + team1_obj.losses + getattr(team1_obj, 'ties', 0)
    games2 = team2_obj.wins + team2_obj.losses + getattr(team2_obj, 'ties', 0)
    ppg1 = team1_obj.points_for / games1 if games1 else 0
    ppg2 = team2_obj.points_for / games2 if games2 else 0

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

    def row(abbrev, v1, v2, lower_is_better=False):
        win1 = (v1 < v2) if lower_is_better else (v1 > v2)
        win2 = (v2 < v1) if lower_is_better else (v2 > v1)
        return {'abbrev': abbrev, 'left_val': f"{v1:.1f}", 'right_val': f"{v2:.1f}",
                'left_win': win1 if v1 != v2 else None, 'right_win': win2 if v1 != v2 else None}

    return {
        'team1': {'name': team1_obj.team_name, 'owner': _team_owner_name(team1_obj), 'record': _team_record_str(team1_obj),
                  'league': league1_name},
        'team2': {'name': team2_obj.team_name, 'owner': _team_owner_name(team2_obj), 'record': _team_record_str(team2_obj),
                  'league': league2_name},
        'series_note': series_note, 'series_note_warn': warn,
        'current_week': current_week,
        'rows': [
            row('PF', team1_obj.points_for, team2_obj.points_for),
            row('PA', team1_obj.points_against, team2_obj.points_against, lower_is_better=True),
            row('PPG', ppg1, ppg2),
            row('PROJ', proj1, proj2),
        ],
    }


def _compare_view(data):
    """Aligned monospace table shared by /compare and /compare_cross_league
    -- one ROW per team (PF/PA/PPG/PROJ as columns), not one row per stat
    with both team names crammed into a shared header.

    Earlier versions of this table put both team names side by side in
    one header row (either as the header itself, or split across a "|"
    divider) -- but two names sharing one row's available width means an
    outlier name (e.g. "Swift Nation (Travis version)") ALWAYS forces
    itself onto its own bare line no matter how that width is split or
    anchored; there's no divider placement that fixes a name simply not
    fitting. Putting each team on its own row instead -- exactly
    /standings' proven pattern, where TEAM is a flex column sized to real
    content -- means a long name only ever has to fit next to four short
    numbers, never next to another team's name, so it fits in full
    without needing a second physical line at all."""
    t1, t2 = data['team1'], data['team2']
    name1 = t1['name'] + (f" ({t1['league']})" if t1.get('league') else "")
    name2 = t2['name'] + (f" ({t2['league']})" if t2.get('league') else "")

    # One fixed width per stat column, wide enough for its own header
    # abbreviation and both teams' values -- summed by _flex_width from
    # real widths (config/discord_display.py) rather than a hand-counted
    # constant. This is a Components V2 Container, not a classic
    # discord.Embed, so it budgets against the wider CODE_BLOCK_MAX_CHARS
    # (65), not EMBED_CODE_BLOCK_MAX_CHARS (56).
    stat_widths = [max(len(r['abbrev']), len(r['left_val']), len(r['right_val'])) for r in data['rows']]
    name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *stat_widths), max(len(name1), len(name2), 6))

    header_lines = _table_row([("", name_w, '<', True)] + [(r['abbrev'], w, '>') for r, w in zip(data['rows'], stat_widths)])
    row1 = _table_row([(name1, name_w, '<', True)] + [(r['left_val'], w, '>') for r, w in zip(data['rows'], stat_widths)])
    row2 = _table_row([(name2, name_w, '<', True)] + [(r['right_val'], w, '>') for r, w in zip(data['rows'], stat_widths)])
    table = _frame_table(header_lines, [*row1, *row2])

    header_line = f"**{name1}**  `{t1['record']}`  vs  **{name2}**  `{t2['record']}`  ·  Week {data['current_week']}"
    body = (f"-# {data['series_note']}\n" if data['series_note'] else "") + f"{header_line}\n{table}"

    class CompareView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(body))
            self.add_item(container)

    return CompareView()


@client.tree.command(name="compare", description="Visual season-long comparison of two teams.")
@app_commands.describe(team1="First team name", team2="Second team name")
@app_commands.autocomplete(team1=team_name_autocomplete, team2=team_name_autocomplete)
@with_league("comparing teams")
async def compare(interaction: discord.Interaction, league, team1: str, team2: str):
    team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
    team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
    if not team1_obj:
        await interaction.followup.send(f"Team '{team1}' not found.")
        return
    if not team2_obj:
        await interaction.followup.send(f"Team '{team2}' not found.")
        return

    card_data = await _build_compare_card(league, team1_obj, team2_obj, interaction.user.id)
    await interaction.followup.send(view=_compare_view(card_data))


@client.tree.command(name="standings", description="Show league standings with records and points.")
@with_league("creating standings")
async def standings(interaction: discord.Interaction, league):
    current_week = getattr(league, 'current_week', 1)
    settings = league.settings
    reg_season_count = getattr(settings, 'reg_season_count', 0)
    playoff_team_count = getattr(settings, 'playoff_team_count', 0)

    teams_data = []
    for team in league.teams:
        wins, losses, ties = team.wins, team.losses, getattr(team, 'ties', 0)
        games = wins + losses + ties
        win_pct = (wins + ties * 0.5) / games if games else 0.0
        teams_data.append({'team': team, 'wins': wins, 'losses': losses, 'ties': ties, 'pf': team.points_for, 'win_pct': win_pct})
    teams_data.sort(key=lambda t: (t['win_pct'], t['pf']), reverse=True)

    # Name column width comes from the real measured code-block budget
    # (config/discord_display.py), not a guessed-then-tuned cap. This is a
    # Components V2 Container, so it budgets against the wider
    # CODE_BLOCK_MAX_CHARS (65), not the classic-Embed-only
    # EMBED_CODE_BLOCK_MAX_CHARS (56). Names longer than even that budget
    # don't truncate -- they get their own line via _table_row instead
    # (e.g. "Swift Nation (Travis version)"), per explicit feedback that
    # truncation isn't acceptable even for outliers.
    FIXED = (3, 5, 7, 4)  # RK, W-L, PF, STRK
    name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *FIXED), max(len(t['team'].team_name) for t in teams_data))
    header = _table_row([("RK", 3), ("TEAM", name_w), ("W-L", 5), ("PF", 7), ("STRK", 4)])[0]
    rows = []
    for i, t in enumerate(teams_data):
        team = t['team']
        record = f"{t['wins']}-{t['losses']}" + (f"-{t['ties']}" if t['ties'] else '')
        streak_type = getattr(team, 'streak_type', None)
        streak_len = getattr(team, 'streak_length', 0)
        streak = f"{'W' if streak_type == 'WIN' else 'L'}{streak_len}" if streak_type else "-"
        rows.extend(_table_row([
            (str(i + 1), 3),
            (team.team_name, name_w, '<', True),
            (record, 5),
            (f"{t['pf']:.1f}", 7),
            (streak, 4),
        ]))
    # No color-coding the streak (used to be an ansi fence with red/
    # green escape codes) -- combining a ```ansi fence with the full
    # border broke badly in Discord's renderer: the raw text was
    # correct, but highlight.js's ansi grammar ate the border pipes
    # and inflated line spacing when mixed with color spans. A plain
    # fence with the shared border is consistent with every other
    # table in the bot and actually renders correctly.
    table = _frame_table([header], rows)

    if reg_season_count and current_week > reg_season_count:
        week_label = f"Final · {reg_season_count} Games Played"
    else:
        week_label = f"Week {current_week} · Regular Season"

    footer_text = None
    if 0 < playoff_team_count < len(teams_data):
        leader, chaser = teams_data[playoff_team_count - 1], teams_data[playoff_team_count]
        gb = ((leader['wins'] - chaser['wins']) + (chaser['losses'] - leader['losses'])) / 2
        footer_text = f"Playoff cutoff: {playoff_team_count} teams · {chaser['team'].team_name} is {gb:.1f} games back"

    body = f"**{week_label}**\n{table}"
    if footer_text:
        body += f"\n-# {footer_text}"

    class StandingsView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(body))
            self.add_item(container)

    await interaction.followup.send(view=StandingsView())






async def _build_playoff_bracket_data(league):
    """Builds the championship path (byes -> semifinals -> championship,
    losers dropped) by tracking a shrinking 'alive' set through each real
    playoff week's box scores, rather than assuming any particular
    seeding/bye formula ourselves -- this naturally handles byes and any
    playoff_team_count because it only ever looks at actual recorded
    matchups (who a still-alive team actually played, and who actually
    won), never a theoretical bracket shape we'd have to guess at.

    Each round is a list of SLOTs (a 'game' of two teams, or a 'bye' of
    one), and every slot beyond round 1 carries 'sources': the index/indices
    of the previous round's slot(s) that produced its participant(s) --
    derived purely from real team-id continuity across rounds, never a
    seeding formula. That linkage is what lets the renderer draw actual
    bracket connector lines instead of just stacking rounds with no visible
    relationship between them.

    Returns (rounds, champion), or (None, None) if the regular season isn't
    over yet (there's no seeding to bracket until it is)."""
    settings = league.settings
    reg_season_count = getattr(settings, 'reg_season_count', None)
    playoff_team_count = getattr(settings, 'playoff_team_count', None)
    current_week = getattr(league, 'current_week', 1)
    if not reg_season_count or not playoff_team_count:
        return None, None
    if current_week <= reg_season_count:
        return None, None

    def win_pct(t):
        games = t.wins + t.losses + getattr(t, 'ties', 0)
        return (t.wins + getattr(t, 'ties', 0) * 0.5) / games if games else 0.0

    ranked = sorted(league.teams, key=lambda t: (win_pct(t), t.points_for), reverse=True)
    playoff_teams = ranked[:playoff_team_count]
    seed_of = {t.team_id: i + 1 for i, t in enumerate(playoff_teams)}

    def slot_team(t):
        return {'seed': seed_of[t.team_id], 'name': t.team_name}

    matchup_periods = getattr(settings, 'matchup_periods', None)
    last_week = max((int(k) for k in matchup_periods.keys()), default=reg_season_count) if matchup_periods else reg_season_count

    alive = {t.team_id: t for t in playoff_teams}
    slot_index_of = {t.team_id: i for i, t in enumerate(playoff_teams)}  # round-1 order = seed order
    rounds = []
    champion = None

    for week in range(reg_season_count + 1, last_week + 1):
        week_box_scores = await _safe_box_scores(league, week)
        games_by_team = {}
        for m in week_box_scores:
            if m.home_team and m.away_team and m.home_team.team_id in alive and m.away_team.team_id in alive:
                games_by_team[m.home_team.team_id] = m
                games_by_team[m.away_team.team_id] = m

        if not games_by_team:
            break  # this round hasn't been scheduled/paired by ESPN yet

        bye_ids = {tid for tid in alive if tid not in games_by_team}
        prev_order = sorted(alive.keys(), key=lambda tid: slot_index_of[tid])

        round_slots = []
        new_slot_index_of = {}
        advancing = {}
        placed = set()

        for tid in prev_order:
            if tid in placed:
                continue
            if tid in bye_ids:
                t = alive[tid]
                round_slots.append({'type': 'bye', 'team': slot_team(t), 'sources': [slot_index_of[tid]]})
                new_slot_index_of[tid] = len(round_slots) - 1
                advancing[tid] = t
                placed.add(tid)
                continue

            m = games_by_team[tid]
            home_id, away_id = m.home_team.team_id, m.away_team.team_id
            placed.add(home_id)
            placed.add(away_id)

            gp_values = [getattr(p, 'game_played', 0) for p in m.home_lineup]
            if gp_values and all(v == 100 for v in gp_values):
                status = 'final'
            elif any(v > 0 for v in gp_values):
                status = 'live'
            else:
                status = 'tbd'

            score1, score2 = float(m.home_score), float(m.away_score)
            win1 = win2 = None
            if status == 'final':
                win1, win2 = score1 > score2, score2 > score1
                winner_id = home_id if win1 else away_id
                advancing[winner_id] = alive[winner_id]

            round_slots.append({
                'type': 'game', 'status': status,
                'team1': {**slot_team(m.home_team), 'score': score1, 'win': win1},
                'team2': {**slot_team(m.away_team), 'score': score2, 'win': win2},
                'sources': [slot_index_of[home_id], slot_index_of[away_id]],
            })
            new_slot_index_of[home_id] = len(round_slots) - 1
            new_slot_index_of[away_id] = len(round_slots) - 1

        rounds.append(round_slots)

        if not all(s['type'] == 'bye' or s['status'] == 'final' for s in round_slots):
            break  # this round is still being played -- don't guess at future pairings

        alive = advancing
        slot_index_of = new_slot_index_of
        if len(alive) == 1:
            champion = slot_team(next(iter(alive.values())))
            break

    # Reorder every round (working backward) so a slot's sources always sit
    # ADJACENT to each other in the previous round. Rounds were built in
    # seed order, which doesn't guarantee that -- e.g. seed 1's bye and
    # seed 2's bye end up on opposite ends of round 1, while the actual
    # semifinal pairing crosses the middle (seed 1 joins the 4-vs-5 winner,
    # seed 2 joins the 3-vs-6 winner). Averaging two DISTANT sources for one
    # semifinal and two MIDDLE sources for the other can land on the exact
    # same Y position (they're symmetric around the same center), so one
    # semifinal box silently rendered on top of the other. Reordering so
    # true siblings are always next to each other eliminates that by
    # construction, which is what any correct bracket layout requires.
    for ri in range(len(rounds) - 1, 0, -1):
        new_order = [src for slot in rounds[ri] for src in slot['sources']]
        remap = {old_idx: new_idx for new_idx, old_idx in enumerate(new_order)}
        for slot in rounds[ri]:
            slot['sources'] = [remap[s] for s in slot['sources']]
        rounds[ri - 1] = [rounds[ri - 1][old_idx] for old_idx in new_order]

    return rounds, champion


@client.tree.command(name="playoffs", description="Visual playoff bracket -- championship path only.")
@with_league("creating playoff bracket")
async def playoffs(interaction: discord.Interaction, league):
    reg_season_count = getattr(league.settings, 'reg_season_count', None)
    current_week = getattr(league, 'current_week', 1)
    if not reg_season_count or current_week <= reg_season_count:
        await safe_interaction_response(interaction, f"❌ Playoffs haven't started yet -- check back after Week {reg_season_count or '?'}. Use `/standings` for the current regular-season race.", ephemeral=True)
        return

    rounds, champion = await _build_playoff_bracket_data(league)
    if not rounds:
        await safe_interaction_response(interaction, "❌ The playoff bracket hasn't been seeded yet -- check back once Week 1 of the playoffs has been scheduled.", ephemeral=True)
        return

    def round_label(i, total):
        remaining = total - i
        if remaining == 1:
            return "Championship"
        if remaining == 2:
            return "Semifinals"
        if remaining == 3:
            return "Quarterfinals"
        return f"Round {i + 1}"

    lines = []
    for i, round_slots in enumerate(rounds):
        lines.append(f"**{round_label(i, len(rounds))}**")
        for slot in round_slots:
            if slot['type'] == 'bye':
                t = slot['team']
                lines.append(f"  ({t['seed']}) {t['name']} — BYE")
            else:
                t1, t2 = slot['team1'], slot['team2']
                if slot['status'] == 'final':
                    s1 = f"**{t1['score']:.1f}**" if t1['win'] else f"{t1['score']:.1f}"
                    s2 = f"**{t2['score']:.1f}**" if t2['win'] else f"{t2['score']:.1f}"
                else:
                    s1 = s2 = "-"
                lines.append(f"  ({t1['seed']}) {t1['name']} {s1}  vs  {s2} ({t2['seed']}) {t2['name']}")
        lines.append("")
    bracket_text = "\n".join(lines).strip()
    if champion:
        bracket_text += f"\n\n\U0001f3c6 **Champion:** {champion['name']}"

    embed = discord.Embed(color=EMBED_COLOR_BRAND, description=f"**Playoff Bracket**  ·  {league.year}\n\n{bracket_text}")
    await interaction.followup.send(embed=embed)


@client.tree.command(name="stats", description="League superlatives -- consistency, luck, schedule strength.")
@with_league("fetching stats")
async def stats(interaction: discord.Interaction, league):
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
        ('Most Consistent', most_consistent['name'], f"\u03c3 {most_consistent['std_dev']:.1f}"),
        ('Most Volatile', most_volatile['name'], f"\u03c3 {most_volatile['std_dev']:.1f}"),
        ('Best Single Week', best_week[1], f"{best_week[0]:.1f} pts"),
        ('Worst Single Week', worst_week[1], f"{worst_week[0]:.1f} pts"),
        ('Most Efficient', most_efficient['name'], f"{most_efficient['efficiency']:.3f} win/1000pf"),
    ]
    if unluckiest:
        tiles.append(('Unluckiest', unluckiest['name'], f"{unluckiest['points_for']:.1f} PF \u00b7 {record_of(unluckiest['name'])}"))
    tiles.append(('Toughest Schedule', toughest['name'], f"{toughest['points_against']:.1f} PA"))
    tiles.append(('Easiest Schedule', easiest['name'], f"{easiest['points_against']:.1f} PA"))

    embed = discord.Embed(color=EMBED_COLOR_BRAND, description="-# League Stats \u00b7 Full Season")
    for label, team, value in tiles:
        embed.add_field(name=label, value=f"**{team}**\n{value}", inline=True)
    await interaction.followup.send(embed=embed)


@client.tree.command(name="sleeper", description="Find undervalued sleeper picks with high upside potential.")
@app_commands.describe(position="Filter by position (QB, RB, WR, TE, K, D/ST) - leave empty for all positions")
@with_league("finding sleepers")
async def sleeper(interaction: discord.Interaction, league, position: str = None):
    if not any(t.roster for t in league.teams):
        await safe_interaction_response(interaction, f"\u274c This league hasn't drafted yet for the {league.year} season -- before a draft, every NFL player shows up as a \"free agent,\" so sleeper picks aren't meaningful yet.", ephemeral=True)
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
    top5 = candidates[:5]

    if not top5:
        await interaction.followup.send("No sleeper candidates found with current criteria.")
        return

    top_pick = top5[0]
    player = top_pick['player']
    images = await get_images(player_ids=[player.playerId], team_abbrs=[])
    headshot = images['players'].get(player.playerId)
    thumb_file = discord.File(headshot, filename=os.path.basename(headshot)) if headshot else None

    body = (
        f"### #1 \u00b7 {player.name}\n"
        f"{player.position} \u00b7 {player.proTeam or 'FA'} \u00b7 {top_pick['owned']:.1f}% owned\n\n"
        f"**Proj. Points:** {top_pick['proj']:.1f}\n"
        f"**Season Avg:** {top_pick['avg']:.1f}"
    )

    rest = top5[1:]
    rest_table = None
    if rest:
        FIXED = (3, 4, 5, 5, 5)  # #, POS, OWN%, PROJ, AVG
        name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *FIXED), max(len(c['player'].name) for c in rest))
        header = _table_row([("#", 3), ("PLAYER", name_w), ("POS", 4), ("OWN%", 5), ("PROJ", 5), ("AVG", 5)])[0]
        rows = []
        for i, c in enumerate(rest, start=2):
            p = c['player']
            rows.extend(_table_row([
                (str(i), 3),
                (p.name, name_w, '<', True),
                (p.position, 4),
                (f"{c['owned']:.1f}", 5),
                (f"{c['proj']:.1f}", 5),
                (f"{c['avg']:.1f}", 5),
            ]))
        rest_table = _frame_table([header], rows)

    pos_label = f"{position.upper()} Only" if position else "All Positions"

    class SleeperView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(f"-# \U0001f4a4 TOP SLEEPER PICK \u00b7 {pos_label}"))
            if thumb_file:
                section = discord.ui.Section(discord.ui.TextDisplay(body), accessory=discord.ui.Thumbnail(f"attachment://{thumb_file.filename}"))
                container.add_item(section)
            else:
                container.add_item(discord.ui.TextDisplay(body))
            if rest_table:
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay(rest_table))
            self.add_item(container)

    if thumb_file:
        await interaction.followup.send(view=SleeperView(), file=thumb_file)
    else:
        await interaction.followup.send(view=SleeperView())

async def _matchup_lineups(league, team1_obj, team2, current_week):
    """Shared lookup for /matchup: finds this week's box score for team1
    (or the team1-vs-team2 box score if team2 is given), and returns the
    two lineups home/away-normalized to (team1, team2) order."""
    box_scores = await _safe_box_scores(league, current_week)
    team2_obj = None
    if team2:
        team2_obj = next((t for t in league.teams if t.team_name.lower() == team2.lower()), None)
        if not team2_obj:
            return None, None, None, None
        box_score = next((m for m in box_scores if {getattr(m.home_team, 'team_id', None), getattr(m.away_team, 'team_id', None)} == {team1_obj.team_id, team2_obj.team_id}), None)
    else:
        box_score = next((m for m in box_scores if (m.home_team and m.home_team.team_id == team1_obj.team_id) or (m.away_team and m.away_team.team_id == team1_obj.team_id)), None)
        if box_score:
            team2_obj = box_score.away_team if box_score.home_team.team_id == team1_obj.team_id else box_score.home_team
    if not box_score or not team2_obj:
        return None, None, None, None
    team1_is_home = box_score.home_team.team_id == team1_obj.team_id
    lineup1 = box_score.home_lineup if team1_is_home else box_score.away_lineup
    lineup2 = box_score.away_lineup if team1_is_home else box_score.home_lineup
    return box_score, team2_obj, lineup1, lineup2


def _matchup_dslot(p):
    slot = getattr(p, 'slot_position', '') or ''
    if slot in ('D/ST', 'DST'):
        return 'D/ST'
    return 'FLEX' if '/' in slot else (slot or p.position)


_MATCHUP_SLOT_ORDER = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'FLEX': 4, 'D/ST': 5, 'K': 6}


def _matchup_display_name(p):
    """Display name for the interleaved matchup table -- the real full
    name, just with the redundant ' D/ST' suffix stripped since the POS
    column already says D/ST. No forced initial-abbreviation: the table's
    column widths are sized (see _matchup_table) to fit real names as
    given, and only ellipsize the specific names that are genuine outliers
    for that matchup, instead of chopping every name down to save space
    nobody needed."""
    if _matchup_dslot(p) == 'D/ST':
        name = p.name
        for suffix in (' D/ST', ' DST'):
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return name
    return p.name


def _matchup_table(starters1, starters2, pregame):
    """One aligned table, one row per starting slot, both teams side by
    side, framed via the shared _frame_table border. The vertical divider
    in the middle reads as a versus line between the two teams.

    Each side's name column is sized from its own real names (full names,
    not force-abbreviated), splitting whatever's left of
    CODE_BLOCK_MAX_CHARS after the fixed slot/points/divider columns --
    measured from an actual rendered code block (config/discord_display.py),
    not a guessed-then-tuned-until-it-stopped-wrapping cap like the
    earlier version of this table used. If both sides' names already fit
    inside that budget, nothing gets truncated at all."""
    def value(p):
        return float(getattr(p, 'projected_points', 0) or 0) if pregame else float(getattr(p, 'points', 0) or 0)

    names1 = [_matchup_display_name(p) for p in starters1]
    names2 = [_matchup_display_name(p) for p in starters2]

    # Fixed columns: "SLOT " (5+1) + name + " " + "PTS.P" (6) + " │ " (3) + name + " " + "PTS.P" (6)
    FIXED_OVERHEAD = 23
    name_budget = CODE_BLOCK_MAX_CHARS - FIXED_OVERHEAD - TABLE_BORDER_OVERHEAD
    # Sized from the PLAYER names only, not name1/name2 (the team names) --
    # team names are never rendered in this table's own columns anymore
    # (see header_lines below), so budgeting room for them here would
    # just make the table wider than the data it actually shows needs.
    natural1 = max(len(n) for n in names1)
    natural2 = max(len(n) for n in names2)
    if natural1 + natural2 <= name_budget:
        w1, w2 = natural1, natural2
    else:
        w1 = max(6, round(name_budget * natural1 / (natural1 + natural2)))
        w2 = max(6, name_budget - w1)

    def row_lines(slot_label, n1, v1_str, n2, v2_str, lead1=' ', lead2=' '):
        """One matchup row (header/data/total all go through this) as
        1+ physical lines via the shared _table_row primitive: two
        independent flex name columns (either can overflow to its own
        line without affecting the other) plus a literal "│" divider
        cell -- sep=" " on both sides of it reproduces the original
        " │ " gap exactly, and because _table_row always joins columns
        through an explicit separator (never fused into a padding
        width), the divider can never end up glued to either name."""
        return _table_row([
            (slot_label, 5),
            (f"{lead1}{n1}", w1 + 1, '<', True),
            (v1_str, 6, '>'),
            ("│", 1, '<', False, True),
            (f"{lead2}{n2}", w2 + 1, '<', True),
            (v2_str, 6, '>'),
        ])

    # The header row leaves the name columns blank rather than repeating
    # name1/name2 -- both full team names are already shown in the
    # message's own header line right above this table (see the
    # /matchup command), so putting them here too was pure duplication.
    # It was also the actual source of a broken-looking table: a TEAM
    # name is often longer than the PLAYER-name budget these columns are
    # sized for (real player rows below rarely overflow; team names
    # almost always did), so this was the row that broke most often --
    # for data that was already fully visible one line up.
    header_lines = row_lines("POS", "", "PTS", "", "PTS")

    rows = []
    total1 = total2 = 0.0
    for p1, p2, n1, n2 in zip(starters1, starters2, names1, names2):
        slot = _matchup_dslot(p1)
        v1, v2 = value(p1), value(p2)
        total1 += v1
        total2 += v2
        lead1 = '>' if (not pregame and v1 > v2) else ' '
        lead2 = '>' if (not pregame and v2 > v1) else ' '
        rows.extend(row_lines(slot, n1, f"{v1:.1f}", n2, f"{v2:.1f}", lead1, lead2))
    total_line = row_lines("", "TOTAL", f"{total1:.1f}", "TOTAL", f"{total2:.1f}")

    return _frame_table(header_lines, rows, total_line)


@client.tree.command(name="matchup", description="Head-to-head visual matchup card for this week.")
@app_commands.describe(team1="First team name", team2="Second team name (optional - will try to find current matchup)")
@app_commands.autocomplete(team1=team_name_autocomplete, team2=team_name_autocomplete)
@with_league("creating matchup")
async def matchup(interaction: discord.Interaction, league, team1: str, team2: str = None):
    team1_obj = next((t for t in league.teams if t.team_name.lower() == team1.lower()), None)
    if not team1_obj:
        await interaction.followup.send(f"Team '{team1}' not found.")
        return

    if not team1_obj.roster:
        await safe_interaction_response(interaction, f"❌ {team1_obj.team_name} has no roster yet -- this league hasn't drafted for the {league.year} season.", ephemeral=True)
        return

    current_week = getattr(league, 'current_week', 1)
    box_score, team2_obj, lineup1, lineup2 = await _matchup_lineups(league, team1_obj, team2, current_week)
    if not box_score:
        await interaction.followup.send(f"Could not find a matchup for {team1_obj.team_name} in week {current_week}. Specify both teams: `/matchup {team1} TeamName`")
        return

    team1_is_home = box_score.home_team.team_id == team1_obj.team_id
    score1 = float(box_score.home_score if team1_is_home else box_score.away_score)
    score2 = float(box_score.away_score if team1_is_home else box_score.home_score)
    proj1 = sum(float(getattr(p, 'projected_points', 0) or 0) for p in lineup1 if _matchup_dslot(p) != 'BE')
    proj2 = sum(float(getattr(p, 'projected_points', 0) or 0) for p in lineup2 if _matchup_dslot(p) != 'BE')

    starters1 = sorted([p for p in lineup1 if _matchup_dslot(p) != 'BE'], key=lambda p: _MATCHUP_SLOT_ORDER.get(_matchup_dslot(p), 99))
    starters2 = sorted([p for p in lineup2 if _matchup_dslot(p) != 'BE'], key=lambda p: _MATCHUP_SLOT_ORDER.get(_matchup_dslot(p), 99))
    bench1 = [p for p in lineup1 if _matchup_dslot(p) == 'BE']
    bench2 = [p for p in lineup2 if _matchup_dslot(p) == 'BE']

    pregame = score1 == 0 and score2 == 0
    if pregame:
        headline = f"proj {proj1:.1f} — {proj2:.1f}"
        accent = EMBED_COLOR_BRAND
    else:
        headline = f"🔴 LIVE · {score1:.1f} — {score2:.1f} · proj {proj1:.1f}/{proj2:.1f}"
        diff = abs(score1 - score2)
        accent = 0x2ECC71 if diff <= 10 else (0x99A1A6 if diff >= 30 else EMBED_COLOR_BRAND)

    # Momentum sparklines -- built from score_history's opportunistic
    # samples (see _safe_box_scores), so they only show up once enough
    # samples have accumulated over the week; empty until then.
    spark1 = _sparkline(score_history.get(league.league_id, league.year, current_week, team1_obj.team_id))
    spark2 = _sparkline(score_history.get(league.league_id, league.year, current_week, team2_obj.team_id))
    spark_parts = []
    if spark1:
        spark_parts.append(f"{team1_obj.team_name} {spark1}")
    if spark2:
        spark_parts.append(f"{team2_obj.team_name} {spark2}")
    spark_line = f"-# {'  ·  '.join(spark_parts)}\n" if spark_parts else ""

    table = _matchup_table(starters1, starters2, pregame)
    header = (
        f"-# Week {current_week} · {team1_obj.team_name} ({_record_str(team1_obj)}) vs {team2_obj.team_name} ({_record_str(team2_obj)})\n"
        f"### {headline}\n"
        f"{spark_line}"
        f"{table}"
    )

    class MatchupView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=accent)
            container.add_item(discord.ui.TextDisplay(header))

            row = discord.ui.ActionRow()
            bench_btn = discord.ui.Button(label="Bench", style=discord.ButtonStyle.secondary)
            lineup_btn = discord.ui.Button(label="Set Lineup", style=discord.ButtonStyle.secondary)
            chat_btn = discord.ui.Button(label="Chat", style=discord.ButtonStyle.secondary)

            async def on_bench(i: discord.Interaction):
                if not bench1 and not bench2:
                    await i.response.send_message("Both benches are empty.", ephemeral=True)
                    return
                bench_table = _matchup_table(bench1, bench2, pregame)

                class BenchView(discord.ui.LayoutView):
                    def __init__(self):
                        super().__init__(timeout=1800)
                        bench_container = discord.ui.Container(accent_colour=accent)
                        bench_container.add_item(discord.ui.TextDisplay(bench_table))
                        self.add_item(bench_container)

                await i.response.send_message(view=BenchView(), ephemeral=True)

            async def on_lineup(i: discord.Interaction):
                await i.response.send_message(
                    "⚠️ This bot has read-only ESPN access -- lineup changes have to be made in the ESPN app.",
                    ephemeral=True)

            async def on_chat(i: discord.Interaction):
                try:
                    thread = await i.message.create_thread(name=f"{team1_obj.team_name} vs {team2_obj.team_name} - Week {current_week}")
                    await i.response.send_message(f"Opened {thread.mention}", ephemeral=True)
                except discord.HTTPException as e:
                    await i.response.send_message(f"Couldn't open a thread here: {e}", ephemeral=True)

            bench_btn.callback = on_bench
            lineup_btn.callback = on_lineup
            chat_btn.callback = on_chat
            row.add_item(bench_btn)
            row.add_item(lineup_btn)
            row.add_item(chat_btn)
            container.add_item(row)
            self.add_item(container)

    await interaction.followup.send(view=MatchupView())


@client.tree.command(name="waiver", description="Analyze waiver wire for top pickup recommendations.")
@app_commands.describe(
    position="Filter by position (QB, RB, WR, TE, K, D/ST)",
    min_owned="Minimum ownership percentage (0-100, default: 0)",
    max_owned="Maximum ownership percentage (0-100, default: 50)"
)
@with_league("analyzing waiver wire")
async def waiver(interaction: discord.Interaction, league, position: str = None, min_owned: int = 0, max_owned: int = 50):
    if not any(t.roster for t in league.teams):
        await safe_interaction_response(interaction, f"\u274c This league hasn't drafted yet for the {league.year} season -- before a draft, every NFL player shows up as a \"free agent,\" so there's no real waiver wire yet.", ephemeral=True)
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
    top5 = top10[:5]

    gem = next((c for c in top10 if c['owned'] <= 10 and c['proj'] >= 80), None)
    popular_ids = {c['player'].playerId for c in top10 if c['owned'] >= 25}

    def tag_for(c):
        if gem and c['player'].playerId == gem['player'].playerId:
            return 'GEM'
        return 'POP' if c['player'].playerId in popular_ids else ''

    pos_counts = {}
    for c in top10:
        pos_counts.setdefault(c['player'].position, []).append(c)
    deepest = max(pos_counts, key=lambda p: len(pos_counts[p])) if pos_counts else None
    scarcest = min(pos_counts, key=lambda p: len(pos_counts[p])) if pos_counts else None
    depth_note = f"{deepest} deep \u00b7 {scarcest} scarce" if deepest and scarcest and deepest != scarcest else None

    top_pick = top5[0]
    player = top_pick['player']
    images = await get_images(player_ids=[player.playerId], team_abbrs=[])
    headshot = images['players'].get(player.playerId)
    thumb_file = discord.File(headshot, filename=os.path.basename(headshot)) if headshot else None

    pick_tag = tag_for(top_pick)
    pick_tag_line = "\U0001f48e Hidden Gem\n" if pick_tag == 'GEM' else ("\U0001f525 Popular Pick\n" if pick_tag == 'POP' else "")
    body = (
        f"### #1 \u00b7 {player.name}\n"
        f"{player.position} \u00b7 {player.proTeam or 'FA'} \u00b7 Free Agent\n"
        f"{pick_tag_line}\n"
        f"**Rostered:** {top_pick['owned']:.1f}%\n"
        f"**Proj. Points:** {top_pick['proj']:.1f}"
    )

    rest = top5[1:]
    rest_table = None
    if rest:
        FIXED = (3, 4, 5, 5, 3)  # #, POS, OWN%, PROJ, TAG
        name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *FIXED), max(len(c['player'].name) for c in rest))
        header = _table_row([("#", 3), ("PLAYER", name_w), ("POS", 4), ("OWN%", 5), ("PROJ", 5), ("TAG", 3)])[0]
        rows = []
        for i, c in enumerate(rest, start=2):
            p = c['player']
            rows.extend(_table_row([
                (str(i), 3),
                (p.name, name_w, '<', True),
                (p.position, 4),
                (f"{c['owned']:.1f}", 5),
                (f"{c['proj']:.1f}", 5),
                (tag_for(c), 3),
            ]))
        rest_table = _frame_table([header], rows)

    class WaiverView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(f"-# \U0001f4c8 TOP WAIVER TARGET \u00b7 {min_owned}-{max_owned}% Owned"))
            if thumb_file:
                section = discord.ui.Section(discord.ui.TextDisplay(body), accessory=discord.ui.Thumbnail(f"attachment://{thumb_file.filename}"))
                container.add_item(section)
            else:
                container.add_item(discord.ui.TextDisplay(body))
            if rest_table:
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay(rest_table))
            if depth_note:
                container.add_item(discord.ui.TextDisplay(f"-# {depth_note}"))
            container.add_item(discord.ui.Separator())

            row = discord.ui.ActionRow()
            order_btn = discord.ui.Button(label="View Waiver Order", style=discord.ButtonStyle.secondary)

            async def on_order(i: discord.Interaction):
                order = sorted(league.teams, key=lambda t: getattr(t, 'waiver_rank', 999))
                if not any(getattr(t, 'waiver_rank', None) for t in order):
                    await i.response.send_message("\u26a0\ufe0f This league/ESPN response doesn't expose waiver priority order via espn_api.", ephemeral=True)
                    return
                lines = [f"{getattr(t, 'waiver_rank', '?')}. {t.team_name}" for t in order]
                await i.response.send_message("**Waiver Order**\n" + "\n".join(lines), ephemeral=True)

            order_btn.callback = on_order
            row.add_item(order_btn)
            container.add_item(row)
            self.add_item(container)

    if thumb_file:
        await interaction.followup.send(view=WaiverView(), file=thumb_file)
    else:
        await interaction.followup.send(view=WaiverView())


@client.tree.command(name="trade", description="Visual trade analysis between two teams.")
@app_commands.describe(
    team1="First team name",
    team2="Second team name",
    team1_players="Players team1 gives up (comma-separated)",
    team2_players="Players team2 gives up (comma-separated)"
)
@app_commands.autocomplete(team1=team_name_autocomplete, team2=team_name_autocomplete)
@with_league("analyzing trade")
async def trade(interaction: discord.Interaction, league, team1: str, team2: str, team1_players: str, team2_players: str):
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

    def trade_table(rows1, rows2):
        """One combined table, both sides side by side with a '|'
        divider -- same shape as /matchup's card, not two separate
        inline embed fields. Inline fields render in a box far
        narrower than a full-width embed description (discovered
        live this session: EMBED_CODE_BLOCK_MAX_CHARS was measured
        from a full-width description and doesn't hold for a 50%-
        width inline field, so the old two-inline-field layout wrapped
        its border on real trades). A single full-width table sidesteps
        that render context entirely -- and, since this card is now a
        Components V2 Container instead of a classic Embed (same
        reason /matchup moved off Embed), it gets CODE_BLOCK_MAX_CHARS'
        wider 65-char budget instead of the Embed's 56, which is what
        actually keeps two real player names from overflowing on
        nearly every row the way they did at 56."""
        n = max(len(rows1), len(rows2))
        blank = {'name': '', 'position': '', 'avg': None}
        rows1 = rows1 + [blank] * (n - len(rows1))
        rows2 = rows2 + [blank] * (n - len(rows2))

        natural1 = max((len(r['name']) for r in rows1), default=0)
        natural2 = max((len(r['name']) for r in rows2), default=0)
        FIXED = (4, 5, 1, 4, 5)  # POS, AVG, divider, POS, AVG
        name_budget = _flex_width(CODE_BLOCK_MAX_CHARS, *FIXED, flex_cols=2)
        if natural1 + natural2 <= name_budget:
            w1, w2 = max(natural1, 1), max(natural2, 1)
        else:
            w1 = max(6, round(name_budget * natural1 / (natural1 + natural2)))
            w2 = max(6, name_budget - w1)

        def row_lines(n1, pos1, avg1, n2, pos2, avg2):
            return _table_row([
                (n1, w1, '<', True), (pos1, 4), (avg1, 5, '>'),
                ("\u2502", 1, '<', False, True),
                (n2, w2, '<', True), (pos2, 4), (avg2, 5, '>'),
            ])

        header = row_lines("", "POS", "AVG", "", "POS", "AVG")
        body = []
        for r1, r2 in zip(rows1, rows2):
            avg1 = f"{r1['avg']:.1f}" if r1['avg'] is not None else ""
            avg2 = f"{r2['avg']:.1f}" if r2['avg'] is not None else ""
            body.extend(row_lines(r1['name'], r1['position'], avg1, r2['name'], r2['position'], avg2))
        return _frame_table(header, body)

    header_line = f"**{team1_obj.team_name} Sends**  vs  **{team2_obj.team_name} Sends**"
    table = trade_table(send_rows, receive_rows)
    body = (
        f"-# {fairness_label} \u00b7 {trade_type}\n"
        f"{header_line}\n"
        f"{table}\n"
        f"**Avg PPG Traded:** {send_total:.1f}  vs  {receive_total:.1f}"
    )
    if injury_notes:
        body += "\n-# " + " \u00b7 ".join(injury_notes)

    class TradeView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(body))
            self.add_item(container)

    await interaction.followup.send(view=TradeView())

@client.tree.command(name="scoreboard", description="Live scoreboard for all of this week's matchups.")
@with_league("creating scoreboard")
async def scoreboard(interaction: discord.Interaction, league):
    current_week = getattr(league, 'current_week', 1)

    # _scoreboard_container is async (the ESPN box-score fetch it does
    # runs on a worker thread now, see _safe_box_scores), and a
    # LayoutView's __init__ can't be async -- so it's built ahead of
    # time, here and again in on_refresh, and just handed to the view.
    class ScoreboardView(discord.ui.LayoutView):
        def __init__(self, container, refresh_btn):
            super().__init__(timeout=1800)  # 30 minutes -- a scoreboard from an hour ago isn't useful to refresh

            async def on_refresh(i: discord.Interaction):
                await i.response.defer()
                new_container, new_btn = await _scoreboard_container(league, current_week, interaction.user.id)
                await i.edit_original_response(view=ScoreboardView(new_container, new_btn))

            refresh_btn.callback = on_refresh
            self.add_item(container)

    container, refresh_btn = await _scoreboard_container(league, current_week, interaction.user.id)
    await interaction.followup.send(view=ScoreboardView(container, refresh_btn))

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
            league1_obj = await asyncio.to_thread(get_league, user_id=interaction.user.id)
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
        #
        # league1_name/league2_name are what trigger the "Different Leagues"
        # note and the "(League Name)" suffix on each team's display name in
        # _compare_view -- only pass them when the leagues are genuinely
        # different. Both optional args default to the caller's own league,
        # so calling this with neither `league1` nor `league2` given always
        # resolved to the SAME League object, but this used to pass the
        # names unconditionally anyway, so /compare_cross_league without any
        # league args (the common case) falsely announced "Different
        # Leagues" for a same-league comparison and lost the real
        # head-to-head series note /compare shows instead.
        same_league = getattr(league1_obj, 'league_id', None) == getattr(league2_obj, 'league_id', None)
        card_data = await _build_compare_card(
            league1_obj, team1_obj, team2_obj, interaction.user.id,
            league1_name=None if same_league else league1_name,
            league2_name=None if same_league else league2_name,
        )
        await interaction.followup.send(view=_compare_view(card_data))
    except Exception as e:
        print(f"Compare cross-league command error: {e}")
        await safe_interaction_response(interaction, f"\u274c Error comparing teams: {e}", ephemeral=True)


@client.tree.command(name="league_info", description="Display detailed league settings and configuration.")
@with_league("getting league info")
async def league_info(interaction: discord.Interaction, league):
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

    embed = discord.Embed(color=EMBED_COLOR_BRAND, description=f"-# {len(league.teams)} Teams \u00b7 {league.year} Season \u00b7 Week {current_week}")
    for row in info_rows:
        embed.add_field(name=row['label'], value=row['value'], inline=True)
    if roster_composition:
        embed.add_field(name="Roster Composition", value=", ".join(roster_composition), inline=False)
    await interaction.followup.send(embed=embed)

@client.tree.command(name="insights", description="League pulse -- who's hot, who's cold, by season PPG.")
@with_league("generating insights")
async def insights(interaction: discord.Interaction, league):
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

    # Components V2 Container, so this budgets against the wider
    # CODE_BLOCK_MAX_CHARS (65), not the classic-Embed-only
    # EMBED_CODE_BLOCK_MAX_CHARS (56).
    FIXED = (5, 5, 1)  # PPG, DIFF, emoji
    name_w = min(_flex_width(CODE_BLOCK_MAX_CHARS, *FIXED), max(len(t['name']) for t in hot + cold))

    def insight_lines(t, emoji):
        return _table_row([(t['name'], name_w, '<', True), (f"{t['ppg']:.1f}", 5), (f"{t['diff']:+.1f}", 5), (emoji, 1)])

    header = _table_row([("TEAM", name_w), ("PPG", 5), ("DIFF", 5)])[0]
    hot_rows = [line for t in hot for line in insight_lines(t, "\ud83d\udd25")]
    cold_rows = [line for t in cold for line in insight_lines(t, "\ud83e\uddca")]
    table = _frame_table([header], hot_rows, cold_rows)

    body = (
        f"-# Week {current_week} \u00b7 League Avg {league_avg_ppg:.1f} ppg\n{table}\n"
        f"-# Season Points Leader: {season_leader['name']} \u00b7 {season_leader['total_points']:.1f}"
    )

    class InsightsView(discord.ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=1800)
            container = discord.ui.Container(accent_colour=EMBED_COLOR_BRAND)
            container.add_item(discord.ui.TextDisplay(body))
            self.add_item(container)

    await interaction.followup.send(view=InsightsView())


def _reference_embed(data):
    """Same {'title','subtitle','sections','footer'} contract the old Pillow
    reference card used -- each section becomes an embed field, side-by-side
    column pairs stay inline just like the card's layout did."""
    embed = discord.Embed(color=EMBED_COLOR_BRAND, description=f"-# {data['subtitle']}")
    for section in data['sections']:
        row = section if isinstance(section, list) else [section]
        for sec in row:
            value = "\n".join(f"**{label}** {desc}" for label, desc in sec['items'])
            embed.add_field(name=sec['title'], value=value, inline=len(row) > 1)
    if data.get('footer'):
        embed.set_footer(text=data['footer'])
    return embed


@client.tree.command(name="welcome", description="Get started guide for using the Fantasy Football bot.")
async def welcome(interaction: discord.Interaction):
    """Comprehensive welcome and setup guide"""
    card_data = {
        'title': "Welcome to Fantasy Football Bot",
        'subtitle': "Your complete guide to dominating fantasy football with data-driven insights",
        'sections': [
            {'title': "Quick Start (New Users)", 'items': [
                ("1.", "Run /register_league with your ESPN League ID"),
                ("2.", "Try /scoreboard to see live scores"),
                ("3.", "Check out /league_info for your league details"),
                ("4.", "Run /help any time for the full command reference"),
            ]},
            [
                {'title': "How to Find Your ESPN League ID", 'items': [
                    ("1.", "Go to your ESPN Fantasy Football league"),
                    ("2.", "URL: fantasy.espn.com/football/league?leagueId=XXXXXX"),
                    ("3.", "Copy the numbers after leagueId="),
                    ("4.", "That's your League ID!"),
                ]},
                {'title': "Private Leagues (Need SWID & ESPN_S2 Cookies)", 'items': [
                    ("•", "Log into ESPN in your browser"),
                    ("•", "Open Developer Tools (F12)"),
                    ("•", "Go to Application → Cookies"),
                    ("•", "Find SWID and espn_s2 values"),
                    ("•", "Use them in /register_league"),
                ]},
            ],
            [
                {'title': "Most Popular Commands", 'items': [
                    ("/scoreboard", "Live weekly scores"),
                    ("/standings", "Regular season standings"),
                    ("/playoffs", "Playoff bracket"),
                    ("/team [name]", "Team roster & stats"),
                    ("/player [name]", "Player details"),
                ]},
                {'title': "Advanced Features", 'items': [
                    ("/compare [team1] [team2]", "Team comparison"),
                    ("/stats", "League analytics"),
                    ("/trade", "Trade analyzer"),
                    ("/sleeper", "Sleeper pick finder"),
                    ("/waiver", "Waiver wire analysis"),
                    ("/matchup", "Weekly matchup preview"),
                ]},
            ],
            [
                {'title': "Multiple Leagues", 'items': [
                    ("•", "Register multiple leagues with /register_league"),
                    ("•", "View all your leagues: /my_leagues"),
                    ("•", "Switch active league: /switch_league"),
                    ("•", "Remove leagues: /remove_league"),
                    ("•", "Check current status: /league_status"),
                ]},
                {'title': "Need Help?", 'items': [
                    ("•", "Run /help for quick command reference"),
                    ("•", "All commands work with your registered league automatically"),
                ]},
            ],
        ],
        'footer': "Pro tip: Pin this message for easy reference! Run /help any time for the full command list.",
    }
    await interaction.response.send_message(embed=_reference_embed(card_data))

@client.tree.command(name="help", description="Quick command reference and help.")
async def help_command(interaction: discord.Interaction):
    """Quick help and command reference"""
    card_data = {
        'title': "Fantasy Football Bot Help",
        'subtitle': "Quick command reference · Use /welcome for the full setup guide",
        'sections': [
            {'title': "Getting Started", 'items': [
                ("New users:", "Run /welcome for complete setup guide"),
                ("Register league:", "/register_league [league_id] [name]"),
                ("Finding League ID?", "Check /welcome"),
            ]},
            [
                {'title': "Core Commands", 'items': [
                    ("/scoreboard", "Live scores & matchups"),
                    ("/standings", "Regular season standings"),
                    ("/playoffs", "Playoff bracket"),
                    ("/team [name]", "Team roster"),
                    ("/player [name]", "Player stats"),
                    ("/league_info", "League settings"),
                ]},
                {'title': "Analysis Tools", 'items': [
                    ("/compare [team1] [team2]", "Compare teams"),
                    ("/stats", "League analytics"),
                    ("/matchup", "Weekly preview"),
                    ("/trade", "Trade analyzer"),
                    ("/waiver", "Waiver recommendations"),
                    ("/sleeper", "Sleeper picks"),
                ]},
            ],
            {'title': "League Management", 'items': [
                ("/my_leagues", "Your registered leagues"),
                ("/switch_league [name]", "Change active league"),
                ("/league_status", "Current settings"),
                ("/all_leagues", "Available leagues"),
            ]},
        ],
        'footer': "Use /welcome for detailed setup instructions and finding your ESPN League ID",
    }
    await interaction.response.send_message(embed=_reference_embed(card_data), ephemeral=True)

if __name__ == '__main__':
    import time
    import traceback

    _test_table_row()

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
