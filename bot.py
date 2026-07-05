import os
import discord
from discord.ext import commands
from quart import Quart
import asyncio
import re
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

# Extract targeted folder path configurations
FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
if not FOLDER_ID:
    print("❌ ERROR: DRIVE_FOLDER_ID environment variable is missing!")

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
async def play(ctx, *, user_input: str):
    """Plays a track by index number or by searching part of its name with strict word boundaries."""
    if not ctx.voice_client:
        return await ctx.send("I need to be in a voice channel first! Use `!join`.")
    if not drive_manager:
        return await ctx.send("Google Drive system is misconfigured.")

    # 1. Fetch the file catalog structure map
    files = drive_manager.list_audio_files(FOLDER_ID)
    if not files:
        return await ctx.send("The Google Drive music folder is empty.")

    target_file = None

    # 2. Check if user provided direct integer index selection matching global tracking arrays
    if user_input.isdigit():
        track_number = int(user_input)
        if 1 <= track_number <= len(files):
            target_file = files[track_number - 1]
        else:
            return await ctx.send(f"Invalid track number. Choose 1 to {len(files)}.")
    
    # 3. Handle Regular Expression word boundary pattern matching engine filters
    else:
        query = user_input.lower().strip()
        matches = []
        for f in files:
            filename_lower = f['name'].lower()
            if re.search(rf"\b{re.escape(query)}\b", filename_lower):
                matches.append(f)

        if not matches:
            return await ctx.send(f"🔍 No tracks found matching the word boundary query: `{user_input}`.")
        
        elif len(matches) == 1:
            target_file = matches[0]
            await ctx.send(f"🎯 Exact word match verified!")
        
        else:
            response = f"🔍 Multiple matches found for word `{user_input}`. Please type `!play [number]` with the correct track:\n\n"
            for f in matches:
                orig_index = files.index(f) + 1
                response += f"**[{orig_index}]** {f['name']}\n"
            return await ctx.send(response)

    # 4. Storage processing and internal caching engine interface layer
    await ctx.send(f"Processing media allocation data for: `{target_file['name']}`...")

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    # Throw synchronous drive operations down a decoupled task worker loop thread pool executor
    loop = asyncio.get_event_loop()
    local_path, success = await loop.run_in_executor(
        None, drive_manager.get_or_download_track, target_file['id'], target_file['name']
    )

    if success and local_path:
        try:
            audio_source = discord.FFmpegPCMAudio(local_path)
            ctx.voice_client.play(
                audio_source, 
                after=lambda e: print(f"Finished playing. Errors: {e}")
            )
            await ctx.send(f"🎶 Now playing: `{target_file['name']}`")
        except Exception as e:
            await ctx.send(f"Failed to play audio stream via FFmpeg: {e}")
    else:
        await ctx.send("Could not retrieve or process data stream from file distribution services.")

# 3. Asynchronous Quart Web Server Configuration
web_app = Quart(__name__)

@web_app.route('/')
async def home():
    """Health check homepage endpoint for Render."""
    return "Bot is alive and running 24/7 with active 100MB LRU storage caching!"

# 4. Joint Dual-Process Execution Layer
async def main():
    port = int(os.getenv("PORT", 10000))
    
    loop = asyncio.get_event_loop()
    loop.create_task(web_app.run_task(host="0.0.0.0", port=port))
    
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
