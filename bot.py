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
bot.remove_command("help")  # Unregister built-in help to allow our custom embed menu

try:
    drive_manager = GoogleDriveManager()
except Exception as e:
    print(f"Failed to initialize Google Drive Manager: {e}")
    drive_manager = None

FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")

# --- GLOBAL DYNAMIC QUEUE SESSION MANAGER ---
class GuildSession:
    def __init__(self):
        self.master_catalog = []   # Cached master file list fetched directly from Google Drive
        self.queue = []            # Dynamic runtime queue (the active playlist)
        self.current_index = -1    # Current track pointing inside self.queue
        self.is_shuffled = False   # Shuffle state flag
        self.manual_skip = False   # Bugfix flag to intercept the autoplay .stop() cascade

    def add_to_queue(self, track):
        self.queue.append(track)
        return len(self.queue)

    def remove_from_queue(self, index):
        if 0 <= index < len(self.queue):
            removed = self.queue.pop(index)
            # Adjust index pointers if the removal shifts elements backward
            if index < self.current_index:
                self.current_index -= 1
            return removed
        return None

    def clear_queue(self):
        self.queue = []
        self.current_index = -1
        self.is_shuffled = False

    def get_current_track(self):
        if not self.queue or not (0 <= self.current_index < len(self.queue)):
            return None
        return self.queue[self.current_index]

    def advance(self):
        if not self.queue:
            return False
        
        if self.is_shuffled:
            # Shuffle Mode Selection: Jump to a completely random index in the current queue bounds
            self.current_index = random.randint(0, len(self.queue) - 1)
            return True
        else:
            # Standard sequential playback progression
            if self.current_index + 1 < len(self.queue):
                self.current_index += 1
                return True
        return False

    def step_back(self):
        if not self.queue:
            return False
        # Shuffle back tracking defaults to sequential fallback in this implementation layout
        if self.current_index - 1 >= 0:
            self.current_index -= 1
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
        print("ℹ️ End of active runtime playlist queue reached.")

# --- UTILITY CATALOG RESOLVER ---
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
        return None, f"🔍 No files found in Drive catalog matching: `{user_input}`."
    return matches, None

# --- COMMANDS ---

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    print("------")

@bot.event
async def on_message(message):
    print(f"📩 [RAW MESSAGE] Author: {message.author} | Content: '{message.content}'")
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    print(f"❌ [COMMAND ERROR] Triggered by '{ctx.message.content}': {error}", file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    await ctx.send(f"⚠️ An internal error occurred: `{error}`")

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
        session.clear_queue()
        await ctx.send("Disconnected from voice channel and cleared active workspace queue.")
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
    response = "**📁 Google Drive Master Catalog:**\n*(Use `!add <number>` to queue a song)*\n\n"
    for idx, f in enumerate(files, 1):
        response += f"{idx}. `{f['name']}`\n"
    await ctx.send(response)

@bot.command(name="add")
async def add(ctx, *, user_input: str):
    if not session.master_catalog:
        session.master_catalog = drive_manager.list_audio_files(FOLDER_ID)
        
    matches, err = resolve_track_from_catalog(user_input)
    if err:
        return await ctx.send(err)
    
    if len(matches) > 1:
        response = f"🔍 Multiple catalog matches found for `{user_input}`. Be more specific or use numbers:\n\n"
        for f in matches:
            orig_idx = session.master_catalog.index(f) + 1
            response += f"**[{orig_idx}]** {f['name']}\n"
        return await ctx.send(response)
    
    track = matches[0]
    pos = session.add_to_queue(track)
    await ctx.send(f"➕ Added to Queue at **#{pos}**: `{track['name']}`")

@bot.command(name="queue")
async def show_queue(ctx):
    if not session.queue:
        return await ctx.send("The playlist queue is currently empty. Add tracks with `!add <name/number>`!")
    
    response = "**🎵 Active Playlist Queue:**\n"
    if session.is_shuffled:
        response += "*(Shuffle Mode: Active)*\n"
        
    for idx, f in enumerate(session.queue):
        if idx == session.current_index:
            response += f"▶️ **{idx + 1}. {f['name']}** *(Now Playing)*\n"
        else:
            response += f"{idx + 1}. `{f['name']}`\n"
    await ctx.send(response)

@bot.command(name="play")
async def play(ctx, *, user_input: str = None):
    if user_input is None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            return await ctx.send("▶️ Resumed track playback.")
        elif session.queue and session.current_index == -1:
            session.current_index = 0
            await start_track_stream(ctx)
            return
        elif not session.queue:
            return await ctx.send("Queue is empty. Use `!add <item>` or `!play <item>` to start streaming.")
        else:
            return await ctx.send("Already playing. Use `!queue` to see upcoming tracks.")

    # Explicit !play targets: Wipe queue, fetch target item, set as item 1, play immediately
    if not session.master_catalog:
        session.master_catalog = drive_manager.list_audio_files(FOLDER_ID)

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
    session.clear_queue()
    session.add_to_queue(track)
    session.current_index = 0
    await start_track_stream(ctx)

@bot.command(name="delete")
async def delete_track(ctx, position: int):
    idx = position - 1
    if idx < 0 or idx >= len(session.queue):
        return await ctx.send(f"Invalid position. Current queue sizing range bounds: 1 to {len(session.queue)}")
    
    is_playing_target = (idx == session.current_index)
    removed = session.remove_from_queue(idx)
    
    await ctx.send(f"🗑️ Removed track from playlist layout position: `{removed['name']}`")
    
    if is_playing_target:
        if len(session.queue) == 0:
            session.manual_skip = True
            ctx.voice_client.stop()
            session.current_index = -1
            await ctx.send("⏹️ Active playlist queue depleted. Halting audio core engine.")
        else:
            if session.current_index >= len(session.queue):
                session.current_index = 0
            await ctx.send("Skipping forward to the next index shift adjustment...")
            await start_track_stream(ctx)

@bot.command(name="pause")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused playback.")
    else:
        await ctx.send("Nothing is currently playing.")

@bot.command(name="resume")
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed track playback.")
    else:
        await ctx.send("Playback is not paused.")

@bot.command(name="stop")
async def stop(ctx):
    if ctx.voice_client:
        session.manual_skip = True  
        ctx.voice_client.stop()
        session.clear_queue()
        await ctx.send("⏹️ Stopped playback and cleared out live active playlist memory mapping.")
    else:
        await ctx.send("I'm not in a voice channel.")

@bot.command(name="next")
async def next_track(ctx):
    if session.advance():
        await start_track_stream(ctx)
    else:
        await ctx.send("🏁 Reached the end of your live active playlist queue selection.")

@bot.command(name="previous")
async def previous_track(ctx):
    if session.step_back():
        await start_track_stream(ctx)
    else:
        await ctx.send("⏮️ Already sitting at the first tracking point of the active queue.")

@bot.command(name="shuffle")
async def shuffle_queue(ctx):
    session.is_shuffled = not session.is_shuffled
    if session.is_shuffled:
        await ctx.send("🔀 **Shuffle On-Demand Activated.** The queue will select random indices on progression tracks.")
    else:
        await ctx.send("🔁 **Shuffle Mode Deactivated.** Sequential playlist alignment restored.")

@bot.command(name="seek")
async def seek_track(ctx, seconds: int):
    if not ctx.voice_client or (not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused()):
        return await ctx.send("There is no active stream to seek timestamps on.")
    await start_track_stream(ctx, seek_time=seconds)

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🎵 DriveMusicBot Engine Help System",
        description="Dynamic playlist tracking synced with Google Drive. Global prefix: `!`",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="📁 Catalog Operations",
        value="`!list` - Scans master Google Drive catalog directories.\n`!add <num/name>` - Appends target item onto runtime playlist arrays.",
        inline=False
    )
    embed.add_field(
        name="📋 Queue Systems",
        value="`!queue` - Shows your active dynamic queue track listing layout.\n`!delete <pos>` - Discards explicit positional tracker elements from active queues.\n`!shuffle` - Toggles randomized on-demand play selection matrices.",
        inline=False
    )
    embed.add_field(
        name="🎮 Flow Management",
        value="`!play <num/name>` - Wipes playlist tracking layout, sets matching track to index #1, plays instantly.\n`!play` - Resumes audio tracks or launches inactive queues.\n`!pause` / `!resume` / `!stop` - Baseline engine stream toggles.\n`!next` / `!previous` - Moves within the active queue.\n`!seek <seconds>` - Changes timeline positional play coordinates.",
        inline=False
    )
    await ctx.send(embed=embed)

web_app = Quart(__name__)

@web_app.route('/')
async def home():
    return "Dynamic Media Queue Engine Status: ONLINE"

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
