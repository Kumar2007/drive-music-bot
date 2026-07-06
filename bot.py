import os
import discord
from discord.ext import commands
from quart import Quart
import asyncio
import re
import traceback
import sys
import random
from drive_utils import GoogleDriveManager

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

try:
    drive_manager = GoogleDriveManager()
except Exception as e:
    print(f"Failed to initialize Google Drive Manager: {e}")
    drive_manager = None

FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")

# --- GLOBAL HYBRID SESSION MANAGER ---
class GuildSession:
    def __init__(self):
        self.master_catalog = []   # Cached chronological file list from Google Drive
        self.queue = []            # User-curated custom queue list
        self.catalog_index = -1    # Pointer for default folder playback mode
        self.queue_index = -1      # Pointer for custom queue playback mode
        self.is_shuffled = False   
        self.manual_skip = False   

    def clear_all(self):
        self.queue = []
        self.catalog_index = -1
        self.queue_index = -1
        self.is_shuffled = False

    def in_queue_mode(self):
        # We are only in queue mode if the user actually added songs to the custom queue
        return len(self.queue) > 0

    def get_current_track(self):
        if self.in_queue_mode():
            if 0 <= self.queue_index < len(self.queue):
                return self.queue[self.queue_index]
        else:
            if 0 <= self.catalog_index < len(self.master_catalog):
                return self.master_catalog[self.catalog_index]
        return None

    def advance(self):
        if self.in_queue_mode():
            if self.is_shuffled:
                self.queue_index = random.randint(0, len(self.queue) - 1)
                return True
            elif self.queue_index + 1 < len(self.queue):
                self.queue_index += 1
                return True
            return False
        else:
            if not self.master_catalog:
                return False
            if self.is_shuffled:
                self.catalog_index = random.randint(0, len(self.master_catalog) - 1)
                return True
            elif self.catalog_index + 1 < len(self.master_catalog):
                self.catalog_index += 1
                return True
            return False

    def step_back(self):
        if self.in_queue_mode():
            if self.queue_index - 1 >= 0:
                self.queue_index -= 1
                return True
            return False
        else:
            if not self.master_catalog:
                return False
            if self.catalog_index - 1 >= 0:
                self.catalog_index -= 1
                return True
            return False

session = GuildSession()

# --- INTERNAL HELPER AUDIO STREAM ENGINE ---
async def start_track_stream(ctx, seek_time=None):
    target_file = session.get_current_track()
    if not target_file:
        return

    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("You must be in a voice room to listen!")

    await ctx.send(f"Processing Track: `{target_file['name']}`" + (f" (Seeking to {seek_time}s)..." if seek_time else "..."))

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        session.manual_skip = True
        ctx.voice_client.stop()
        await asyncio.sleep(0.1)

    loop = asyncio.get_event_loop()
    local_path, success = await loop.run_in_executor(
        None, drive_manager.get_or_download_track, target_file['id'], target_file['name']
    )

    if success and local_path:
        try:
            ffmpeg_options = f"-ss {seek_time}" if seek_time else None
            audio_source = discord.FFmpegPCMAudio(local_path, options=ffmpeg_options)
            
            session.manual_skip = False
            ctx.voice_client.play(
                audio_source, 
                after=lambda e: bot.loop.create_task(handle_autoplay_next(ctx, e))
            )
            await ctx.send(f"🎶 Now playing: `{target_file['name']}`")
        except Exception as e:
            await ctx.send(f"Failed to play stream via FFmpeg: {e}")
    else:
        await ctx.send("Could not retrieve track from Google Drive workspace storage.")

async def handle_autoplay_next(ctx, error):
    if error:
        print(f"Autoplay stream error log entry: {error}")
    if not ctx.voice_client or not ctx.voice_client.is_connected():
        return
    if session.manual_skip:
        session.manual_skip = False
        return

    if session.advance():
        await start_track_stream(ctx)
    else:
        await ctx.send("🏁 Reached the end of available playback tracks.")

def resolve_track_from_catalog(user_input):
    if not session.master_catalog:
        return None, "Catalog empty. Run `!list` first."
    
    if user_input.isdigit():
        idx = int(user_input) - 1
        if 0 <= idx < len(session.master_catalog):
            return [session.master_catalog[idx]], None
        return None, f"Invalid catalog number. Choose 1 to {len(session.master_catalog)}."
    
    query = user_input.lower().strip()
    matches = [f for f in session.master_catalog if re.search(rf"\b{re.escape(query)}\b", f['name'].lower())]
    if not matches:
        return None, f"🔍 No files found matching: `{user_input}`."
    return matches, None

# --- COMMANDS ---

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    print("------")

@bot.event
async def on_message(message):
    await bot.process_commands(message)

@bot.command(name="join")
async def join(ctx):
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
    if ctx.voice_client:
        session.manual_skip = True
        await ctx.voice_client.disconnect()
        session.clear_all()
        await ctx.send("Disconnected from voice channel and cleared active workspace queue state.")
    else:
        await ctx.send("I'm not in a voice channel!")

@bot.command(name="list")
async def list_catalog(ctx):
    if not drive_manager:
        return await ctx.send("Google Drive system is misconfigured.")
    await ctx.send("Fetching master track catalog from Google Drive...")
    files = drive_manager.list_audio_files(FOLDER_ID)
    if not files:
        return await ctx.send("No audio files found in the specified folder.")
    
    session.master_catalog = files  
    response = "**📁 Google Drive Master Catalog:**\n*(Type `!play <number>` to play directly, or `!add <number>` to build a queue)*\n\n"
    for idx, f in enumerate(files, 1):
        response += f"{idx}. `{f['name']}`\n"
    await ctx.send(response)

@bot.command(name="play")
async def play(ctx, *, user_input: str = None):
    if not session.master_catalog:
        session.master_catalog = drive_manager.list_audio_files(FOLDER_ID)

    if user_input is None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            return await ctx.send("▶️ Resumed track playback.")
        
        # Check fallback positions if typing plain !play
        if session.in_queue_mode():
            if session.queue_index == -1:
                session.queue_index = 0
            await start_track_stream(ctx)
        else:
            if session.catalog_index == -1:
                session.catalog_index = 0
            await start_track_stream(ctx)
        return

    matches, err = resolve_track_from_catalog(user_input)
    if err:
        return await ctx.send(err)
    if len(matches) > 1:
        response = f"🔍 Multiple matches found. Pick a precise index target:\n\n"
        for f in matches:
            orig_idx = session.master_catalog.index(f) + 1
            response += f"**[{orig_idx}]** {f['name']}\n"
        return await ctx.send(response)

    track = matches[0]
    
    # If custom queue is active, we append or override inside queue
    if session.in_queue_mode():
        session.queue.insert(session.queue_index + 1, track)
        session.queue_index += 1
        await ctx.send(f"Inserting into active queue playlist line...")
    else:
        # Jukebox default mode: just jump straight to the catalog position and flow naturally
        session.catalog_index = session.master_catalog.index(track)
        
    await start_track_stream(ctx)

@bot.command(name="add")
async def add(ctx, *, user_input: str):
    if not session.master_catalog:
        session.master_catalog = drive_manager.list_audio_files(FOLDER_ID)
        
    matches, err = resolve_track_from_catalog(user_input)
    if err:
        return await ctx.send(err)
    if len(matches) > 1:
        response = f"🔍 Multiple matches found. Be more specific:\n\n"
        for f in matches:
            orig_idx = session.master_catalog.index(f) + 1
            response += f"**[{orig_idx}]** {f['name']}\n"
        return await ctx.send(response)
    
    track = matches[0]
    session.queue.append(track)
    await ctx.send(f"➕ Added to Queue at **#{len(session.queue)}**: `{track['name']}`")

@bot.command(name="queue")
async def show_queue(ctx):
    if not session.in_queue_mode():
        return await ctx.send("The custom queue playlist is empty. Currently flowing natively through the Google Drive folder layout. Use `!add <item>` to create a queue!")
    
    response = "**🎵 Active Custom Playlist Queue:**\n"
    for idx, f in enumerate(session.queue):
        if idx == session.queue_index:
            response += f"▶️ **{idx + 1}. {f['name']}** *(Now Playing)*\n"
        else:
            response += f"{idx + 1}. `{f['name']}`\n"
    await ctx.send(response)

@bot.command(name="delete")
async def delete_track(ctx, position: int):
    if not session.in_queue_mode():
        return await ctx.send("There is no custom queue active to delete from.")
    
    idx = position - 1
    if idx < 0 or idx >= len(session.queue):
        return await ctx.send("Invalid position index calculation bounds.")
    
    is_playing_target = (idx == session.queue_index)
    removed = session.queue.pop(idx)
    await ctx.send(f"🗑️ Removed custom queue track: `{removed['name']}`")
    
    if idx < session.queue_index:
        session.queue_index -= 1
        
    if is_playing_target:
        if len(session.queue) == 0:
            session.manual_skip = True
            ctx.voice_client.stop()
            session.queue_index = -1
            await ctx.send("⏹️ Custom queue empty. Falling back to default catalog playback states.")
        else:
            if session.queue_index >= len(session.queue):
                session.queue_index = 0
            await start_track_stream(ctx)

@bot.command(name="clear")
async def clear_queue_cmd(ctx):
    session.queue = []
    session.queue_index = -1
    await ctx.send("🔄 **Custom Queue Cleared.** Restoring default continuous Drive folder autoplay flow.")

@bot.command(name="pause")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused playback.")

@bot.command(name="resume")
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed track playback.")

@bot.command(name="stop")
async def stop(ctx):
    if ctx.voice_client:
        session.manual_skip = True  
        ctx.voice_client.stop()
        session.clear_all()
        await ctx.send("⏹️ Stopped playback and cleared all active playlist trackers.")

@bot.command(name="next")
async def next_track(ctx):
    if session.advance():
        await start_track_stream(ctx)
    else:
        await ctx.send("🏁 Reached the end of available playback tracks.")

@bot.command(name="previous")
async def previous_track(ctx):
    if session.step_back():
        await start_track_stream(ctx)
    else:
        await ctx.send("⏮️ Already sitting at the very first tracking target.")

@bot.command(name="shuffle")
async def shuffle_queue(ctx):
    session.is_shuffled = not session.is_shuffled
    if session.is_shuffled:
        await ctx.send("🔀 **Shuffle Mode Activated.** Next tracks will be picked completely at random.")
    else:
        await ctx.send("🔁 **Shuffle Mode Deactivated.** Sequential pathing alignment restored.")

@bot.command(name="seek")
async def seek_track(ctx, seconds: int):
    if not ctx.voice_client or (not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused()):
        return await ctx.send("There is no active stream to seek timestamps on.")
    await start_track_stream(ctx, seek_time=seconds)

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🎵 Hybrid DriveMusicBot Help Menu",
        description="Works both as an automatic Jukebox or an On-Demand Playlist Planner. Prefix: `!`",
        color=discord.Color.teal()
    )
    embed.add_field(
        name="📻 Jukebox Mode (Default)",
        value="`!list` - Scans folder files.\n`!play <num/name>` - Plays a track directly and automatically autoplays the rest of the folder in chronological order.\n`!next` / `!previous` - Moves forward/back through the Drive folder naturally.",
        inline=False
    )
    embed.add_field(
        name="📋 Custom Queue Mode",
        value="`!add <num/name>` - Builds an overriding custom queue. The bot instantly switches focus to this list!\n`!queue` - Inspects custom lineup elements.\n`!delete <pos>` - Removes tracks from custom list.\n`!clear` - Empties queue, reverting right back to natural folder autoplay loops.",
        inline=False
    )
    await ctx.send(embed=embed)

web_app = Quart(__name__)

@web_app.route('/')
async def home():
    return "Hybrid Media System Status: ONLINE"

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
