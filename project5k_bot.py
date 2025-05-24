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
import pickle  # For saving user tokens
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# Set your local model path here (Phi-2, optimized for Apple Silicon)
MODEL_PATH = "./phi-2.Q4_K_M.gguf"

# Initialize the model only once (use caching if needed)
llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=2048,             # Default context size (increase if you want longer context)
    n_threads=os.cpu_count() or 8,  # Use all available CPU cores for max performance
    use_mlock=True,         # Pin model in memory (optional, improves performance)
    backend="metal"        # Use Metal backend for Apple Silicon (best for M1/M2/M3)
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

# Google Calendar API setup
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GOOGLE_CREDENTIALS_FILE = "./google_api_credentials.json"  # Your OAuth2 credentials file

# Helper: Authenticate user and get Google Calendar service
def get_calendar_service(user_id: str):
    """
    Authenticate the user and return a Google Calendar service object.
    Each user gets their own token file (token_{user_id}.pickle).
    """
    creds = None
    token_file = f"token_{user_id}.pickle"
    if os.path.exists(token_file):
        with open(token_file, "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "wb") as token:
            pickle.dump(creds, token)
    service = build("calendar", "v3", credentials=creds)
    return service

# Helper: Parse LLM workout plan into events (robust version)
def parse_workout_plan(plan_text: str):
    """
    Parse a workout plan from the LLM response into a structured list of (day, workout) tuples.
    This version extracts the string between each day label (e.g., 'Monday:') and the next day label (e.g., 'Tuesday:').
    
    Args:
        plan_text (str): Raw text response from the LLM containing the workout plan
        
    Returns:
        list: A list of tuples (day, workout description) for calendar integration
    """
    # Remove anything before the first 'Monday:'
    monday_idx = plan_text.find("Monday:")
    if monday_idx != -1:
        plan_text = plan_text[monday_idx:]

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    events = []
    import re
    # Build a regex pattern to match each day and its content up to the next day or end of string
    day_pattern = r"(" + "|".join(days) + "):(.*?)(?=(?:" + "|".join(days) + "):(?!\\S)|$)"
    matches = re.findall(day_pattern, plan_text, re.DOTALL)
    for day, content in matches:
        workout = content.strip()
        events.append((day, workout))
    return events

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

# --- Helper: Generate a motivational message using the LLM

def get_motivation(user_log_minutes: int) -> str:
    prompt = f"""<s>[INST] You are a friendly, supportive fitness coach.\nThe user just completed a workout of {user_log_minutes} minutes.\nGive them a short, energetic motivational message. [/INST]"""
    response = llm(
        prompt,
        max_tokens=100,
        stop=["</s>"]
    )
    return response["choices"][0]["text"].strip()  # type: ignore

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

# Global in-memory store for pending plans (user_id -> {plan_text, timestamp})
pending_plans = {}

# Helper for /plan autocomplete
async def plan_goal_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest example workout goals for /plan command autocomplete."""
    EXAMPLES = [
        "strength training",
        "cardio",
        "yoga",
        "5k run",
        "HIIT",
        "upper body",
        "lower body",
        "full body",
        "weight loss",
        "muscle gain",
        "flexibility"
    ]
    return [
        app_commands.Choice(name=ex, value=ex)
        for ex in EXAMPLES if current.lower() in ex.lower()
    ][:5]



@bot.tree.command(name="plan", description="Create a weekly workout plan and add it to your Google Calendar.")
@app_commands.describe(goal="Describe your workout goal or type (e.g. 'strength', 'cardio', 'yoga', '5k run', 'full body', etc.)")
@app_commands.autocomplete(goal=plan_goal_autocomplete)
async def plan(interaction: discord.Interaction, goal: str):
    """
    Generates a weekly workout plan using the LLM based on the user's goal and, upon user confirmation, adds it to the user's Google Calendar.
    Prompts the user to authenticate with Google if needed.
    """
    await interaction.response.defer()
    # 1. Generate plan with LLM
    prompt = (
        f"Create a 7-day workout plan for the goal: {goal}. "
        "List only the days and the workout for each day. "
        "Format exactly as: Monday: ...\\nTuesday: ...\\nWednesday: ...\\nThursday: ...\\nFriday: ...\\nSaturday: ...\\nSunday: ... "
        "No introduction, no summary, just the plan."
    )
    prompt = f"<s>[INST] You are a friendly, supportive fitness coach. {prompt} [/INST]"
    output = llm(
        prompt,
        max_tokens=20000,  # Adjust based on your needs
        top_p=0.95,       # Nucleus sampling
        stop=["<s>"]     # Define stop sequences if needed
    )
    
    response = output["choices"][0]["text"] # type: ignore
    
    print("Response from LLM: ", response)
    
    # Clean up the response by removing the "Response: " prefix if present
    if response.startswith(" Response: "):
        response = response[len(" Response: "):]
    
    await interaction.followup.send(
        f"Here is your weekly workout plan for **{goal}**:\n```\n{response}\n```\n\nIf you want to add this plan to your Google Calendar, reply with `/confirmplan` in the next 2 minutes.\n\n**Example prompts for /plan:**\n- strength training\n- yoga\n- 5k run\n- full body\n- weight loss\n- flexibility\n- HIIT\n- upper body\n- lower body\n- muscle gain\n- cardio"
    )
    # Store the plan temporarily for this user (in-memory, simple dict)
    pending_plans[interaction.user.id] = {
        'plan_text': response,
        'timestamp': datetime.datetime.utcnow()
    }

@bot.tree.command(name="confirmplan", description="Confirm and add your last generated workout plan to Google Calendar.")
async def confirmplan(interaction: discord.Interaction):
    """
    Adds the last generated workout plan to the user's Google Calendar if confirmed within 2 minutes.
    """
    await interaction.response.defer()
    user_id = interaction.user.id
    # Check for pending plan
    pending = pending_plans.get(user_id)
    if not pending:
        await interaction.followup.send("❌ No recent workout plan found to confirm. Please use `/plan` first.")
        return
    # Check if confirmation is within 2 minutes
    now = datetime.datetime.utcnow()
    if (now - pending['timestamp']).total_seconds() > 120:
        del pending_plans[user_id]
        await interaction.followup.send("❌ Your workout plan confirmation timed out. Please use `/plan` again.")
        return
    plan_text = pending['plan_text']
    try:
        loop = asyncio.get_event_loop()
        service = await loop.run_in_executor(None, get_calendar_service, str(user_id))
        events = parse_workout_plan(plan_text)
        today = datetime.date.today()
        weekday_map = {day: i for i, day in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
        for day, desc in events:
            if day in weekday_map:
                days_ahead = (weekday_map[day] - today.weekday() + 7) % 7
                event_date = today + datetime.timedelta(days=days_ahead)
                event = {
                    'summary': f'Workout: {desc}',
                    'start': {'date': event_date.isoformat()},
                    'end': {'date': event_date.isoformat()},
                }
                service.events().insert(calendarId='primary', body=event).execute()
        await interaction.followup.send("✅ Added your workout plan to your Google Calendar!")
    except Exception as e:
        await interaction.followup.send(f"⚠️ Could not add to Google Calendar: {e}\nIf this is your first time, check your browser for a Google login window.")
    finally:
        del pending_plans[user_id]

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
