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
from discord import app_commands  # For slash commands and autocomplete
import pickle  # For saving user tokens
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import re  # For regex operations
from google_auth_oauthlib.helpers import session_from_client_secrets_file
import requests
from llama_log_redirect import llama_log_redirect
from utils import (
    get_calendar_service,
    parse_workout_plan,
    get_motivation,
    check_streaks,
    db,
    scheduler,
    get_llm_response,
    gemini_generate,
    call_gemini_async
)
import concurrent.futures

# Load environment variables from .env file (e.g., DISCORD_BOT_TOKEN)
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Initialize Firebase using service account credentials
cred = credentials.Certificate("serviceAccountKey.json")
# firebase_admin.initialize_app(cred)
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

# --- Autocomplete helpers ---
# Common workout durations and example prompts for autocomplete in discord slash commands
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
    print(f"executing /log with {interaction.user}: {minutes} minutes")
    if minutes <= 0:
        await interaction.followup.send(
            f"{interaction.user.mention} ❌ Please log a positive number of minutes."
        )
        return
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
    Slash command to ask the LLM a question or for motivation.
    Provides autocomplete for example prompts.
    Uses deferred response to avoid Discord timeout.
    Echoes the user's request in the bot's response for chat visibility.
    """
    await interaction.response.defer()  # Defer response to prevent timeout
    print(f"executing /ask with {interaction.user}: {prompt}")
    prompt = f"<s>[INST] You are a friendly, supportive fitness coach. {prompt} [/INST]"
    import traceback
    try:
        with llama_log_redirect("logs/project5k_bot_llm.log"):
            output = await call_gemini_async(
                prompt,
                max_tokens=768,  # Slightly higher for plan
            )
        response = output
    except Exception as e:
        error_log_path = "logs/project5k_bot_llm_error.log"
        with open(error_log_path, "a") as f:
            f.write(f"\n[ERROR] {datetime.datetime.now()}\n")
            f.write(f"Prompt: {prompt}\n")
            f.write(f"Exception: {e}\n")
            f.write(traceback.format_exc())
            f.write("\n---\n")
        print(f"[LLM ERROR] Exception occurred in /ask. Details written to {error_log_path}")
        response = "[LLM ERROR] Sorry, there was a problem generating a response. Please try again later."
    await interaction.followup.send(
        f"**{interaction.user.mention} asked:** `{prompt}`\n💡 {response}"
    )

# Global in-memory store for pending plans (user_id -> {plan_text, timestamp})
pending_plans = {}

@bot.tree.command(name="plan", description="Create a weekly workout plan and add it to your Google Calendar.")
@app_commands.describe(goal="Describe your workout goal or type (e.g. 'strength', 'cardio', 'yoga', '5k run', 'full body', etc.)")
@app_commands.autocomplete(goal=plan_goal_autocomplete)
async def plan(interaction: discord.Interaction, goal: str):
    """
    Generates a weekly workout plan using the LLM based on the user's goal and, upon user confirmation, adds it to the user's Google Calendar.
    Prompts the user to authenticate with Google if needed.
    """
    await interaction.response.defer()
    print(f"executing /plan with {interaction.user}: {goal}")
    prompt = (
        f"Create a 7-day workout plan for the goal: {goal}. "
        "List only the days and the workout for each day. "
        "Format exactly as: Monday: ...\\nTuesday: ...\\nWednesday: ...\\nThursday: ...\\nFriday: ...\\nSaturday: ...\\nSunday: ... "
        "No introduction, no summary, just the plan."
    )
    prompt = f"<s>[INST] You are a friendly, supportive fitness coach. {prompt} [/INST]"
    import traceback
    try:
        with llama_log_redirect("logs/project5k_bot_llm.log"):
            output = await call_gemini_async(
                prompt,
                max_tokens=768,  # Slightly higher for plan
            )
        response = output
    except Exception as e:
        error_log_path = "logs/project5k_bot_llm_error.log"
        with open(error_log_path, "a") as f:
            f.write(f"\n[ERROR] {datetime.datetime.now()}\n")
            f.write(f"Prompt: {prompt}\n")
            f.write(f"Exception: {e}\n")
            f.write(traceback.format_exc())
            f.write("\n---\n")
        print(f"[LLM ERROR] Exception occurred in /plan. Details written to {error_log_path}")
        response = "[LLM ERROR] Sorry, there was a problem generating a response. Please try again later."
    print("Response from LLM: ", response)
    monday_idx = response.find("Monday:")
    if monday_idx != -1:
        plan_text = response[monday_idx:]
    else:
        plan_text = response
    await interaction.followup.send(
        f"Here is your weekly workout plan for **{goal}**:\n```\n{response}\n```\n\nIf you want to add this plan to your Google Calendar, reply with `/confirmplan` in the next 2 minutes.\n\n**Example prompts for /plan:**\n- strength training\n- yoga\n- 5k run\n- full body\n- weight loss\n- flexibility\n- HIIT\n- upper body\n- lower body\n- muscle gain\n- cardio"
    )
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
    print(f"executing /confirmplan with {interaction.user}")
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
        # Call the async get_calendar_service function directly
        service = await get_calendar_service(str(user_id), interaction)
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
        await interaction.followup.send(f"⚠️ Could not add to Google Calendar: {e}\nIf this is your first time, check your Discord DMs for a Google login link.")
    finally:
        if user_id in pending_plans:
            del pending_plans[user_id]

# --- IMPORTANT: Sync slash commands on startup ---
@bot.event
async def on_ready():
    """
    Event handler for when the bot is ready. Syncs slash commands with Discord.
    """
    await bot.tree.sync()
    print(f'✅ Bot is online as {bot.user}')

# Method to prompt a user a question via DM after their first login and wait for their response using the Discord API.
async def dm_user(user: discord.User | discord.Member, bot: commands.Bot, question: str, timeout: int = 120) -> str | None:
    """
    Prompts the user with a question via DM after their first login and waits for a response.
    Returns the user's response as a string, or None if they do not respond in time.
    """
    dm_channel = None
    try:
        dm_channel = await user.create_dm()
        await dm_channel.send(question)

        def check(m):
            return m.author == user and m.channel == dm_channel

        response_msg = await bot.wait_for('message', check=check, timeout=timeout)
        return response_msg.content
    except asyncio.TimeoutError:
        if dm_channel:
            await dm_channel.send("⏰ You did not respond in time. If you want to answer later, just message me again!")
        return None
    except Exception as e:
        print(f"Error prompting user {user.id}: {e}")
        return None

# Method to ask a set of predefined questions to the user via DM
# after their first login and store the responses in Firestore.
async def get_to_know_user(member: discord.User | discord.Member, bot: commands.Bot):
    questions = [
        "What is your current fitness level? (e.g. beginner, intermediate, advanced)",
        "Do you have any specific health conditions or injuries I should know about?",
        "What are your main fitness goals? (e.g. weight loss, muscle gain, endurance, flexibility)",
        "How many days a week do you currently work out?",
        "What types of workouts do you enjoy? (e.g. cardio, strength training, yoga, etc.)",
        "Do you have access to a gym or prefer home workouts?",
        "How long do you typically work out each session? (in minutes)",
        "What motivates you to stay active and healthy?"
    ]
    for question in questions:
        response = await dm_user(member, bot, question)
        if response is None:
            # If the user does not respond, you can choose to skip or log this
            await member.send("⏰ You did not respond in time. If you want to answer later, just message me again with /introduceyourself!")
            return
        # add the response to the user's profile in Firestore
        db.collection("profiles").document(str(member.id)).set(
            {question: response}, merge=True
        )
        # Send a confirmation message back to the user
        await member.send(f"Got it! Your answer: '{response}'\n\nNext question...") 
        
        
    # After all questions, thank the user and store their goal
    await member.send("Thanks for sharing all that! I'll use this info to help you stay on track and reach your goals. 💪")

# --- Automated LLM-driven onboarding Q&A loop ---

async def llm_onboarding_loop(member: discord.User | discord.Member, bot: commands.Bot):
    """
    Automated onboarding: LLM generates each question based on conversation so far, stops when LLM replies 'DONE', then drafts a plan.
    Stores Q&A in Firestore under 'profiles'.
    """
    conversation = []  # List of (Q, A) tuples
    user_id = str(member.id)
    max_rounds = 10  # Safety: prevent infinite loops
    for round_num in range(max_rounds):
        # Build LLM prompt with conversation so far
        if conversation:
            history = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(conversation)])
            onboarding_prompt = (
                f"[INST] You are onboarding a fitness client. Here is the conversation so far:\n"
                f"{history}\n"
                "If you have enough information to draft a personalized weekly workout plan, reply with ONLY 'DONE'. "
                "Otherwise, reply with the next best question to ask. [/INST]"
            )
        else:
            history = ""    
            onboarding_prompt = (
                f"[INST] You are onboarding a fitness client. I want you to ask them questions to gather information about their fitness journey.\n"
                "If you have enough information to draft a personalized weekly workout plan, reply with ONLY 'DONE'. "
                "Otherwise, reply with the next best question to ask. [/INST]"
            )
        # Get next question or 'DONE' from LLM
        try:
            with llama_log_redirect("logs/project5k_bot_llm.log"):
                llm_response = await call_gemini_async(onboarding_prompt, max_tokens=128) # type: ignore
            # Handle LLM response format (dict with 'choices' list)
            if isinstance(llm_response, dict) and "choices" in llm_response:
                next_q = llm_response
            else:
                next_q = str(llm_response).strip()
        except Exception as e:
            await member.send("[LLM ERROR] Sorry, there was a problem generating the next question. Please try again later.")
            return
        if next_q.strip().upper() == "DONE":
            break
        # Ask user the next question via DM
        answer = await dm_user(member, bot, next_q)
        if answer is None:
            await member.send("⏰ You did not respond in time. If you want to answer later, just message me again with /introduceyourself!")
            return
        # Store Q&A
        conversation.append((next_q, answer))
        db.collection("profiles").document(user_id).set({next_q: answer}, merge=True)
        await member.send(f"Got it! Your answer: '{answer}'\n\nNext question...")
    # After loop, draft the plan
    plan_history = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(conversation)])
    plan_prompt = (
        f"[INST] You are a fitness coach. Here is the onboarding conversation with a new client:\n"
        f"{plan_history}\n"
        "Based on this, draft a safe, personalized 7-day workout plan. "
        "List only the days and the workout for each day. "
        "Format exactly as: Monday: ...\\nTuesday: ...\\nWednesday: ...\\nThursday: ...\\nFriday: ...\\nSaturday: ...\\nSunday: ... "
        "No introduction, no summary, just the plan. [/INST]"
    )
    try:
        with llama_log_redirect("logs/project5k_bot_llm.log"):
            plan_response = await call_gemini_async(plan_prompt, max_tokens=768)
        plan_text = plan_response.strip()
    except Exception as e:
        await member.send("[LLM ERROR] Sorry, there was a problem generating your workout plan. Please try again later.")
        return
    monday_idx = plan_text.find("Monday:")
    if monday_idx != -1:
        plan_text = plan_text[monday_idx:]
    await member.send(f"Here is your weekly workout plan!\n```\n{plan_text}\n```")
    # Optionally, store the plan in Firestore
    db.collection("profiles").document(user_id).set({"workout_plan": plan_text}, merge=True)

# --- Refactor onboarding event to be async and use LLM onboarding loop ---

@bot.event
async def on_member_join(member):
    print(f"New member joined: {member.name} ({member.id})")
    user_doc_ref = db.collection("logs").document(str(member.id))
    user_doc = user_doc_ref.get()
    if not hasattr(user_doc, 'exists') or not user_doc.exists:
        question = ("Hey 👋 — great to meet you! I’m your AI accountability partner. "
                    "Before we dive in, is it okay if I ask a few quick questions about your health, workout history, and goals (will take 2 mins) so I can build a safe, personalized plan?")
        response = await dm_user(member, bot, question)
        if response and response.lower() in ("yes", "sure", "ok", "okay", "y"):
            await llm_onboarding_loop(member, bot)
        else:
            pass  # Optionally log or handle users who don't respond

@bot.tree.command(name="introduceyourself", description="Answer a few questions to help me understand your fitness journey.")
async def introduce_yourself(interaction: discord.Interaction):
    """
    Slash command to start the LLM-driven onboarding Q&A if the user missed it on join.
    """
    await interaction.response.defer()
    print(f"executing /introduceyourself with {interaction.user}")
    user_doc_ref = db.collection("logs").document(str(interaction.user.id))
    user_doc = user_doc_ref.get()
    if not hasattr(user_doc, 'exists') or not user_doc.exists:
        await llm_onboarding_loop(interaction.user, bot)
        await interaction.followup.send("Thanks for answering! I'll use this info to help you stay on track and reach your goals. 💪")
    else:
        await interaction.followup.send("You have already answered these questions. If you want to update your profile, please contact support.")

# Main async function to start the scheduler and bot
async def main():
    scheduler.add_job(lambda: check_streaks(bot), 'cron', hour=7)
    scheduler.start()

    # Start the Discord bot
    await bot.start(TOKEN)  # type: ignore

# Entry point: run the main() coroutine using asyncio
if __name__ == "__main__":
    asyncio.run(main())
