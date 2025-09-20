# 🏈 ESPN Fantasy Football Discord Bot

A comprehensive Discord bot that integrates with ESPN Fantasy Football API to provide real-time league data, analytics, and interactive features for your fantasy football server.

> **Note:** This is a personal project shared for educational purposes. Feel free to use and modify for your own needs, but please note that active maintenance and support are not guaranteed.

## ✨ Features

- **Multi-League Support** - Register and manage multiple ESPN Fantasy leagues
- **Real-Time Data** - Live scoring, standings, and player statistics
- **Advanced Analytics** - In-depth team comparisons, trade analysis, and waiver wire recommendations
- **Interactive Commands** - Visual team cards, scoreboards, and matchup analysis
- **Cross-League Comparisons** - Compare teams from different leagues
- **Private League Support** - Full access to private ESPN leagues with authentication

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- Discord Developer Account
- ESPN Fantasy Football League (public or private)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/ryanmachancock/ff-discord-bot.git
   cd ff-discord-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create environment file**
   ```bash
   cp .env.example .env
   ```

4. **Configure your bot** (see [Configuration](#-configuration) section)

5. **Run the bot**
   ```bash
   python bot.py
   ```

## ⚙️ Configuration

### Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and bot
3. Copy the bot token
4. Invite the bot to your server with appropriate permissions

### Environment Variables

Edit your `.env` file with the following configuration:

```env
# Discord Bot Configuration
DISCORD_TOKEN=your_discord_bot_token_here

# ESPN Fantasy Football League Configuration
ESPN_LEAGUE_ID=your_league_id_here
ESPN_SEASON_ID=2025

# ESPN Authentication (Required for private leagues only)
ESPN_SWID=your_swid_cookie_value_here
ESPN_S2=your_espn_s2_cookie_value_here
```

### Finding ESPN Credentials

For **private leagues**, you'll need to get your ESPN authentication cookies:

1. Login to [ESPN Fantasy Football](https://fantasy.espn.com)
2. Open browser developer tools (F12)
3. Go to Application/Storage → Cookies → fantasy.espn.com
4. Find and copy the values for:
   - `SWID` (including the curly braces)
   - `espn_s2` (long URL-encoded string)

**Note:** Public leagues don't require ESPN authentication credentials.

## 📋 Commands Reference

### 🎯 Core Team Commands

| Command | Description | Parameters |
|---------|-------------|------------|
| `/team` | Get detailed roster for a team | `team_name` |
| `/compare` | Compare two teams side-by-side | `team1`, `team2` |
| `/player` | Get detailed stats for a specific player | `player_name` |
| `/card` | Generate visual team card with stats | `team_name` |

### 📊 League Information

| Command | Description | Parameters |
|---------|-------------|------------|
| `/standings` | View league standings with records | None |
| `/stats` | Show detailed league analytics | None |
| `/scoreboard` | Live updating scoreboard for current week | `auto_refresh` (optional) |
| `/league_info` | Display league settings and configuration | None |

### 🔍 Analysis & Strategy

| Command | Description | Parameters |
|---------|-------------|------------|
| `/matchup` | Player-by-player matchup analysis | `team1`, `team2` (optional) |
| `/trade` | Analyze potential trades between teams | `team1`, `team2`, `give_players`, `get_players` |
| `/waiver` | Top waiver wire pickup recommendations | `position`, `min_owned`, `max_owned` |
| `/sleeper` | Find undervalued sleeper picks | `position` (optional) |

### 🏆 Multi-League Management

| Command | Description | Parameters |
|---------|-------------|------------|
| `/register_league` | Register a new ESPN league | `league_id`, `league_name`, `year`, `swid`, `espn_s2` |
| `/my_leagues` | View your registered leagues | None |
| `/switch_league` | Switch your default league | `league_name` |
| `/remove_league` | Remove a league from your account | `league_name` |
| `/all_leagues` | View all available server leagues | None |
| `/compare_cross_league` | Compare teams from different leagues | `team1`, `league1`, `team2`, `league2` |

### 🛠️ Utility Commands

| Command | Description | Parameters |
|---------|-------------|------------|
| `/menu` | Interactive command menu | None |
| `/help` | Quick command reference | None |
| `/welcome` | Complete setup and usage guide | None |
| `/league_status` | Show current default league and status | None |
| `/ping` | Check if bot is responsive | None |

## 📸 Command Examples

### `/team` - Team Roster Display
<img width="515" height="686" alt="Team roster example" src="https://github.com/user-attachments/assets/d084ace1-344b-4131-a261-4cd3ea05fb42" />

### `/compare` - Team Comparison
<img width="534" height="336" alt="Team comparison example" src="https://github.com/user-attachments/assets/7d4f1b51-5837-48d4-a836-f66304dfea44" />

### `/standings` - League Standings
<img width="570" height="479" alt="League standings example" src="https://github.com/user-attachments/assets/316d23b8-227b-450a-8c21-274dfe5f91bf" />

### `/stats` - League Analytics
<img width="521" height="667" alt="League stats example" src="https://github.com/user-attachments/assets/92e6cb7e-ed93-4a7e-aa63-1a119d96b001" />

## 🔧 Advanced Usage

### Multi-League Setup

The bot supports managing multiple ESPN leagues simultaneously:

1. Use `/register_league` to add each of your leagues
2. Use `/switch_league` to change your default league
3. Use `/compare_cross_league` to compare teams across different leagues
4. Use `/all_leagues` to see all available leagues in your server

### Private League Access

For private leagues, you'll need to provide ESPN authentication:

```bash
/register_league league_id:123456 league_name:"My Private League" year:2025 swid:"{YOUR-SWID}" espn_s2:"YOUR-ESPN-S2-COOKIE"
```

### Interactive Menu System

Use `/menu` to access an interactive button-based interface for easier command navigation.

## 🐛 Troubleshooting

### Common Issues

**Bot not responding to commands:**
- Check that the bot has proper permissions in your Discord server
- Verify the bot token is correct in your `.env` file
- Use `/ping` to test basic connectivity

**"League not found" errors:**
- Verify your league ID is correct (found in ESPN URL)
- For private leagues, ensure SWID and espn_s2 cookies are valid
- Check that the season year matches your league settings

**ESPN API timeout errors:**
- ESPN API can be slow during peak times (Sunday game days)
- The bot includes automatic retry logic for temporary failures
- Commands will show "Fetching data..." while processing

### Getting Help

- Use `/help` for a quick command reference
- Use `/welcome` for a comprehensive setup guide

## 📝 Requirements

See `requirements.txt` for the complete list of Python dependencies. Key libraries include:

- `discord.py` - Discord bot framework
- `espn-api` - ESPN Fantasy Sports API wrapper
- `python-dotenv` - Environment variable management

---

**Note:** This bot is not affiliated with ESPN or Discord. ESPN Fantasy Football is a trademark of ESPN, Inc.
