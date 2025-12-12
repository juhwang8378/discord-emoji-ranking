import discord
import os
from discord.ext import commands

# 1. SETUP CLASS (The modern way to load extensions)
class MyBot(commands.Bot):
    async def setup_hook(self):
        # This ensures the library loads before the bot logs in
        await self.load_extension("discord_emoji_ranking")
        print("Extension loaded successfully.")

# 2. DEFINE INTENTS
intents = discord.Intents.default()
intents.message_content = True  # Required to see emojis in messages

# 3. INITIALIZE BOT
# We use the custom class created above
bot = MyBot(command_prefix="/", intents=intents)

# 4. RUN
# Ensure the variable name matches what is in PM2!
token = os.environ.get("DISCORD_TOKEN") 

if not token:
    # Fallback check in case the name is different
    token = os.environ.get("DISCORD_BOT_TOKEN")

if token:
    bot.run(token)
else:
    print("Error: No token found. Make sure DISCORD_TOKEN is set in PM2.")