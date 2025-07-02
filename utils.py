import os
import datetime
import pickle
import re
import requests
import asyncio
import firebase_admin
from firebase_admin import credentials, firestore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import discord
from discord.ext import commands
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.generativeai as genai

genai_api_key = os.getenv("GEMINI_API_KEY")
USE_GEMINI = bool(genai_api_key)
if USE_GEMINI:
    genai.configure(api_key=genai_api_key) # type: ignore

# Firebase and Firestore initialization
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Scheduler initialization (for streaks)
scheduler = AsyncIOScheduler()

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GOOGLE_CREDENTIALS_FILE = "./google_api_credentials.json"

async def get_calendar_service(user_id: str, interaction=None):
    creds = None
    token_file = f"token_{user_id}.pickle"
    if os.path.exists(token_file):
        with open(token_file, "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        from google.oauth2.credentials import Credentials
        import json
        device_auth_url = "https://oauth2.googleapis.com/device/code"
        token_url = "https://oauth2.googleapis.com/token"
        with open(GOOGLE_CREDENTIALS_FILE, "r") as f:
            client_info = json.load(f)["installed"]
        client_id = client_info["client_id"]
        client_secret = client_info["client_secret"]
        data = {
            "client_id": client_id,
            "scope": " ".join(SCOPES)
        }
        r = requests.post(device_auth_url, data=data)
        resp = r.json()
        if "verification_url" not in resp:
            raise Exception(f"Google OAuth device flow error: {resp}")
        verification_url = resp["verification_url"]
        user_code = resp["user_code"]
        device_code = resp["device_code"]
        expires_in = resp["expires_in"]
        interval = resp.get("interval", 5)
        if interaction:
            await interaction.user.send(
                f"🔗 **Google Calendar Authentication Required**\n\n"
                f"To connect your Google Calendar, please:\n"
                f"1. Go to: {verification_url}\n"
                f"2. Enter this code: **{user_code}**\n\n"
                f"⏰ This code expires in {expires_in//60} minutes.\n"
                f"Once you complete authentication, your workout plan will be added to your calendar automatically."
            )
        else:
            print(f"Go to {verification_url} and enter code: {user_code}")
        start_time = asyncio.get_event_loop().time()
        while True:
            await asyncio.sleep(interval)
            data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            }
            token_resp = requests.post(token_url, data=data).json()
            if "access_token" in token_resp:
                creds = Credentials(
                    token=token_resp["access_token"],
                    refresh_token=token_resp.get("refresh_token"),
                    token_uri=token_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=SCOPES
                )
                with open(token_file, "wb") as token:
                    pickle.dump(creds, token)
                break
            elif token_resp.get("error") == "authorization_pending":
                if asyncio.get_event_loop().time() - start_time > expires_in:
                    raise Exception("Device code expired. Please try again.")
                continue
            else:
                raise Exception(f"Google OAuth error: {token_resp}")
    service = build("calendar", "v3", credentials=creds)
    return service

def parse_workout_plan(plan_text: str):
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    events = []
    day_pattern = r"(" + "|".join(days) + "):(.*?)(?=(?:" + "|".join(days) + "):(?!\\S)|$)"
    matches = re.findall(day_pattern, plan_text, re.DOTALL)
    for day, content in matches:
        workout = content.strip()
        events.append((day, workout))
    return events

def sanitize_llm_output(text: str, max_length: int = 2000) -> str:
    """
    Sanitize Gemini output for Discord:
    - Truncate to Discord's 2000 char limit
    - Remove accidental code blocks (triple backticks)
    - Replace blank/empty output with fallback error
    - Strip leading/trailing whitespace
    """
    if not text or not isinstance(text, str) or not text.strip():
        return "[LLM ERROR] Sorry, the response was empty. Please try again."
    sanitized = text.strip()
    # Remove leading/trailing triple backticks
    if sanitized.startswith("```") and sanitized.endswith("```"):
        sanitized = sanitized[3:-3].strip()
    # Remove any lone triple backticks inside
    sanitized = sanitized.replace("```", "\u200b")
    # Truncate to Discord's max message length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length-3] + "..."
    if not sanitized:
        return "[LLM ERROR] Sorry, the response was empty. Please try again."
    return sanitized

def gemini_generate(prompt, max_tokens=512):
    """Call Gemini API synchronously using gemini-2.0-flash (latest official API)."""
    try:
        model = genai.GenerativeModel('gemini-2.0-flash') # type: ignore
        response = model.generate_content(prompt, generation_config={"max_output_tokens": max_tokens})
        print(f"[GEMINI] Generated response for prompt: {prompt[:50]}... (max_tokens={max_tokens})")
        if not response or not response.text:
            print("[GEMINI] No response text generated.")
            return "[LLM ERROR] Sorry, there was a problem generating a response. Please try again later."
        return sanitize_llm_output(response.text)
    except Exception as e:
        error_log_path = "logs/utils_llm_error.log"
        import traceback
        with open(error_log_path, "a") as f:
            f.write(f"\n[ERROR] {datetime.datetime.now()}\n")
            f.write(f"Prompt: {prompt}\n")
            f.write(f"Exception: {e}\n")
            f.write(traceback.format_exc())
            f.write("\n---\n")
        print(f"[GEMINI ERROR] Exception occurred. Details written to {error_log_path}")
        return "[LLM ERROR] Sorry, there was a problem generating a response. Please try again later."

async def call_gemini_async(prompt, max_tokens=512):
    """
    Asynchronously call Gemini API in a thread to avoid blocking the event loop.
    Uses asyncio.to_thread for robust async execution.
    """
    return await asyncio.to_thread(gemini_generate, prompt, max_tokens)

def get_llm_response(prompt, max_tokens=2000, stop=None):
    """
    Helper to get LLM response using Gemini API only.
    """
    import traceback
    if not USE_GEMINI:
        return "[LLM ERROR] Gemini API key not set. Please set GEMINI_API_KEY in your environment."
    return gemini_generate(prompt, max_tokens=max_tokens)

# Update get_motivation to use get_llm_response

def get_motivation(user_log_minutes: int) -> str:
    prompt = f"""<s>[INST] You are a friendly, supportive fitness coach.\nThe user just completed a workout of {user_log_minutes} minutes.\nGive them a short, energetic motivational message. [/INST]"""
    return get_llm_response(prompt)

# Streak checking logic
async def check_streaks(bot):
    docs = db.collection("logs").stream()
    today = datetime.date.today()
    for doc in docs:
        data = doc.to_dict()
        streak = 0
        for i in range(7):
            day = (today - datetime.timedelta(days=i)).isoformat()
            if day in data:
                streak += 1
            else:
                break
        if streak >= 3:
            user_id = doc.id
            user = await bot.fetch_user(int(user_id))
            if user:
                try:
                    await user.send(f"🔥 You're on a {streak}-day streak! Keep going!")
                except:
                    print(f"Could not DM user {user_id}")
