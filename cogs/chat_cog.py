# cogs/chat_cog.py
import discord
from discord.ext import commands
from discord import app_commands, ui, ButtonStyle
import os
import aiohttp
import sqlite3
import asyncio
import re
import tempfile
from elevenlabs.client import ElevenLabs
from elevenlabs import Voice, VoiceSettings

# --- CÀI ĐẶT BIẾN TOÀN CỤC ---
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
DB_FILE = 'memory.db'
MEMORY_LIMIT = 33
ELEVENLABS_VOICE_ID = "Pt5YrLNyu6d2s3s4CVMg"
PERSONA_FOLDER = 'templates'
DEFAULT_PERSONA = 'kurisu_makise'

if ELEVENLABS_API_KEY:
    eleven_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
else:
    eleven_client = None
    print("CẢNH BÁO: ELEVENLABS_API_KEY chưa được thiết lập. Tính năng voice sẽ không hoạt động.")

# --- CÁC HÀM QUẢN LÝ DATABASE ---
def setup_database():
    """Khởi tạo hoặc cập nhật các bảng cần thiết trong database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Bảng lưu trữ lịch sử trò chuyện theo user và persona
    cursor.execute('''CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        persona_name TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )''')
    # Bảng lưu trữ persona đang hoạt động cho mỗi kênh
    cursor.execute('''CREATE TABLE IF NOT EXISTS channel_personalities (
                        channel_id INTEGER PRIMARY KEY,
                        persona_name TEXT NOT NULL
                    )''')
    conn.commit()
    conn.close()

def prune_history(user_id, persona_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM conversations WHERE user_id = ? AND persona_name = ? ORDER BY timestamp DESC LIMIT 1 OFFSET ?", (user_id, persona_name, MEMORY_LIMIT))
    limit_id = cursor.fetchone()
    if limit_id:
        cursor.execute("DELETE FROM conversations WHERE user_id = ? AND persona_name = ? AND id <= ?", (user_id, persona_name, limit_id[0]))
    conn.commit()
    conn.close()

def add_to_history(user_id, persona_name, role, content):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO conversations (user_id, persona_name, role, content) VALUES (?, ?, ?, ?)", (user_id, persona_name, role, content))
    conn.commit()
    conn.close()
    prune_history(user_id, persona_name)

def get_history(user_id, persona_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM conversations WHERE user_id = ? AND persona_name = ? ORDER BY timestamp DESC LIMIT 10", (user_id, persona_name))
    history = [{"role": row[0], "content": row[1]} for row in cursor.fetchall()][::-1]
    conn.close()
    return history

def delete_user_persona_history(user_id, persona_name):
    """Xóa toàn bộ lịch sử trò chuyện của một user với một persona cụ thể."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conversations WHERE user_id = ? AND persona_name = ?", (user_id, persona_name))
    conn.commit()
    deleted_rows = cursor.rowcount
    conn.close()
    return deleted_rows

def set_channel_persona(channel_id, persona_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO channel_personalities (channel_id, persona_name) VALUES (?, ?)", (channel_id, persona_name))
    conn.commit()
    conn.close()

def get_channel_persona(channel_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT persona_name FROM channel_personalities WHERE channel_id = ?", (channel_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else DEFAULT_PERSONA

# --- CÁC HÀM QUẢN LÝ FILE PERSONA (GIỮ NGUYÊN) ---
def load_persona(persona_name: str):
    safe_persona_name = os.path.basename(persona_name)
    filepath = os.path.join(PERSONA_FOLDER, f"{safe_persona_name}.txt")
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return f.read()
    except FileNotFoundError: return None

def save_persona(persona_name: str, content: str):
    if not os.path.exists(PERSONA_FOLDER): os.makedirs(PERSONA_FOLDER)
    safe_persona_name = os.path.basename(persona_name)
    filepath = os.path.join(PERSONA_FOLDER, f"{safe_persona_name}.txt")
    with open(filepath, 'w', encoding='utf-8') as f: f.write(content)

def delete_persona_file(persona_name: str):
    safe_persona_name = os.path.basename(persona_name)
    filepath = os.path.join(PERSONA_FOLDER, f"{safe_persona_name}.txt")
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False

def list_personas():
    if not os.path.exists(PERSONA_FOLDER): return []
    return [f.replace('.txt', '') for f in os.listdir(PERSONA_FOLDER) if f.endswith('.txt')]

# --- MODAL ĐỂ THÊM/SỬA PERSONA (GIỮ NGUYÊN) ---
class PersonaModal(ui.Modal, title='Persona Editor'):
    def __init__(self, persona_name: str, current_content: str = ""):
        super().__init__()
        self.persona_name = persona_name
        self.content = ui.TextInput(
            label=f"Nội dung cho '{persona_name}'", style=discord.TextStyle.paragraph,
            default=current_content, max_length=4000
        )
        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction):
        await self.bot.loop.run_in_executor(None, save_persona, self.persona_name, self.content.value)
        await interaction.response.send_message(f"✅ Đã lưu thành công persona **{self.persona_name}**.", ephemeral=True)

# --- LỚP COG CHO CHATBOT ---
class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        setup_database()
        if not os.path.exists(PERSONA_FOLDER): os.makedirs(PERSONA_FOLDER)

    async def ask_ai(self, user_id: int, channel_id: int):
        active_persona_name = await self.bot.loop.run_in_executor(None, get_channel_persona, channel_id)
        personality_prompt = await self.bot.loop.run_in_executor(None, load_persona, active_persona_name)
        
        if not personality_prompt:
            personality_prompt = await self.bot.loop.run_in_executor(None, load_persona, DEFAULT_PERSONA) or "You are a helpful assistant."

        history = await self.bot.loop.run_in_executor(None, get_history, user_id, active_persona_name)

        api_url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
        messages = [{"role": "system", "content": personality_prompt}] + history
        payload = {"model": "deepseek/deepseek-chat-v3.1", "messages": messages}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(api_url, headers=headers, json=payload, timeout=60) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['choices'][0]['message']['content']
                    else: return f"API Error: {await response.text()}"
            except Exception as e: return f"An error occurred: {e}"

    def play_stream(self, voice_client: discord.VoiceClient, text: str):
        # (Giữ nguyên logic của hàm play_stream)
        try:
            if not eleven_client: return
            text_plain = re.sub(r'\*[^*]+\*', '', text)
            text_plain = re.sub(r'([_~`])', '', text_plain)
            text_plain = re.sub(r'[^\w\s\.,!?\-\'"():;]', '', text_plain, flags=re.UNICODE)
            text_plain = ' '.join(text_plain.split()).strip()
            if not text_plain: return
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
                temp_path = temp_file.name
                audio_stream = eleven_client.text_to_speech.stream(voice_id=ELEVENLABS_VOICE_ID, text=text_plain, model_id="eleven_flash_v2_5")
                for chunk in audio_stream:
                    if chunk: temp_file.write(chunk)
            source = discord.FFmpegPCMAudio(temp_path)
            def after_play(error):
                if error: print(f'Player error: {error}')
                try: os.remove(temp_path)
                except: pass
            voice_client.play(source, after=after_play)
        except Exception as e: print(f"Lỗi khi stream TTS: {e}")

    @commands.hybrid_command(name="chat", description="Trò chuyện với trợ lý AI.")
    async def chat(self, ctx: commands.Context, message: str, voice: bool = False):
        await ctx.defer()
        user_id = ctx.author.id
        channel_id = ctx.channel.id
        active_persona = await self.bot.loop.run_in_executor(None, get_channel_persona, channel_id)

        await self.bot.loop.run_in_executor(None, add_to_history, user_id, active_persona, "user", message)
        ai_response = await self.ask_ai(user_id, channel_id)
        await self.bot.loop.run_in_executor(None, add_to_history, user_id, active_persona, "assistant", ai_response)
        
        await ctx.send(ai_response)
        
        if voice:
            # (Giữ nguyên logic voice)
            if not eleven_client: return await ctx.channel.send("*Lỗi: Tính năng giọng nói chưa được cấu hình.*", delete_after=10)
            if not ctx.author.voice: return await ctx.channel.send("*Lưu ý: Bạn không ở trong kênh thoại.*", delete_after=10)
            voice_client = ctx.guild.voice_client
            if not voice_client: voice_client = await ctx.author.voice.channel.connect()
            elif voice_client.channel != ctx.author.voice.channel: await voice_client.move_to(ctx.author.voice.channel)
            if voice_client.is_playing(): voice_client.stop()
            await self.bot.loop.run_in_executor(None, self.play_stream, voice_client, ai_response)

    # --- CÁC LỆNH QUẢN LÝ PERSONA ---
    persona_group = app_commands.Group(name="persona", description="Quản lý các personality của AI")

    @persona_group.command(name="view", description="Xem danh sách các personality có sẵn.")
    async def view_persona(self, interaction: discord.Interaction):
        # (Giữ nguyên logic)
        all_personas = await self.bot.loop.run_in_executor(None, list_personas)
        active_persona = await self.bot.loop.run_in_executor(None, get_channel_persona, interaction.channel.id)
        if not all_personas: return await interaction.response.send_message("Không tìm thấy persona nào.", ephemeral=True)
        embed = discord.Embed(title="🎭 Danh sách Persona", color=discord.Color.gold())
        description = "".join([f"➡️ **{p}** (đang dùng)\n" if p == active_persona else f"• {p}\n" for p in all_personas])
        embed.description = description
        embed.set_footer(text="Dùng /persona switch để đổi.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @persona_group.command(name="switch", description="Chuyển đổi personality và xóa lịch sử cũ của bạn với persona đó.")
    @app_commands.describe(name="Tên của persona bạn muốn chuyển sang.")
    async def switch_persona(self, interaction: discord.Interaction, name: str):
        all_personas = await self.bot.loop.run_in_executor(None, list_personas)
        if name not in all_personas:
            return await interaction.response.send_message(f"❌ Persona **{name}** không tồn tại.", ephemeral=True)
        
        # Lấy persona cũ và xóa lịch sử liên quan của user
        user_id = interaction.user.id
        channel_id = interaction.channel.id
        old_persona = await self.bot.loop.run_in_executor(None, get_channel_persona, channel_id)
        
        # Chỉ xóa nếu persona cũ khác persona mới
        if old_persona != name:
            await self.bot.loop.run_in_executor(None, delete_user_persona_history, user_id, old_persona)

        # Đặt persona mới cho kênh
        await self.bot.loop.run_in_executor(None, set_channel_persona, channel_id, name)
        await interaction.response.send_message(f"✅ Đã chuyển sang persona **{name}**. Lịch sử trò chuyện của bạn với **{old_persona}** đã được xóa.", ephemeral=True)

    @persona_group.command(name="delete_memory", description="Xóa lịch sử trò chuyện của bạn với persona hiện tại.")
    async def delete_memory(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        channel_id = interaction.channel.id
        active_persona = await self.bot.loop.run_in_executor(None, get_channel_persona, channel_id)

        deleted_count = await self.bot.loop.run_in_executor(None, delete_user_persona_history, user_id, active_persona)
        
        if deleted_count > 0:
            await interaction.response.send_message(f"🗑️ Đã xóa **{deleted_count}** tin nhắn trong cuộc trò chuyện của bạn với **{active_persona}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"👍 Không có lịch sử nào để xóa cho bạn với persona **{active_persona}**.", ephemeral=True)

    @persona_group.command(name="add", description="Thêm một personality mới.")
    # (Giữ nguyên logic)
    async def add_persona(self, interaction: discord.Interaction, name: str):
        modal = PersonaModal(persona_name=name); modal.bot = self.bot
        await interaction.response.send_modal(modal)

    @persona_group.command(name="edit", description="Chỉnh sửa một personality đã có.")
    # (Giữ nguyên logic)
    async def edit_persona(self, interaction: discord.Interaction, name: str):
        content = await self.bot.loop.run_in_executor(None, load_persona, name)
        if content is None: return await interaction.response.send_message(f"❌ Persona **{name}** không tồn tại.", ephemeral=True)
        modal = PersonaModal(persona_name=name, current_content=content); modal.bot = self.bot
        await interaction.response.send_modal(modal)

    @persona_group.command(name="delete", description="Xóa một file personality.")
    # (GiÃữ nguyên logic)
    async def delete_persona_file(self, interaction: discord.Interaction, name: str):
        if name == DEFAULT_PERSONA: return await interaction.response.send_message(f"❌ Không thể xóa persona mặc định (`{DEFAULT_PERSONA}`).", ephemeral=True)
        was_deleted = await self.bot.loop.run_in_executor(None, delete_persona_file, name)
        if was_deleted: await interaction.response.send_message(f"🗑️ Đã xóa thành công file persona **{name}**.", ephemeral=True)
        else: await interaction.response.send_message(f"❌ File persona **{name}** không tồn tại.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot))