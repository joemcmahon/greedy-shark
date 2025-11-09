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
    get_all_streamers
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
                timestamp = float(f.read().strip())

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

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("ERROR: DISCORD_BOT_TOKEN not configured in .env file")
        exit(1)

    if DISCORD_CHANNEL_ID == 0:
        print("ERROR: DISCORD_CHANNEL_ID not configured in .env file")
        exit(1)

    print("Starting Grace Period Bot...")
    bot.run(DISCORD_BOT_TOKEN)
