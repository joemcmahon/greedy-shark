import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta

# Load configuration
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GRACE_PERIOD_MINUTES = int(os.getenv("GRACE_PERIOD_MINUTES", 15))
GRACE_PERIOD_FILE = ".grace_period_until"

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

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("ERROR: DISCORD_BOT_TOKEN not configured in .env file")
        exit(1)

    if DISCORD_CHANNEL_ID == 0:
        print("ERROR: DISCORD_CHANNEL_ID not configured in .env file")
        exit(1)

    print("Starting Grace Period Bot...")
    bot.run(DISCORD_BOT_TOKEN)
