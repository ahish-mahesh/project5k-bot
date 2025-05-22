# Import necessary libraries
import discord  # Discord API wrapper
import os  # For environment variable access
import datetime  # For date handling
import asyncio  # For async event loop
from discord.ext import commands  # Discord bot commands extension
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # Scheduler for periodic jobs
from dotenv import load_dotenv  # For loading environment variables from .env
import firebase_admin  # Firebase SDK
from firebase_admin import credentials, firestore  # For auth and database access

# Load environment variables from .env file (e.g., DISCORD_BOT_TOKEN)
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Initialize Firebase using service account credentials
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()  # Firestore database client

# Set up Discord bot with required permissions (specifically to read message content)
intents = discord.Intents.default()
intents.message_content = True  # Needed for message content access
bot = commands.Bot(command_prefix='/', intents=intents)  # Define bot with '/' as command prefix

# Initialize scheduler to run background tasks (like daily streak checks)
scheduler = AsyncIOScheduler()

# Define a command `/log <minutes>` that users can call to log workout time
@bot.command()
async def log(ctx, minutes: int):
    uid = str(ctx.author.id)  # Use Discord user ID as key
    today = datetime.date.today().isoformat()  # Get today’s date in YYYY-MM-DD format
    entry = {today: minutes}  # Create log entry for today

    # Merge the new log entry into the user's document in Firestore
    db.collection("logs").document(uid).set(entry, merge=True)

    # Acknowledge logging to the user
    await ctx.send(f"✅ {ctx.author.mention}, logged *{minutes} min* for today!")

# Function to check all users' streaks and send DMs if they’re on a streak
async def check_streaks():
    docs = db.collection("logs").stream()  # Get all user log documents
    today = datetime.date.today()

    for doc in docs:
        data = doc.to_dict()
        streak = 0

        # Check the last 7 days to calculate a streak
        for i in range(7):
            day = (today - datetime.timedelta(days=i)).isoformat()
            if day in data:
                streak += 1
            else:
                break  # Streak is broken

        # If streak is 3 or more, send a motivational DM
        if streak >= 3:
            user_id = doc.id
            user = await bot.fetch_user(int(user_id))
            if user:
                try:
                    await user.send(f"🔥 You're on a {streak}-day streak! Keep going!")
                except:
                    print(f"Could not DM user {user_id}")  # Handle users with DMs disabled

# Event that triggers when the bot has connected to Discords
@bot.event
async def on_ready():
    print(f'✅ Bot is online as {bot.user}')

# Main async function to start the scheduler and bot
async def main():
    # Schedule the check_streaks task to run every day at 7 AM
    scheduler.add_job(check_streaks, 'cron', hour=7)
    scheduler.start()

    # Start the Discord bot
    await bot.start(TOKEN)  # type: ignore

# Entry point: run the main() coroutine using asyncio
if __name__ == "__main__":
    asyncio.run(main())
