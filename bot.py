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

try:
    drive_manager = GoogleDriveManager()
except Exception as e:
    print(f"Failed to initialize Google Drive Manager: {e}")
    drive_manager = None

FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")

# --- GLOBAL SESSION MANAGER ---
class GuildSession:
    def __init__(self):
        self.queue = []            
        self.current_index = -1     
        self.is_shuffled = False   
        self.shuffled_order = []   
        self.manual_skip = False   # CRUCIAL BUGFIX FLAG: Prevents the .stop() loop cascade

    def set_tracks(self, tracks):
        self.queue = tracks
        self.current_index = -1
        self.is_shuffled = False
        self.shuffled_order = []

    def toggle_shuffle(self):
        if not self.queue:
            return False
            
        if not self.is_shuffled:
            # --- SWITCHING TO SHUFFLE MODE ---
            # 1. Generate the randomized index deck layout first
            self.shuffled_order = list(range(len(self.queue)))
            random.shuffle(self.shuffled_order)
            
            # 2. Map the current index safely if a song was already playing
            if 0 <= self.current_index < len(self.queue):
                # Save the absolute chronological index position
                playing_idx = self.current_index 
                # Align our session pointer to wherever that index landed in the shuffled deck
                self.current_index = self.shuffled_order.index(playing_idx)
            else:
                self.current_index = 0
                
            self.is_shuffled = True
        else:
            # --- RETURNING TO NORMAL MODE ---
            # Revert smoothly back to true chronological index position
            if self.shuffled_order and 0 <= self.current_index < len(self.shuffled_order):
                self.current_index = self.shuffled_order[self.current_index]
            else:
                self.current_index = 0
                
            self.is_shuffled = False
            self.shuffled_order = []
            
        return self.is_shuffled

    def get_current_track(self):
        if not self.queue:
            return None
        idx = self.shuffled_order[self.current_index] if self.is_shuffled else self.current_index
        if 0 <= idx < len(self.queue):
            return self.queue[idx]
        return None

    def advance(self):
        if not self.queue:
            return False
        limit = len(self.shuffled_order) if self.is_shuffled else len(self.queue)
        if self.current_index + 1 < limit:
            self.current_index += 1
            return True
        return False

    def step_back(self):
        if not self.queue:
            return False
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

    # If already playing, raise the manual skip flag BEFORE calling stop()
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        session.manual_skip = True
        ctx.voice_client.stop()
        # Give the audio thread a tiny millisecond to cleanly register termination events
        await asyncio.sleep(0.1)

    loop = asyncio.get_event_loop()
    local_path, success = await loop.run_in_executor(
        None, drive_manager.get_or_download_track, target_file['id'], target_file['name']
    )

    if success and local_path:
        try:
            ffmpeg_options = f"-ss {seek_time}" if seek_time else None
            audio_source = discord.FFmpegPCMAudio(local_path, options=ffmpeg_options)
            
            # Reset skip state right before attaching the new stream
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
        print(f"Autoplay stream feedback error report: {error}")
        
    # DISCONNECT SAFETY CHECK: If we disconnected manually (!leave), kill the autoplay engine immediately
    if not ctx.voice_client or not ctx.voice_client.is_connected():
        print("ℹ️ Autoplay aborted because the bot disconnected from the voice channel.")
        return

    # COMMAND OVERRIDE CHECK: If the song ended because of !next/!play/!seek, abort autoplay intervention
    if session.manual_skip:
        session.manual_skip = False
        return

    if session.advance():
        await start_track_stream(ctx)

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
    print("📥 !join command triggered")
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
    print("📤 !leave command triggered")
    if ctx.voice_client:
        session.manual_skip = True  # Signal to block the cascading autoplay loop
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected from voice channel.")
    else:
        await ctx.send("I'm not in a voice channel!")

@bot.command(name="list")
async def list_tracks(ctx):
    print("📋 !list command triggered")
    if not drive_manager:
        return await ctx.send("Google Drive system is misconfigured.")
    await ctx.send("Fetching tracks from Google Drive...")
    files = drive_manager.list_audio_files(FOLDER_ID)
    if not files:
        return await ctx.send("No audio files found in the specified folder.")
    
    session.queue = files  
    
    response = "**Available Tracks:**\n"
    if session.is_shuffled:
        response += "*(Shuffle Mode Active)*\n"
    for idx, f in enumerate(files, 1):
        response += f"{idx}. `{f['name']}`\n"
    await ctx.send(response)

@bot.command(name="play")
async def play(ctx, *, user_input: str = None):
    print(f"🎵 !play command triggered with input: {user_input}")
    if not drive_manager:
        return await ctx.send("Google Drive system is misconfigured.")

    if not session.queue:
        session.queue = drive_manager.list_audio_files(FOLDER_ID)
    if not session.queue:
        return await ctx.send("The Google Drive music folder is empty.")

    if user_input is None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            return await ctx.send("▶️ Resumed track playback.")
        elif session.current_index == -1:
            session.current_index = 0
            await start_track_stream(ctx)
            return
        else:
            return await ctx.send("Already playing or no active queue selected. Use `!play <number/name>`")

    if user_input.isdigit():
        track_number = int(user_input)
        if 1 <= track_number <= len(session.queue):
            if session.is_shuffled:
                target_idx = track_number - 1
                if target_idx in session.shuffled_order:
                    session.shuffled_order.remove(target_idx)
                    session.shuffled_order.insert(0, target_idx)
                session.current_index = 0
            else:
                session.current_index = track_number - 1
            await start_track_stream(ctx)
        else:
            await ctx.send(f"Invalid track number. Choose 1 to {len(session.queue)}.")
    else:
        query = user_input.lower().strip()
        matches = [f for f in session.queue if re.search(rf"\b{re.escape(query)}\b", f['name'].lower())]

        if not matches:
            return await ctx.send(f"🔍 No tracks found matching: `{user_input}`.")
        elif len(matches) == 1:
            orig_idx = session.queue.index(matches[0])
            if session.is_shuffled:
                if orig_idx in session.shuffled_order:
                    session.shuffled_order.remove(orig_idx)
                    session.shuffled_order.insert(0, orig_idx)
                session.current_index = 0
            else:
                session.current_index = orig_idx
            await start_track_stream(ctx)
        else:
            response = f"🔍 Multiple matches found for `{user_input}`. Choose a track number:\n\n"
            for f in matches:
                orig_index = session.queue.index(f) + 1
                response += f"**[{orig_index}]** {f['name']}\n"
            await ctx.send(response)

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
        session.manual_skip = True  # Block autoplay trigger
        ctx.voice_client.stop()
        session.current_index = -1
        await ctx.send("⏹️ Stopped playback and reset queue tracking pointer.")
    else:
        await ctx.send("I'm not in a voice channel.")

@bot.command(name="next")
async def next_track(ctx):
    if session.advance():
        await start_track_stream(ctx)
    else:
        await ctx.send("🏁 Reached the end of your playlist queue selection.")

@bot.command(name="previous")
async def previous_track(ctx):
    if session.step_back():
        await start_track_stream(ctx)
    else:
        await ctx.send("⏮️ Already sitting at the first track of the queue.")

@bot.command(name="shuffle")
async def shuffle_queue(ctx):
    if not session.queue:
        session.queue = drive_manager.list_audio_files(FOLDER_ID)
    if not session.queue:
        return await ctx.send("Cannot shuffle an empty track queue directory.")
        
    shuf_state = session.toggle_shuffle()
    if shuf_state:
        await ctx.send("🔀 **Shuffle Mode Activated.** Tracks will play in randomized order sequence.")
    else:
        await ctx.send("🔁 **Shuffle Mode Deactivated.** Returning back to original folder listing sequence.")

@bot.command(name="seek")
async def seek_track(ctx, seconds: int):
    if not ctx.voice_client or (not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused()):
        return await ctx.send("There is no active stream to seek timestamps on.")
    
    await start_track_stream(ctx, seek_time=seconds)

web_app = Quart(__name__)

@web_app.route('/')
async def home():
    return "Bot is alive and running!"

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
