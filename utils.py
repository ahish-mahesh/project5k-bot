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
from llama_cpp import Llama
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from llama_log_redirect import llama_log_redirect

# Set your local model path here (Phi-3 Mini, optimized for Apple Silicon or CPU)
MODEL_PATH = "./phi-2.Q4_K_M.gguf"

# Initialize the model only once (use caching if needed)
llm = None
try:
    with llama_log_redirect("logs/utils_llm.log"):
        llm = Llama(
            model_path=MODEL_PATH,
            n_ctx=1024,  # Balanced context size for Phi-3 Mini
            n_threads=os.cpu_count() or 8,
            use_mlock=True,
            backend="cpu"  # Use "cpu" if you have issues with Metal
        )
except Exception as e:
    import traceback
    error_log_path = "logs/utils_llm_error.log"
    with open(error_log_path, "a") as f:
        f.write(f"\n[ERROR] {datetime.datetime.now()}\n")
        f.write(f"Exception: {e}\n")
        f.write(traceback.format_exc())
        f.write("\n---\n")
    print(f"[LLM ERROR] Exception occurred during model load. Details written to {error_log_path}")
    llm = None

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GOOGLE_CREDENTIALS_FILE = "./google_api_credentials.json"

# Firebase and Firestore initialization
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Scheduler initialization (for streaks)
scheduler = AsyncIOScheduler()

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

def get_llm_response(prompt, max_tokens=2000, stop=None):
    """
    Helper to get LLM response and handle both streaming and non-streaming outputs.
    Adds extra logging to catch silent errors.
    """
    import traceback
    if llm is None:
        error_log_path = "logs/utils_llm_error.log"
        with open(error_log_path, "a") as f:
            f.write(f"\n[ERROR] {datetime.datetime.now()}\n")
            f.write(f"Prompt: {prompt}\n")
            f.write(f"Exception: LLM model is not loaded.\n")
            f.write("\n---\n")
        print(f"[LLM ERROR] LLM model is not loaded. Details written to {error_log_path}")
        return "[LLM ERROR] Sorry, the language model is not available. Please try again later."
    try:
        with llama_log_redirect("logs/utils_llm.log"):
            response = llm(
                prompt,
                max_tokens=max_tokens,
                stop=stop or ["</s>"]
            )
        # If response is a generator/iterator, get the first item
        if hasattr(response, '__iter__') and not isinstance(response, dict):
            response = next(iter(response))
        return response["choices"][0]["text"].strip() # type: ignore
    except Exception as e:
        error_log_path = "logs/utils_llm_error.log"
        with open(error_log_path, "a") as f:
            f.write(f"\n[ERROR] {datetime.datetime.now()}\n")
            f.write(f"Prompt: {prompt}\n")
            f.write(f"Exception: {e}\n")
            f.write(traceback.format_exc())
            f.write("\n---\n")
        print(f"[LLM ERROR] Exception occurred. Details written to {error_log_path}")
        return "[LLM ERROR] Sorry, there was a problem generating a response. Please try again later."

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
