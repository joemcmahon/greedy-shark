import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import time
import json
from datetime import datetime, timedelta

# Import functions from monitor_stream
import sys
sys.path.insert(0, os.path.dirname(__file__))
from monitor_stream import (
    load_auto_suspended_streamers,
    remove_auto_suspended_streamer,
    reactivate_streamer,
    get_all_streamers,
    suspend_streamer,
    add_auto_suspended_streamer
)

# Load configuration
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GRACE_PERIOD_MINUTES = int(os.getenv("GRACE_PERIOD_MINUTES", 15))
GRACE_PERIOD_FILE = ".grace_period_until"
AUTO_SUSPENDED_FILE = ".auto_suspended_streamers"

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

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
    """
    Display all available Greedy Shark bot commands.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    help_text = """
🦈 **Greedy Shark Bot Commands**

**Grace Period Commands:**
• `!working-on-it` (or `!woi`) - Activate a {grace_min}-minute grace period
  Pauses auto-suspension monitoring while you fix technical issues

• `!grace-status` (or `!gs`) - Check if grace period is active and when it expires

• `!cancel-grace` (or `!cg`) - Cancel an active grace period early

**Streamer Management:**
• `!sharked` - List all streamers auto-suspended by the Shark
  Shows names, timestamps, and reasons for suspension

• `!letin <username>` - Re-enable a streamer auto-suspended by the Shark
  Example: `!letin TestDJ`
  Note: Only works on auto-suspensions, not manual staff suspensions

• `!shark-status` (or `!status`) - Show current Shark monitoring status
  Displays suspended users and stream silence status

• `!shark-help` (or `!sharkhelp`) - Show this help message

**How the Shark Works:**
• No streamer connected: 2-minute silence → alert
• Streamer connected: 8-minute warning, 10-minute auto-suspension
• Audio detection resets all timers
• Grace period pauses monitoring entirely
""".format(grace_min=GRACE_PERIOD_MINUTES)

    await ctx.send(help_text)

@bot.command(name='working-on-it', aliases=['workingonit', 'woi'])
async def working_on_it(ctx):
    """
    Grant a grace period to the current streamer.
    This prevents automatic suspension for the configured duration.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        # Calculate grace period expiration time
        expiration = datetime.now() + timedelta(minutes=GRACE_PERIOD_MINUTES)
        timestamp = expiration.timestamp()

        # Write timestamp to file
        with open(GRACE_PERIOD_FILE, 'w') as f:
            f.write(str(timestamp))

        # Send confirmation
        await ctx.send(f"✅ Grace period activated! Monitoring suspended for {GRACE_PERIOD_MINUTES} minutes. "
                      f"Auto-suspension disabled until {expiration.strftime('%H:%M:%S')}.")
        print(f"Grace period activated by {ctx.author} until {expiration}")

    except Exception as e:
        await ctx.send(f"❌ Failed to activate grace period: {str(e)}")
        print(f"Error activating grace period: {e}")

@bot.command(name='cancel-grace', aliases=['cancelgrace', 'cg'])
async def cancel_grace(ctx):
    """
    Cancel an active grace period and return to normal monitoring.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        if os.path.exists(GRACE_PERIOD_FILE):
            os.remove(GRACE_PERIOD_FILE)
            await ctx.send("✅ Grace period cancelled. Normal monitoring resumed.")
            print(f"Grace period cancelled by {ctx.author}")
        else:
            await ctx.send("ℹ️ No active grace period to cancel.")

    except Exception as e:
        await ctx.send(f"❌ Failed to cancel grace period: {str(e)}")
        print(f"Error cancelling grace period: {e}")

@bot.command(name='grace-status', aliases=['gracestatus', 'gs'])
async def grace_status(ctx):
    """
    Check if a grace period is currently active and when it expires.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        if os.path.exists(GRACE_PERIOD_FILE):
            with open(GRACE_PERIOD_FILE, 'r') as f:
                content = f.read().strip()

            if not content:
                await ctx.send("ℹ️ No active grace period. Normal monitoring active.")
                return

            timestamp = float(content)
            expiration = datetime.fromtimestamp(timestamp)
            now = datetime.now()

            if expiration > now:
                remaining = expiration - now
                minutes = int(remaining.total_seconds() / 60)
                seconds = int(remaining.total_seconds() % 60)
                await ctx.send(f"⏳ Grace period active. Expires in {minutes}m {seconds}s at {expiration.strftime('%H:%M:%S')}.")
            else:
                await ctx.send("ℹ️ Grace period has expired. Normal monitoring active.")
        else:
            await ctx.send("ℹ️ No active grace period. Normal monitoring active.")

    except Exception as e:
        await ctx.send(f"❌ Failed to check grace period status: {str(e)}")
        print(f"Error checking grace period: {e}")

@bot.command(name='letin', aliases=['let-in'])
async def letin(ctx, username: str):
    """
    Re-enable a streamer that was auto-suspended by the Shark.
    Only works on streamers suspended by the monitoring system.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        # Get list of auto-suspended streamers
        suspended = load_auto_suspended_streamers()

        if not suspended:
            await ctx.send("ℹ️ No streamers are currently auto-suspended by the Shark.")
            return

        # Find the streamer by name
        streamer_id = None
        streamer_info = None

        for sid, info in suspended.items():
            if info['name'].lower() == username.lower():
                streamer_id = int(sid)
                streamer_info = info
                break

        if not streamer_id:
            # Check if streamer exists but wasn't auto-suspended
            all_streamers = get_all_streamers()
            if all_streamers:
                for s in all_streamers:
                    if s.get('display_name', '').lower() == username.lower():
                        await ctx.send(f"ℹ️ '{username}' is not suspended by the Shark. "
                                     f"They may have been manually suspended by staff or are already active.")
                        return

            await ctx.send(f"❌ Streamer '{username}' not found in auto-suspended list. "
                         f"Use `!sharked` to see who the Shark has suspended.")
            return

        # Re-enable the streamer via Azuracast API
        if reactivate_streamer(streamer_id):
            # Remove from tracking list
            remove_auto_suspended_streamer(streamer_id)
            await ctx.send(f"✅ Successfully re-enabled '{streamer_info['name']}'! "
                         f"They were auto-suspended {streamer_info.get('reason', 'for silence')}.")
            print(f"Streamer {streamer_info['name']} re-enabled by {ctx.author}")
        else:
            await ctx.send(f"❌ Failed to re-enable '{streamer_info['name']}' via Azuracast API. "
                         f"Check logs for details.")

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in letin command: {e}")

@bot.command(name='shark-status', aliases=['sharkstatus', 'status'])
async def shark_status(ctx):
    """
    Display current Shark monitoring status.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        # Load suspended streamers
        suspended = load_auto_suspended_streamers()

        # Build suspended users summary
        if not suspended:
            suspended_msg = "No users suspended"
        else:
            count = len(suspended)
            user_word = "user" if count == 1 else "users"
            names = [info.get('name', 'Unknown') for info in suspended.values()]
            suspended_msg = f"{count} {user_word} suspended: {', '.join(names)}"

        # Load monitor state from shared file
        state_file = ".monitor_state"
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state_data = json.load(f)

                monitor_state = state_data.get('state', 'unknown')
                silence_checks = state_data.get('consecutive_silent_checks', 0)
                streamer_name = state_data.get('streamer_name', '')

                # Check if grace period is active
                grace_active = False
                grace_remaining = 0
                if os.path.exists(GRACE_PERIOD_FILE):
                    try:
                        with open(GRACE_PERIOD_FILE, 'r') as f:
                            content = f.read().strip()
                        if content:
                            grace_timestamp = float(content)
                            now = time.time()
                            if grace_timestamp > now:
                                grace_active = True
                                grace_remaining = int((grace_timestamp - now) / 60)
                    except:
                        pass

                # Build status message based on state
                if grace_active and streamer_name:
                    status_msg = f"**{streamer_name}** streaming, {grace_remaining} minute{'s' if grace_remaining != 1 else ''} into grace period"
                elif monitor_state == 'no_streamer':
                    if silence_checks == 0:
                        status_msg = "No silence detected"
                    else:
                        seconds = silence_checks * 60
                        status_msg = f"No streamer, {seconds} second{'s' if seconds != 1 else ''} silence"
                elif monitor_state == 'streamer_active':
                    if silence_checks == 0:
                        status_msg = f"**{streamer_name}** streaming, no silence detected"
                    else:
                        minutes = silence_checks
                        status_msg = f"**{streamer_name}** streaming, {minutes} minute{'s' if minutes != 1 else ''} silence"
                else:
                    status_msg = "Monitor status unknown"
            except Exception as e:
                status_msg = f"Error reading monitor state: {str(e)}"
        else:
            status_msg = "Monitor state not available"

        # Build final message
        message = f"🦈 **Greedy Shark Status**\n\n"
        message += f"**Suspended:** {suspended_msg}\n"
        message += f"**Status:** {status_msg}"

        await ctx.send(message)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in shark-status command: {e}")

@bot.command(name='sharked')
async def sharked(ctx):
    """
    List all streamers that have been auto-suspended by the Shark.
    """
    # Only respond in the configured channel
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        suspended = load_auto_suspended_streamers()

        if not suspended:
            await ctx.send("ℹ️ No streamers are currently auto-suspended by the Shark. All clear! 🦈")
            return

        # Build the message
        message = "🦈 **Streamers auto-suspended by the Shark:**\n\n"

        for sid, info in suspended.items():
            name = info.get('name', 'Unknown')
            suspended_at = info.get('suspended_at', 'Unknown time')
            reason = info.get('reason', 'Unknown reason')

            # Parse and format the timestamp
            try:
                dt = datetime.fromisoformat(suspended_at)
                time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                time_str = suspended_at

            message += f"• **{name}** (ID: {sid})\n"
            message += f"  ├ Suspended: {time_str}\n"
            message += f"  └ Reason: {reason}\n\n"

        message += f"Use `!letin <username>` to re-enable a streamer."

        await ctx.send(message)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in sharked command: {e}")

@bot.command(name='streamers')
async def streamers(ctx):
    """
    List all streamers registered in Azuracast with their IDs and status.
    Use IDs with !shark to suspend a specific streamer.
    """
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        all_streamers = get_all_streamers()

        if all_streamers is None:
            await ctx.send("❌ Failed to fetch streamers from Azuracast. Check logs for details.")
            return

        if not all_streamers:
            await ctx.send("ℹ️ No streamers found in Azuracast.")
            return

        message = "🦈 **Registered Streamers:**\n\n"
        for s in sorted(all_streamers, key=lambda x: x.get('display_name', '').lower()):
            sid = s.get('id', '?')
            name = s.get('display_name', 'Unknown')
            active = s.get('is_active', True)
            status = "✅ active" if active else "🔴 suspended"
            message += f"• **{name}** (ID: `{sid}`) — {status}\n"

        message += f"\nUse `!shark <id>` to suspend a streamer by their ID."
        await ctx.send(message)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in streamers command: {e}")

@bot.command(name='shark')
async def shark(ctx, streamer_id: str = None):
    """
    Suspend a specific streamer by their Azuracast numeric ID.
    Use !streamers to find the ID first.
    """
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    # Require an ID argument
    if streamer_id is None:
        await ctx.send("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!shark <id>`.")
        return

    # Validate it's numeric
    if not streamer_id.isdigit():
        await ctx.send(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return

    sid = int(streamer_id)

    try:
        # Look up the streamer in Azuracast to verify they exist and get their name
        all_streamers = get_all_streamers()
        if all_streamers is None:
            await ctx.send("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return

        target = None
        for s in all_streamers:
            if s.get('id') == sid:
                target = s
                break

        if target is None:
            await ctx.send(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return

        name = target.get('display_name', f'ID {sid}')

        # Check if already suspended
        if not target.get('is_active', True):
            await ctx.send(f"ℹ️ **{name}** (ID: `{sid}`) is already suspended.")
            return

        # Suspend via Azuracast API
        if suspend_streamer(sid):
            add_auto_suspended_streamer(sid, name, reason="staff action via !shark")
            await ctx.send(f"🦈 **{name}** (ID: `{sid}`) has been suspended by {ctx.author.display_name}.")
            print(f"Streamer {name} (ID: {sid}) suspended by {ctx.author}")
        else:
            await ctx.send(f"❌ Failed to suspend **{name}** (ID: `{sid}`) via Azuracast API. Check logs.")

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in shark command: {e}")

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("ERROR: DISCORD_BOT_TOKEN not configured in .env file")
        exit(1)

    if DISCORD_CHANNEL_ID == 0:
        print("ERROR: DISCORD_CHANNEL_ID not configured in .env file")
        exit(1)

    print("Starting Grace Period Bot...")
    bot.run(DISCORD_BOT_TOKEN)
