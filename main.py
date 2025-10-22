# main.py
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio

# --- CẢI TIẾN: TỰ ĐỘNG XÓA FILE CACHE KHI KHỞI ĐỘNG ---
# Điều này đảm bảo bot luôn nhận được một token xác thực mới từ Spotify.
CACHE_FILE_PATH = '.cache'
if os.path.exists(CACHE_FILE_PATH):
    try:
        os.remove(CACHE_FILE_PATH)
        print(f"Đã xóa file cache cũ của Spotify: {CACHE_FILE_PATH}")
    except OSError as e:
        print(f"Lỗi khi xóa file cache {CACHE_FILE_PATH}: {e}")
# -------------------------------------------------------------

# Tải các biến môi trường từ file .env
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Cài đặt quyền (Intents) mà bot của bạn cần
intents = discord.Intents.default()
intents.message_content = True

# Khởi tạo bot, đồng thời tắt lệnh help mặc định để tránh xung đột
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Danh sách các "cogs" (module logic) mà bot sẽ tải
initial_extensions = [
    'cogs.chat_cog',
    'cogs.music_cog',
    'cogs.blackjack_cog'
]

# Sự kiện này được kích hoạt khi bot đã kết nối thành công với Discord
@bot.event
async def on_ready():
    # Đồng bộ hóa các lệnh slash (/) với Discord để chúng xuất hiện
    await bot.tree.sync()
    print(f'Đã đăng nhập với tên {bot.user}')
    print(f'Đã đồng bộ hóa {len(await bot.tree.fetch_commands())} lệnh ứng dụng.')
    print('------')

# Hàm để tải các cogs
async def load_cogs():
    for extension in initial_extensions:
        try:
            await bot.load_extension(extension)
            print(f'Tải thành công extension: {extension}')
        except Exception as e:
            print(f'Lỗi khi tải extension {extension}: {e}')

# Hàm chính để chạy bot
async def main():
    if not DISCORD_TOKEN:
        print("Lỗi: DISCORD_TOKEN chưa được thiết lập. Vui lòng kiểm tra file .env.")
        return
        
    async with bot:
        await load_cogs()
        await bot.start(DISCORD_TOKEN)

# Chạy hàm main nếu file này được thực thi trực tiếp
if __name__ == "__main__":
    asyncio.run(main())