# cogs/music_cog.py
import discord
from discord.ext import commands
import os
import yt_dlp
import asyncio
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import random
import json
import re
import threading
from collections import deque

# --- CÀI ĐẶT BIẾN TOÀN CỤC CHO MUSIC ---
SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')

spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET))
FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn -loglevel quiet'}
CACHE_FILE = 'cache.json'

# --- LOGIC CACHE ---
def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_cache(cache_data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f: json.dump(cache_data, f, indent=4, ensure_ascii=False)

song_cache = load_cache()
cache_lock = threading.Lock()

# --- CÁC HÀM YOUTUBE ---
def search_youtube(query):
    with cache_lock:
        if query in song_cache:
            print(f"Cache HIT for query: {query}")
            return song_cache[query]

    print(f"Cache MISS. Searching YouTube for: {query}")
    YDL_SEARCH_OPTS = {'format': 'bestaudio/best', 'quiet': True, 'extract_flat': 'generic', 'noplaylist': True, 'source_address': '0.0.0.0', 'cookiefile': 'cookies.txt'}
    try:
        with yt_dlp.YoutubeDL(YDL_SEARCH_OPTS) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]
            
            # --- THAY ĐỔI LOGIC (GIỐNG SUISEI-BOT) ---
            # Ưu tiên 'webpage_url' (link vĩnh viễn) và không lưu 'stream_url'
            result = {'url': info.get('webpage_url', info.get('url')), 'title': info.get('title', 'Untitled')}
            
            with cache_lock:
                song_cache[query] = result
                save_cache(song_cache)
            return result
    except Exception as e:
        print(f"YouTube search failed for '{query}': {e}")
    return None

def get_stream_data(youtube_url):
    YDL_STREAM_OPTS = {'format': 'bestaudio/best', 'quiet': True, 'source_address': '0.0.0.0', 'cookiefile': 'cookies.txt'}
    try:
        with yt_dlp.YoutubeDL(YDL_STREAM_OPTS) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            # Trả về link stream tạm thời ('source')
            return {'source': info['url'], 'title': info.get('title', 'Untitled')}
    except Exception as e:
        print(f"Failed to get stream for '{youtube_url}': {e}")
    return None

# --- LỚP COG CHO MUSIC ---
class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.music_queues = {}
        self.loop_states = {}

    # --- ĐÃ XÓA HÀM prefetch_next_song ---
    # Logic tải trước đã bị loại bỏ để tránh lưu link stream hết hạn.

    # --- HÀM play_next_song VIẾT LẠI (GIỐNG SUISEI-BOT) ---
    async def play_next_song(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        
        if not voice_client: return

        # Dừng nếu bot đang chạy (tránh lỗi)
        if voice_client.is_playing():
            voice_client.stop()

        queue = self.music_queues.get(guild_id)
        if not queue:
            # Logic rảnh rỗi và tự ngắt kết nối
            await asyncio.sleep(120)
            if voice_client.is_connected() and not voice_client.is_playing():
                await voice_client.disconnect()
            return
        
        current_song = queue[0]
        
        # --- THAY ĐỔI CHÍNH ---
        # Không kiểm tra 'stream_url' nữa.
        # LUÔN LUÔN lấy link stream mới ngay trước khi phát.
        
        # current_song['url'] lúc này là link vĩnh viễn (webpage_url)
        stream_data = await self.bot.loop.run_in_executor(None, get_stream_data, current_song['url'])
        
        if not stream_data:
            await interaction.channel.send(f"❌ Lỗi khi lấy stream cho **{current_song['title']}**. Bỏ qua.")
            self.music_queues[guild_id].popleft()
            # Đệ quy để thử phát bài tiếp theo
            self.bot.loop.create_task(self.play_next_song(interaction))
            return
        
        # Sử dụng stream_data['source'] (link stream tạm thời) trực tiếp
        source = discord.FFmpegPCMAudio(stream_data['source'], **FFMPEG_OPTIONS)

        def after_playing(error):
            if error: print(f'Player error: {error}')
            
            q = self.music_queues.get(guild_id)
            if not q: return

            loop_state = self.loop_states.get(guild_id)
            
            # Logic lặp bài
            if loop_state == 'queue':
                played_song = q.popleft()
                q.append(played_song)
            elif loop_state != 'song':
                q.popleft() # Chỉ xóa khỏi hàng đợi nếu không lặp bài hát
            
            # Không cần reset 'stream_url' vì nó không còn tồn tại
            
            # Gọi tác vụ để phát bài tiếp theo
            self.bot.loop.create_task(self.play_next_song(interaction))

        voice_client.play(source, after=after_playing)
        await interaction.channel.send(f"▶️ Đang phát: **{current_song['title']}**")
        
        # Đã xóa dòng gọi hàm prefetch_next_song

    # CẢI TIẾN: XỬ LÝ PLAYLIST SONG SONG
    async def process_playlist_concurrently(self, ctx: commands.Context, track_queries: list):
        guild_id = ctx.guild.id
        
        async def search_task(query):
            return await self.bot.loop.run_in_executor(None, search_youtube, query)

        tasks = [search_task(query) for query in track_queries]
        results = await asyncio.gather(*tasks)
        
        added_songs = [song for song in results if song]
        if added_songs:
            self.music_queues[guild_id].extend(added_songs)
            await ctx.send(f"✅ Đã xử lý xong playlist và thêm được **{len(added_songs)}** bài hát nữa vào hàng đợi.")

    @commands.hybrid_command(name="play", description="Phát nhạc hoặc playlist từ YouTube/Spotify.")
    async def play(self, ctx: commands.Context, *, query: str):
        await ctx.defer(ephemeral=True)
        guild_id = ctx.guild.id

        if not ctx.author.voice:
            return await ctx.send("Bạn phải ở trong một kênh thoại để dùng lệnh này!")

        voice_client = ctx.guild.voice_client
        if not voice_client:
            voice_client = await ctx.author.voice.channel.connect()
        elif voice_client.channel != ctx.author.voice.channel:
            await voice_client.move_to(ctx.author.voice.channel)

        if guild_id not in self.music_queues:
            self.music_queues[guild_id] = deque() # Sử dụng deque để tối ưu pop(0)
        
        spotify_url_pattern = re.compile(r'https://open\.spotify\.com/(playlist|album|artist|track)/([a-zA-Z0-9]+)')
        match = spotify_url_pattern.match(query)

        if match:
            spotify_type, spotify_id = match.groups()
            await ctx.channel.send(f"🔎 Nhận diện {spotify_type} từ Spotify. Bắt đầu xử lý...")

            try:
                def fetch_spotify_data():
                    if spotify_type == 'playlist': return spotify.playlist_tracks(spotify_id).get('items', [])
                    if spotify_type == 'album': return spotify.album_tracks(spotify_id).get('items', [])
                    if spotify_type == 'artist': return spotify.artist_top_tracks(spotify_id).get('tracks', [])
                    if spotify_type == 'track': return [spotify.track(spotify_id)]
                    return None
                
                results = await self.bot.loop.run_in_executor(None, fetch_spotify_data)
                if not results: return await ctx.channel.send(f"❌ Không tìm thấy bài hát nào cho {spotify_type} này.")

                track_queries = []
                for item in results:
                    track_data = item.get('track', item)
                    if track_data and track_data.get('name') and track_data.get('artists'):
                        track_queries.append(f"{track_data['name']} {track_data['artists'][0]['name']}")
                
                if not track_queries: return await ctx.channel.send("❌ Không thể trích xuất thông tin bài hát hợp lệ.")

                first_track_query = track_queries.pop(0)
                first_song_info = await self.bot.loop.run_in_executor(None, search_youtube, first_track_query)

                if first_song_info:
                    self.music_queues[guild_id].append(first_song_info)
                    await ctx.channel.send(f"☑️ Đã thêm bài hát đầu tiên: **{first_song_info['title']}**. Phần còn lại sẽ được xử lý trong nền...")
                    
                    if not voice_client.is_playing():
                        self.bot.loop.create_task(self.play_next_song(ctx))
                    
                    if track_queries:
                        self.bot.loop.create_task(self.process_playlist_concurrently(ctx, track_queries))
                else:
                    await ctx.channel.send(f"❌ Không tìm thấy bài hát đầu tiên '{first_track_query}'.")

            except Exception as e:
                await ctx.channel.send(f"❌ Lỗi khi xử lý link Spotify: `{e}`")
        
        else:
            search_result = await self.bot.loop.run_in_executor(None, search_youtube, query)
            if search_result:
                self.music_queues[guild_id].append(search_result)
                await ctx.channel.send(f"👍 Đã thêm vào hàng đợi: **{search_result['title']}**")
                if not voice_client.is_playing():
                    self.bot.loop.create_task(self.play_next_song(ctx))
            else:
                await ctx.channel.send("❌ Không tìm thấy bài hát nào với truy vấn đó.")

    # --- CÁC LỆNH KHÁC GIỮ NGUYÊN ---
    @commands.hybrid_command(name="help", description="Hiển thị danh sách các lệnh.")
    async def help_command(self, ctx: commands.Context):
        embed = discord.Embed(title="🎧 Music Bot Commands", color=discord.Color.blue())
        embed.add_field(name="`/chat [message]`", value="Trò chuyện với trợ lý AI.", inline=False)
        embed.add_field(name="`/play [query]`", value="Phát nhạc hoặc thêm playlist từ YouTube/Spotify.", inline=False)
        embed.add_field(name="`/skip`", value="Bỏ qua bài hát hiện tại.", inline=False)
        embed.add_field(name="`/queue`", value="Hiển thị hàng đợi bài hát.", inline=False)
        embed.add_field(name="`/shuffle`", value="Xáo trộn hàng đợi.", inline=False)
        embed.add_field(name="`/loop`", value="Chuyển chế độ lặp (Tắt -> Hàng đợi -> Bài hát).", inline=False)
        embed.add_field(name="`/stop`", value="Dừng phát nhạc và xóa hàng đợi.", inline=False)
        embed.add_field(name="`/leave`", value="Ngắt kết nối bot khỏi kênh thoại.", inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="skip", description="Bỏ qua bài hát hiện tại.")
    async def skip(self, ctx: commands.Context):
        if ctx.guild.voice_client and ctx.guild.voice_client.is_playing():
            ctx.guild.voice_client.stop()
            await ctx.send("⏭️ Đã bỏ qua bài hát.")
        else:
            await ctx.send("Không có gì đang phát.", ephemeral=True)

    @commands.hybrid_command(name="queue", description="Hiển thị hàng đợi bài hát.")
    async def queue(self, ctx: commands.Context):
        queue = self.music_queues.get(ctx.guild.id)
        if not queue: return await ctx.send("Hàng đợi đang trống.")
        
        embed = discord.Embed(title="📜 Hàng Đợi Bài Hát", color=discord.Color.purple())
        loop_mode = self.loop_states.get(ctx.guild.id, None)
        status = "Tắt"
        if loop_mode == 'queue': status = "Hàng đợi 🔁"
        elif loop_mode == 'song': status = "Bài hát 🔂"
        embed.set_author(name=f"Chế độ lặp: {status}")

        queue_list = "\n".join([f"`{i+1}.` {song['title']}" for i, song in enumerate(list(queue)[:10])])
        embed.description = queue_list
        if len(queue) > 10:
            embed.set_footer(text=f"và {len(queue) - 10} bài hát khác...")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="shuffle", description="Xáo trộn hàng đợi.")
    async def shuffle(self, ctx: commands.Context):
        queue = self.music_queues.get(ctx.guild.id)
        if queue and len(queue) > 1:
            now_playing = queue.popleft()
            temp_list = list(queue)
            random.shuffle(temp_list)
            self.music_queues[ctx.guild.id] = deque(temp_list)
            self.music_queues[ctx.guild.id].appendleft(now_playing)
            await ctx.send("🔀 Hàng đợi đã được xáo trộn!")
        else:
            await ctx.send("Không có đủ bài hát để xáo trộn.", ephemeral=True)

    @commands.hybrid_command(name="loop", description="Chuyển chế độ lặp (Tắt -> Hàng đợi -> Bài hát).")
    async def loop(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        current_state = self.loop_states.get(guild_id)
        if current_state is None:
            self.loop_states[guild_id] = 'queue'
            await ctx.send("🔁 Chế độ lặp được đặt thành **Hàng đợi**.")
        elif current_state == 'queue':
            self.loop_states[guild_id] = 'song'
            await ctx.send("🔂 Chế độ lặp được đặt thành **Bài hát**.")
        else:
            self.loop_states[guild_id] = None
            await ctx.send("🔁 Chế độ lặp đã **Tắt**.")

    @commands.hybrid_command(name="stop", description="Dừng phát nhạc và xóa hàng đợi.")
    async def stop(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        if ctx.guild.voice_client: ctx.guild.voice_client.stop()
        if guild_id in self.music_queues: self.music_queues[guild_id].clear()
        self.loop_states[guild_id] = None
        await ctx.send("⏹️ Đã dừng phát nhạc và xóa hàng đợi.")

    @commands.hybrid_command(name="leave", description="Ngắt kết nối bot khỏi kênh thoại.")
    async def leave(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        if ctx.guild.voice_client:
            if guild_id in self.music_queues: self.music_queues[guild_id].clear()
            self.loop_states[guild_id] = None
            await ctx.guild.voice_client.disconnect()
            await ctx.send("👋 Tạm biệt!")
        else:
            await ctx.send("Tôi không có trong kênh thoại nào.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))