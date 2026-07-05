import os
import discord
from discord.ext import commands
from quart import Quart
import asyncio
from drive_utils import GoogleDriveManager

# 1. Initialize the Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize the Google Drive manager (Handles local file or cloud environment string)
try:
    drive_manager = GoogleDriveManager()
except Exception as e:
    print(f"Failed to initialize Google Drive Manager: {e}")
    drive_manager = None

# Hardcoded Google Drive Folder ID containing your audio tracks
FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "1koYriiJvKEGIM-WvyEJShRArzi-tuHYV")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    print("------")

# 2. Discord Bot Commands
@bot.command(name="join")
async def join(ctx):
    """Joins the user's current voice channel."""
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
        await ctx.send(f"Joined **{channel.name}**!")
    else:
        await ctx.send("You need to be in a voice channel first!")

@bot.command(name="leave")
async def leave(ctx):
    """Disconnects the bot from the voice channel."""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected from voice channel.")
    else:
        await ctx.send("I'm not in a voice channel!")

@bot.command(name="list")
async def list_tracks(ctx):
    """Lists the audio files available in the targeted Google Drive folder."""
    if not drive_manager:
        return await ctx.send("Google Drive system is misconfigured.")
        
    await ctx.send("Fetching tracks from Google Drive...")
    files = drive_manager.list_audio_files(FOLDER_ID)
    
    if not files:
        return await ctx.send("No audio files found in the specified folder.")
        
    response = "**Available Tracks:**\n"
    for idx, f in enumerate(files, 1):
        response += f"{idx}. `{f['name']}`\n"
    await ctx.send(response)

@bot.command(name="play")
async def play(ctx, track_number: int):
    """Downloads a file by index from Drive and streams it through voice."""
    if not ctx.voice_client:
        return await ctx.send("I need to be in a voice channel first! Use `!join`.")
    if not drive_manager:
        return await ctx.send("Google Drive system is misconfigured.")

    files = drive_manager.list_audio_files(FOLDER_ID)
    if not files or track_number < 1 or track_number > len(files):
        return await ctx.send("Invalid track number. Use `!list` to see options.")

    target_file = files[track_number - 1]
    await ctx.send(f"Downloading and preparing to play: `{target_file['name']}`...")

    # Create local temp path for audio streaming
    os.makedirs("temp", exist_ok=True)
    local_path = f"temp/{target_file['id']}.mp3"

    # Stop current playing context if active
    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    # Thread-safe download handler
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None, drive_manager.download_file, target_file['id'], local_path
    )

    if success:
        try:
            # Load file directly into Discord voice via FFmpeg
            audio_source = discord.FFmpegPCMAudio(local_path)
            ctx.voice_client.play(
                audio_source, 
                after=lambda e: print(f"Finished playing. Errors: {e}")
            )
            await ctx.send(f"🎶 Now playing: `{target_file['name']}`")
        except Exception as e:
            await ctx.send(f"Failed to play audio stream via FFmpeg: {e}")
    else:
        await ctx.send("Could not retrieve file from Google Drive.")

# 3. Asynchronous Quart Web Server Configuration
web_app = Quart(__name__)

@web_app.route('/')
async def home():
    """Health check homepage endpoint for Render."""
    return "Bot is alive and running 24/7!"

# 4. Joint Dual-Process Execution Layer
async def main():
    # Dynamically read port values injected via Render dashboard environment maps
    port = int(os.getenv("PORT", 10000))
    
    # Fire up the lightweight asynchronous web layer in background process routine
    loop = asyncio.get_event_loop()
    # Start Quart server (without use_reloader)
    loop.create_task(web_app.run_task(host="0.0.0.0", port=port))
    
    # Authenticate and engage core Discord event architecture loops
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise ValueError("Critical Error: 'DISCORD_TOKEN' environment variable is missing!")
        
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot application terminated locally.")
