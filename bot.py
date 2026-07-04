import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from drive_utils import GoogleDriveManager

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
FOLDER_ID = os.getenv('DRIVE_FOLDER_ID')

drive = GoogleDriveManager('service_account.json')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

music_queues = {}
TEMP_DIR = './temp'
os.makedirs(TEMP_DIR, exist_ok=True)

def check_queue(ctx):
    guild_id = ctx.guild.id
    last_played = os.path.join(TEMP_DIR, f"{guild_id}_current.mp3")
    if os.path.exists(last_played):
        try:
            os.remove(last_played)
        except Exception as e:
            print(f"Error cleaning up temp file: {e}")

    if guild_id in music_queues and music_queues[guild_id]:
        next_track = music_queues[guild_id].pop(0)
        local_path = os.path.join(TEMP_DIR, f"{guild_id}_current.mp3")
        coro = drive_next_and_play(ctx, next_track, local_path)
        asyncio.run_coroutine_threadable(bot.loop, coro)
    else:
        asyncio.run_coroutine_threadable(bot.loop, ctx.send("Queue empty. Standing by!"))

async def drive_next_and_play(ctx, track, local_path):
    voice_client = ctx.voice_client
    if not voice_client:
        return

    await ctx.send(f"📥 Loading **{track['name']}** from Google Drive...")
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, drive.download_file, track['id'], local_path)
    
    if not success:
        await ctx.send("❌ Failed to download file from Google Drive.")
        check_queue(ctx)
        return

    audio_source = discord.FFmpegPCMAudio(local_path)
    voice_client.play(audio_source, after=lambda e: check_queue(ctx))
    await ctx.send(f"🎶 Now playing: **{track['name']}**")

@bot.event
async def on_ready():
    print(f'✅ Bot is online and logged in as {bot.user}')

@bot.command(name='join')
async def join(ctx):
    if not ctx.author.voice:
        return await ctx.send("❌ You must be in a voice channel for me to join!")
    channel = ctx.author.voice.channel
    if ctx.voice_client is not None:
        return await ctx.voice_client.move_to(channel)
    await channel.connect()
    await ctx.send(f"Connected to **{channel.name}**")

@bot.command(name='list')
async def list_files(ctx):
    await ctx.send("🔍 Scanning Google Drive folder...")
    files = drive.list_audio_files(FOLDER_ID)
    if not files:
        return await ctx.send("📭 No supported audio files found in that folder.")
    msg = "**Available Tracks:**\n"
    for idx, f in enumerate(files, start=1):
        msg += f"`{idx}.` {f['name']}\n"
        if len(msg) > 1800:
            await ctx.send(msg)
            msg = ""
    if msg:
        await ctx.send(msg)

@bot.command(name='play')
async def play(ctx, *, track_search: str = None):
    if track_search is None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            return await ctx.send("▶️ Resumed track.")
        return await ctx.send("Please specify a song title or number. Example: `!play 1`")

    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("❌ You need to be in a voice channel first.")

    files = drive.list_audio_files(FOLDER_ID)
    selected_track = None

    if track_search.isdigit():
        idx = int(track_search) - 1
        if 0 <= idx < len(files):
            selected_track = files[idx]
    else:
        for f in files:
            if track_search.lower() in f['name'].lower():
                selected_track = f
                break

    if not selected_track:
        return await ctx.send("❌ Track not found. Use `!list` to verify files.")

    guild_id = ctx.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        music_queues[guild_id].append(selected_track)
        await ctx.send(f"⏳ Added **{selected_track['name']}** to the queue at position #{len(music_queues[guild_id])}")
    else:
        local_path = os.path.join(TEMP_DIR, f"{guild_id}_current.mp3")
        await drive_next_and_play(ctx, selected_track, local_path)

@bot.command(name='skip')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped current track.")
    else:
        await ctx.send("❌ Nothing is currently playing.")

@bot.command(name='pause')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused track.")

@bot.command(name='queue')
async def show_queue(ctx):
    guild_id = ctx.guild.id
    if guild_id not in music_queues or not music_queues[guild_id]:
        return await ctx.send("📂 The queue is currently empty.")
    msg = "**Upcoming Queue:**\n"
    for idx, track in enumerate(music_queues[guild_id], start=1):
        msg += f"`{idx}.` {track['name']}\n"
    await ctx.send(msg)

@bot.command(name='leave')
async def leave(ctx):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        if guild_id in music_queues:
            music_queues[guild_id] = []
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Disconnected.")

bot.run(TOKEN)
