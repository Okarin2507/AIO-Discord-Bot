# cogs/blackjack_cog.py
import discord
from discord.ext import commands
from discord import app_commands, ui, ButtonStyle
import random
import asyncio

# --- LOGIC CƠ BẢN CỦA TRÒ CHƠI BLACKJACK ---

# Định nghĩa các lá bài và giá trị
SUITS = {"♥️": "Hearts", "♦️": "Diamonds", "♣️": "Clubs", "♠️": "Spades"}
RANKS = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11}

class Card:
    """Đại diện cho một lá bài."""
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
        self.value = RANKS[rank]

    def __str__(self):
        return f"{self.rank}{self.suit}"

class Deck:
    """Đại diện cho một bộ bài."""
    def __init__(self):
        self.cards = [Card(s, r) for s in SUITS for r in RANKS]
        self.shuffle()

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self):
        if not self.cards:
            # Nếu hết bài, tạo và xáo trộn một bộ bài mới
            self.cards = [Card(s, r) for s in SUITS for r in RANKS]
            self.shuffle()
        return self.cards.pop()

class BlackjackGame:
    """Quản lý trạng thái của một ván Blackjack."""
    def __init__(self):
        self.deck = Deck()
        self.player_hand = []
        self.dealer_hand = []
        self.player_score = 0
        self.dealer_score = 0
        self.status = "playing" # playing, player_win, dealer_win, push

    def calculate_score(self, hand):
        """Tính điểm cho một bộ bài, xử lý quân Át (A) một cách linh hoạt."""
        score = sum(card.value for card in hand)
        num_aces = sum(1 for card in hand if card.rank == 'A')
        while score > 21 and num_aces:
            score -= 10
            num_aces -= 1
        return score

    def start_game(self):
        """Bắt đầu ván bài: chia 2 lá cho mỗi bên."""
        self.player_hand = [self.deck.deal(), self.deck.deal()]
        self.dealer_hand = [self.deck.deal(), self.deck.deal()]
        self.update_scores()

    def update_scores(self):
        self.player_score = self.calculate_score(self.player_hand)
        self.dealer_score = self.calculate_score(self.dealer_hand)

    def player_hit(self):
        """Người chơi rút thêm một lá."""
        self.player_hand.append(self.deck.deal())
        self.update_scores()
        if self.player_score > 21:
            self.status = "dealer_win" # Người chơi thua (bust)
            return False # Trả về False nếu thua
        return True # Trả về True nếu vẫn trong cuộc

    def dealer_play(self):
        """Logic của nhà cái: rút bài cho đến khi đạt 17 điểm trở lên."""
        while self.dealer_score < 17:
            self.dealer_hand.append(self.deck.deal())
            self.update_scores()

        # Xác định người thắng cuộc
        if self.dealer_score > 21 or self.player_score > self.dealer_score:
            self.status = "player_win"
        elif self.dealer_score > self.player_score:
            self.status = "dealer_win"
        else:
            self.status = "push" # Hòa

# --- GIAO DIỆN TƯƠNG TÁC TRÊN DISCORD ---

class BlackjackView(ui.View):
    """Giao diện chứa các nút Hit và Stand."""
    def __init__(self, game_cog, game, original_interaction):
        super().__init__(timeout=180) # View sẽ hết hạn sau 180 giây
        self.game_cog = game_cog
        self.game = game
        self.original_interaction = original_interaction

    async def on_timeout(self):
        """Xử lý khi view hết hạn (người dùng không tương tác)."""
        for item in self.children:
            item.disabled = True
        embed = self.original_interaction.message.embeds[0]
        embed.set_footer(text="⌛ Hết thời gian! Ván bài đã kết thúc.")
        await self.original_interaction.edit_original_response(embed=embed, view=self)
        self.game_cog.end_game(self.original_interaction.user.id)

    def create_embed(self, game_over=False):
        """Tạo và cập nhật embed hiển thị trạng thái ván bài."""
        player_hand_str = " ".join(str(card) for card in self.game.player_hand)
        
        if not game_over:
            dealer_hand_str = f"{str(self.game.dealer_hand[0])} ❔"
            dealer_score_str = self.game.dealer_hand[0].value
            color = discord.Color.gold()
            title = "♦️ Ván bài Blackjack của bạn ♠️"
        else:
            dealer_hand_str = " ".join(str(card) for card in self.game.dealer_hand)
            dealer_score_str = self.game.dealer_score
            if self.game.status == "player_win":
                title = "🎉 Bạn đã thắng Ado rồi :3! 🎉"
                color = discord.Color.green()
            elif self.game.status == "dealer_win":
                title = "💔 Bạn đã thua! 💔"
                color = discord.Color.red()
            else: # Push
                title = "🤝 Hòa! 🤝"
                color = discord.Color.light_grey()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name=f"🃏 Tay bài của bạn ({self.game.player_score})", value=player_hand_str, inline=False)
        embed.add_field(name=f"🤖 Tay bài của nhà cái ({dealer_score_str})", value=dealer_hand_str, inline=False)
        return embed

    @ui.button(label="Hit (Rút)", style=ButtonStyle.primary, emoji="➕")
    async def hit_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user != self.original_interaction.user:
            await interaction.response.send_message("Đây không phải là ván bài của bạn!", ephemeral=True)
            return

        if not self.game.player_hit(): # Nếu người chơi thua (bust)
            # Dừng ván bài và hiển thị kết quả
            for item in self.children:
                item.disabled = True
            embed = self.create_embed(game_over=True)
            await interaction.response.edit_message(embed=embed, view=self)
            self.game_cog.end_game(interaction.user.id)
        else:
            # Cập nhật embed với lá bài mới
            embed = self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Stand (Dừng)", style=ButtonStyle.secondary, emoji="🛑")
    async def stand_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user != self.original_interaction.user:
            await interaction.response.send_message("Đây không phải là ván bài của bạn!", ephemeral=True)
            return

        # Vô hiệu hóa các nút
        for item in self.children:
            item.disabled = True
        
        # Nhà cái chơi
        self.game.dealer_play()
        
        # Cập nhật embed với kết quả cuối cùng
        embed = self.create_embed(game_over=True)
        await interaction.response.edit_message(embed=embed, view=self)
        self.game_cog.end_game(interaction.user.id)


# --- LỚP COG CHÍNH ---

class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_games = {} # Lưu các ván bài đang diễn ra theo user_id

    def end_game(self, user_id: int):
        """Xóa ván bài khỏi danh sách đang hoạt động."""
        if user_id in self.active_games:
            del self.active_games[user_id]

    blackjack = app_commands.Group(name="blackjack", description="Chơi một ván Blackjack với bot.")

    @blackjack.command(name="start", description="Bắt đầu một ván Blackjack mới.")
    async def start_blackjack(self, interaction: discord.Interaction):
        if interaction.user.id in self.active_games:
            await interaction.response.send_message("Bạn đã có một ván bài đang diễn ra rồi!", ephemeral=True)
            return

        await interaction.response.defer() # Đợi một chút để xử lý

        # Khởi tạo game
        game = BlackjackGame()
        game.start_game()
        self.active_games[interaction.user.id] = game
        
        # Tạo giao diện và embed ban đầu
        view = BlackjackView(self, game, interaction)
        
        # Kiểm tra Blackjack ngay từ đầu
        if game.player_score == 21:
            game.status = "player_win"
            for item in view.children:
                item.disabled = True
            self.end_game(interaction.user.id)

        embed = view.create_embed(game_over=(game.status != "playing"))
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(BlackjackCog(bot))