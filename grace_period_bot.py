import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import time
import json
import functools
from typing import NamedTuple
from datetime import datetime, timedelta

# Import functions from monitor_stream
import sys
sys.path.insert(0, os.path.dirname(__file__))
from azuracast_client import AzuracastClient
from monitor_stream import (
    load_auto_suspended_streamers,
    remove_auto_suspended_streamer,
    add_auto_suspended_streamer,
    check_and_fix_suspended_file_path,
    MONITOR_STATE_FILE,
)
from station import (
    Station,
    StationConfigError,
    find_station,
    load_stations,
    station_names,
    streamer_station,
)

# Load configuration
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GRACE_PERIOD_MINUTES = int(os.getenv("GRACE_PERIOD_MINUTES", 15))
GRACE_PERIOD_FILE = ".grace_period_until"
AUTO_SUSPENDED_FILE = ".auto_suspended_streamers"
AZURACAST_BASE_URL = os.getenv("AZURACAST_BASE_URL")
AZURACAST_API_KEY = os.getenv("AZURACAST_API_KEY")
STATIONS_FILE = os.getenv("STATIONS_FILE", "stations.json")
STATIONS: "list[Station]" = []  # set at startup (see __main__)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

def tagged(message, station_name=""):
    """Prefix a message with the station name, if one is given."""
    return f"[{station_name}] {message}" if station_name else message


async def with_station(name, send_fn, stations, action):
    """Run `await action(station)` for the named station.

    Never guesses: if the name is missing or unknown, sends one refusal
    listing the valid names and does not call `action`.
    """
    st = find_station(name, stations)
    if st is None:
        names = station_names(stations)
        example = stations[-1].name if stations else "<station>"
        if name:
            problem = f"❌ Unknown station `{name}`."
        else:
            problem = "❌ Which station? Please name one."
        await send_fn(f"{problem} Stations: {names}. Example: `!woi {example}`")
        return
    await action(st)


class StreamerTools(NamedTuple):
    client: AzuracastClient
    track_fn: object
    untrack_fn: object
    load_fn: object


def streamer_tools(station, base_url, api_key):
    """Azuracast client and suspended-file functions bound to one station."""
    path = station.paths.suspended
    return StreamerTools(
        client=AzuracastClient(base_url or "", api_key or "",
                               station.azuracast_station_id),
        track_fn=functools.partial(add_auto_suspended_streamer, path=path),
        untrack_fn=functools.partial(remove_auto_suspended_streamer, path=path),
        load_fn=functools.partial(load_auto_suspended_streamers, path=path),
    )


async def with_streamer_station(send_fn, stations, action):
    """Run `await action(station)` for the one streamers-enabled station."""
    st = streamer_station(stations)
    if st is None:
        await send_fn("❌ No station has streamers enabled, so streamer "
                      "commands are unavailable.")
        return
    await action(st)


async def handle_shark_help(send_fn, grace_minutes=GRACE_PERIOD_MINUTES,
                            station_names=""):
    sa = " <station>" if station_names else ""
    stations_line = (f"**Stations:** {station_names}\n" if station_names else "")
    help_text = """
🦈 **Greedy Shark Bot Commands**

{stations_line}**Grace Period Commands:**
• `!working-on-it{sa}` (or `!woi{sa}`) - Activate a {grace_min}-minute grace period
  Pauses monitoring while you fix technical issues

• `!grace-status{sa}` (or `!gs{sa}`) - Check if grace period is active and when it expires

• `!cancel-grace{sa}` (or `!cg{sa}`) - Cancel an active grace period early

**Streamer Management:**
• `!streamers` - List all registered streamers with their IDs and status
  Use this first to find the ID you need for !shark

• `!shark <id>` - Suspend a streamer by their numeric Azuracast ID
  Example: `!shark 42`
  Recommended workflow: `!streamers` → find ID → `!shark <id>`

• `!sharked` - List all streamers currently suspended by the Shark
  Shows names, timestamps, and reasons for suspension

• `!unshark <id>` - Re-enable a suspended streamer by their numeric Azuracast ID
  Example: `!unshark 42`
  Recommended workflow: `!streamers` → find ID → `!unshark <id>`

• `!retrack <id>` - Mark an already-suspended streamer as tracked by the Shark
  Use when someone was suspended outside the bot and is missing from `!sharked`
  Example: `!retrack 42`

• `!shark-status{sa}` (or `!status{sa}`) - Show current Shark monitoring status

• `!shark-help` (or `!sharkhelp`) - Show this help message

**How the Shark Works:**
• No streamer connected: 2-minute silence → staff alert, repeated every 2 minutes
• Streamer connected: 2 minutes → heads-up, 4 minutes → warning,
  6 minutes → staff action alert, repeated every 2 minutes
• Staff uses `!streamers` then `!shark <id>` to suspend when needed
• Audio detection resets all timers
• Grace period pauses monitoring entirely
""".format(grace_min=grace_minutes, sa=sa, stations_line=stations_line)
    await send_fn(help_text)


async def handle_working_on_it(send_fn, grace_file=GRACE_PERIOD_FILE,
                               grace_minutes=GRACE_PERIOD_MINUTES,
                               station_name=""):
    async def say(m):
        await send_fn(tagged(m, station_name))

    try:
        expiration = datetime.now() + timedelta(minutes=grace_minutes)
        with open(grace_file, 'w') as f:
            f.write(str(expiration.timestamp()))
        await say(
            f"✅ Grace period activated! Monitoring suspended for {grace_minutes} minutes. "
            f"Auto-suspension disabled until {expiration.strftime('%H:%M:%S')}.")
    except Exception as e:
        await say(f"❌ Failed to activate grace period: {str(e)}")


async def handle_cancel_grace(send_fn, grace_file=GRACE_PERIOD_FILE, station_name=""):
    async def say(m):
        await send_fn(tagged(m, station_name))

    try:
        if os.path.exists(grace_file):
            os.remove(grace_file)
            await say("✅ Grace period cancelled. Normal monitoring resumed.")
        else:
            await say("ℹ️ No active grace period to cancel.")
    except Exception as e:
        await say(f"❌ Failed to cancel grace period: {str(e)}")


async def handle_grace_status(send_fn, grace_file=GRACE_PERIOD_FILE, station_name=""):
    async def say(m):
        await send_fn(tagged(m, station_name))

    try:
        if not os.path.exists(grace_file):
            await say("ℹ️ No active grace period. Normal monitoring active.")
            return
        with open(grace_file, 'r') as f:
            content = f.read().strip()
        if not content:
            await say("ℹ️ No active grace period. Normal monitoring active.")
            return
        expiration = datetime.fromtimestamp(float(content))
        now = datetime.now()
        if expiration > now:
            remaining = expiration - now
            minutes = int(remaining.total_seconds() / 60)
            seconds = int(remaining.total_seconds() % 60)
            await say(
                f"⏳ Grace period active. Expires in {minutes}m {seconds}s "
                f"at {expiration.strftime('%H:%M:%S')}.")
        else:
            await say("ℹ️ Grace period has expired. Normal monitoring active.")
    except Exception as e:
        await say(f"❌ Failed to check grace period status: {str(e)}")


async def handle_sharked(send_fn, load_fn=load_auto_suspended_streamers):
    try:
        suspended = load_fn()
        if not suspended:
            await send_fn("ℹ️ No streamers are currently suspended by the Shark. All clear! 🦈")
            return
        message = "🦈 **Streamers suspended by the Shark:**\n\n"
        for sid, info in suspended.items():
            name = info.get('name', 'Unknown')
            suspended_at = info.get('suspended_at', 'Unknown time')
            reason = info.get('reason', 'Unknown reason')
            try:
                time_str = datetime.fromisoformat(suspended_at).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                time_str = suspended_at
            message += f"• **{name}** (ID: {sid})\n"
            message += f"  ├ Suspended: {time_str}\n"
            message += f"  └ Reason: {reason}\n\n"
        message += "Use `!unshark <id>` to re-enable a streamer by their ID."
        await send_fn(message)
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_streamers(send_fn, client):
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Check logs for details.")
            return
        if not all_streamers:
            await send_fn("ℹ️ No streamers found in Azuracast.")
            return
        message = "🦈 **Registered Streamers:**\n\n"
        for s in sorted(all_streamers, key=lambda x: x.get('display_name', '').lower()):
            sid = s.get('id', '?')
            name = s.get('display_name', 'Unknown')
            status = "✅ active" if s.get('is_active', True) else "🔴 suspended"
            message += f"• **{name}** (ID: `{sid}`) — {status}\n"
        message += "\nUse `!shark <id>` to suspend a streamer by their ID."
        await send_fn(message)
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_shark(streamer_id, send_fn, client,
                       track_fn=add_auto_suspended_streamer, author_name="staff"):
    if streamer_id is None:
        await send_fn("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!shark <id>`.")
        return
    if not streamer_id.isdigit():
        await send_fn(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return
    sid = int(streamer_id)
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return
        target = next((s for s in all_streamers if s.get('id') == sid), None)
        if target is None:
            await send_fn(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return
        name = target.get('display_name', f'ID {sid}')
        if not target.get('is_active', True):
            await send_fn(f"ℹ️ **{name}** (ID: `{sid}`) is already suspended.")
            return
        if client.suspend_streamer(sid):
            track_fn(sid, name, reason="staff action via !shark")
            is_live, _, live_sid = client.check_streamer_connected()
            if is_live and live_sid == sid:
                if client.disconnect_streamer(sid):
                    await send_fn(f"🦈 **{name}** (ID: `{sid}`) has been suspended and disconnected by {author_name}.")
                else:
                    await send_fn(f"🦈 **{name}** (ID: `{sid}`) has been suspended by {author_name} (disconnect failed — they may drop on their own).")
            else:
                await send_fn(f"🦈 **{name}** (ID: `{sid}`) has been suspended by {author_name}.")
        else:
            await send_fn(f"❌ Failed to suspend **{name}** (ID: `{sid}`) via Azuracast API. Check logs.")
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_retrack(streamer_id, send_fn, client,
                         track_fn=add_auto_suspended_streamer, author_name="staff"):
    if streamer_id is None:
        await send_fn("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!retrack <id>`.")
        return
    if not streamer_id.isdigit():
        await send_fn(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return
    sid = int(streamer_id)
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return
        target = next((s for s in all_streamers if s.get('id') == sid), None)
        if target is None:
            await send_fn(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return
        name = target.get('display_name', f'ID {sid}')
        if target.get('is_active', True):
            await send_fn(f"❌ **{name}** (ID: `{sid}`) is not suspended — use `!shark {sid}` to suspend them first.")
            return
        track_fn(sid, name, reason="manual retrack via !retrack")
        await send_fn(f"✅ **{name}** (ID: `{sid}`) is now tracked as suspended by {author_name}.")
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_unshark(streamer_id, send_fn, client,
                         untrack_fn=remove_auto_suspended_streamer):
    if streamer_id is None:
        await send_fn("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!unshark <id>`.")
        return
    if not streamer_id.isdigit():
        await send_fn(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return
    sid = int(streamer_id)
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return
        target = next((s for s in all_streamers if s.get('id') == sid), None)
        if target is None:
            await send_fn(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return
        name = target.get('display_name', f'ID {sid}')
        if target.get('is_active', True):
            await send_fn(f"ℹ️ **{name}** (ID: `{sid}`) is not currently suspended.")
            return
        if client.reactivate_streamer(sid):
            untrack_fn(sid)
            await send_fn(f"✅ Successfully re-enabled **{name}** (ID: `{sid}`)!")
        else:
            await send_fn(f"❌ Failed to re-enable **{name}** (ID: `{sid}`) via Azuracast API. Check logs.")
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_shark_status(send_fn, client,
                              load_fn=load_auto_suspended_streamers,
                              state_file=MONITOR_STATE_FILE,
                              grace_file=GRACE_PERIOD_FILE,
                              station_name=""):
    async def say(m):
        await send_fn(tagged(m, station_name))

    try:
        suspended = load_fn()
        if not suspended:
            suspended_msg = "No users suspended"
        else:
            count = len(suspended)
            word = "user" if count == 1 else "users"
            names = [info.get('name', 'Unknown') for info in suspended.values()]
            suspended_msg = f"{count} {word} suspended: {', '.join(names)}"

        if not os.path.exists(state_file):
            status_msg = "Monitor state not available"
        else:
            try:
                with open(state_file, 'r') as f:
                    state_data = json.load(f)
                monitor_state = state_data.get('state', 'unknown')
                silence_checks = state_data.get('consecutive_silent_checks', 0)
                streamer_name = state_data.get('streamer_name', '')

                grace_active = False
                grace_remaining = 0
                if os.path.exists(grace_file):
                    try:
                        with open(grace_file, 'r') as f:
                            content = f.read().strip()
                        if content:
                            grace_ts = float(content)
                            now = time.time()
                            if grace_ts > now:
                                grace_active = True
                                grace_remaining = int((grace_ts - now) / 60)
                    except Exception:
                        pass

                if grace_active and streamer_name:
                    s = 's' if grace_remaining != 1 else ''
                    status_msg = (f"**{streamer_name}** streaming, "
                                  f"{grace_remaining} minute{s} into grace period")
                elif monitor_state == 'no_streamer':
                    if silence_checks == 0:
                        status_msg = "No silence detected"
                    else:
                        secs = silence_checks * 60
                        s = 's' if secs != 1 else ''
                        status_msg = f"No streamer, {secs} second{s} silence"
                elif monitor_state == 'streamer_active':
                    if silence_checks == 0:
                        status_msg = f"**{streamer_name}** streaming, no silence detected"
                    else:
                        s = 's' if silence_checks != 1 else ''
                        status_msg = f"**{streamer_name}** streaming, {silence_checks} minute{s} silence"
                else:
                    status_msg = "Monitor status unknown"
            except Exception as e:
                status_msg = f"Error reading monitor state: {str(e)}"

        message = "🦈 **Greedy Shark Status**\n\n"
        message += f"**Suspended:** {suspended_msg}\n"
        message += f"**Status:** {status_msg}"
        await say(message)
    except Exception as e:
        await say(f"❌ Error: {str(e)}")


@bot.event
async def on_ready():
    print(f'Grace Period Bot logged in as {bot.user}')
    print(f'Monitoring channel ID: {DISCORD_CHANNEL_ID}')
    print(f'Grace period duration: {GRACE_PERIOD_MINUTES} minutes')

    # Send startup message to Discord channel
    print('Attempting to send startup message...')
    try:
        print(f'Looking for channel {DISCORD_CHANNEL_ID}...')
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        print(f'Channel found: {channel}')
        if channel:
            print('Sending message...')
            await channel.send("🦈 **Greedy Shark is now online and monitoring!**\nType `!shark-help` for available commands.")
            print(f'✅ Startup message sent to channel {DISCORD_CHANNEL_ID}')
        else:
            print(f'❌ ERROR: Could not find channel {DISCORD_CHANNEL_ID}')
            print(f'Available guilds: {[g.name for g in bot.guilds]}')
    except Exception as e:
        print(f'❌ ERROR sending startup message: {e}')
        import traceback
        traceback.print_exc()

@bot.event
async def on_message(message):
    # Don't respond to ourselves
    if message.author == bot.user:
        return

    # Log all messages for debugging
    print(f'[DEBUG] Message from {message.author} in channel {message.channel.id}: {message.content}')

    # Process commands
    await bot.process_commands(message)

@bot.command(name='shark-help', aliases=['sharkhelp'])
async def shark_help(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_shark_help(ctx.send, station_names=station_names(STATIONS))

@bot.command(name='working-on-it', aliases=['workingonit', 'woi'])
async def working_on_it(ctx, station: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        await handle_working_on_it(ctx.send, grace_file=st.paths.grace,
                                   station_name=st.name)
    await with_station(station, ctx.send, STATIONS, action)

@bot.command(name='cancel-grace', aliases=['cancelgrace', 'cg'])
async def cancel_grace(ctx, station: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        await handle_cancel_grace(ctx.send, grace_file=st.paths.grace,
                                  station_name=st.name)
    await with_station(station, ctx.send, STATIONS, action)

@bot.command(name='grace-status', aliases=['gracestatus', 'gs'])
async def grace_status(ctx, station: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        await handle_grace_status(ctx.send, grace_file=st.paths.grace,
                                  station_name=st.name)
    await with_station(station, ctx.send, STATIONS, action)

@bot.command(name='unshark')
async def unshark(ctx, streamer_id: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        t = streamer_tools(st, AZURACAST_BASE_URL, AZURACAST_API_KEY)
        await handle_unshark(streamer_id, ctx.send, t.client, untrack_fn=t.untrack_fn)
    await with_streamer_station(ctx.send, STATIONS, action)

@bot.command(name='shark-status', aliases=['sharkstatus', 'status'])
async def shark_status(ctx, station: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        client = AzuracastClient(AZURACAST_BASE_URL or "", AZURACAST_API_KEY or "",
                                 st.azuracast_station_id)
        await handle_shark_status(
            ctx.send, client,
            load_fn=lambda: load_auto_suspended_streamers(st.paths.suspended),
            state_file=st.paths.monitor_state,
            grace_file=st.paths.grace,
            station_name=st.name)
    await with_station(station, ctx.send, STATIONS, action)

@bot.command(name='sharked')
async def sharked(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        t = streamer_tools(st, AZURACAST_BASE_URL, AZURACAST_API_KEY)
        await handle_sharked(ctx.send, load_fn=t.load_fn)
    await with_streamer_station(ctx.send, STATIONS, action)

@bot.command(name='streamers')
async def streamers(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        t = streamer_tools(st, AZURACAST_BASE_URL, AZURACAST_API_KEY)
        await handle_streamers(ctx.send, t.client)
    await with_streamer_station(ctx.send, STATIONS, action)

@bot.command(name='shark')
async def shark(ctx, streamer_id: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        t = streamer_tools(st, AZURACAST_BASE_URL, AZURACAST_API_KEY)
        await handle_shark(streamer_id, ctx.send, t.client, track_fn=t.track_fn,
                           author_name=ctx.author.display_name)
    await with_streamer_station(ctx.send, STATIONS, action)

@bot.command(name='retrack')
async def retrack(ctx, streamer_id: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async def action(st):
        t = streamer_tools(st, AZURACAST_BASE_URL, AZURACAST_API_KEY)
        await handle_retrack(streamer_id, ctx.send, t.client, track_fn=t.track_fn,
                             author_name=ctx.author.display_name)
    await with_streamer_station(ctx.send, STATIONS, action)

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("ERROR: DISCORD_BOT_TOKEN not configured in .env file")
        exit(1)

    if DISCORD_CHANNEL_ID == 0:
        print("ERROR: DISCORD_CHANNEL_ID not configured in .env file")
        exit(1)

    try:
        STATIONS = load_stations(STATIONS_FILE)
    except StationConfigError as e:
        print(f"ERROR: {e}")
        exit(1)
    for st in STATIONS:
        check_and_fix_suspended_file_path(st.paths.suspended)
    print("Starting Grace Period Bot...")
    bot.run(DISCORD_BOT_TOKEN)
