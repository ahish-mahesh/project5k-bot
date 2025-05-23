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
from llama_cpp import Llama
from discord import app_commands  # For slash commands and autocomplete

# Set your local model path here (TinyLlama, optimized for Apple Silicon)
MODEL_PATH = "./tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"

# Initialize the model only once (use caching if needed)
llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=2048,             # Default context size
    n_threads=4,            # Tune based on your Mac's CPU cores
    use_mlock=True,         # Pin model in memory (optional, improves performance)
    # Enable Metal backend for Apple Silicon (M1/M2/M3) for maximum performance
    # llama.cpp will auto-detect Metal if built with Metal support
    # If you want to force Metal: set 'backend' if your llama-cpp-python version supports it
    backend="metal"  # Uncomment if your llama-cpp-python supports this argument
)

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


def get_motivation(user_log_minutes: int) -> str:
    
    prompt = f"""<s>[INST] You are a friendly, supportive fitness coach.
    The user just completed a workout of {user_log_minutes} minutes.
    Give them a short, energetic motivational message. [/INST]"""

    response = llm(
        prompt,
        max_tokens=100,
        stop=["</s>"]
    )
    
    # Extract the model’s reply from the structured response
    return response["choices"][0]["text"].strip() # type: ignore

# --- Autocomplete helpers ---
COMMON_MINUTES = [15, 20, 30, 45, 60, 90, 120]
EXAMPLE_PROMPTS = [
    "Give me a workout tip",
    "How do I stay motivated?",
    "Suggest a 30-minute workout",
    "What's a good post-workout meal?",
    "How do I recover from muscle soreness?"
]

async def get_minutes_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest common workout durations for /log command autocomplete."""
    return [
        app_commands.Choice(name=f"{m} minutes", value=m)
        for m in COMMON_MINUTES if current in str(m)
    ][:5]

async def get_prompt_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest example prompts for /ask command autocomplete."""
    return [
        app_commands.Choice(name=prompt, value=prompt)
        for prompt in EXAMPLE_PROMPTS if current.lower() in prompt.lower()
    ][:5]

# --- Slash command definitions ---

# Remove old @bot.command() versions to avoid duplicate commands

@bot.tree.command(name="log", description="Log your workout time in minutes.")
@app_commands.describe(minutes="Number of minutes you worked out today.")
@app_commands.autocomplete(minutes=get_minutes_autocomplete)
async def log(interaction: discord.Interaction, minutes: int):
    """
    Slash command to log workout minutes for the current user.
    Provides autocomplete for common durations.
    Uses deferred response to avoid Discord timeout.
    Echoes the user's request in the bot's response for chat visibility.
    """
    await interaction.response.defer()  # Defer response to prevent timeout
    uid = str(interaction.user.id)
    today = datetime.date.today().isoformat()
    entry = {today: minutes}
    db.collection("logs").document(uid).set(entry, merge=True)
    motivation = get_motivation(minutes)
    await interaction.followup.send(
        f"{interaction.user.mention} logged `/log {minutes}`\n\n✅ *{minutes} min* for today!\n{motivation}"
    )

@bot.tree.command(name="ask", description="Ask the LLM a question or for motivation.")
@app_commands.describe(prompt="Your question or prompt for the LLM.")
@app_commands.autocomplete(prompt=get_prompt_autocomplete)
async def ask(interaction: discord.Interaction, prompt: str):
    """
    Slash command to ask the local LLM a question or for motivation.
    Provides autocomplete for example prompts.
    Uses deferred response to avoid Discord timeout.
    Echoes the user's request in the bot's response for chat visibility.
    """
    await interaction.response.defer()  # Defer response to prevent timeout
    llm_prompt = f"<s>[INST] You are a friendly, supportive fitness coach. {prompt} [/INST]"
    response = llm(
        llm_prompt,
        max_tokens=200,
        stop=["</s>"]
    )
    reply = response["choices"][0]["text"].strip()  # type: ignore
    await interaction.followup.send(
        f"**{interaction.user.mention} asked:** `{prompt}`\n💡 {reply}"
    )

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

# --- IMPORTANT: Sync slash commands on startup ---
@bot.event
async def on_ready():
    """
    Event handler for when the bot is ready. Syncs slash commands with Discord.
    """
    await bot.tree.sync()
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
