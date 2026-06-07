"""
StarFilm - YouTube Shorts Maker
================================
Vercel + Supabase + Rendi FFmpeg API
Topic → Script → Voice → Images → Rendi Video → Done!
"""

import os
import re
import time
import json
import uuid
import hashlib
import asyncio
import secrets
import smtplib
import requests
import tempfile
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from supabase import create_client, Client
from fastapi.responses import StreamingResponse
import re
from fastapi.responses import FileResponse
import wave
import subprocess

load_dotenv()

import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# SSL Fix
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['CURL_CA_BUNDLE'] = ''

# ─── Supabase Setup ────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── Rendi Setup ───────────────────────────────────────────────
RENDI_API_KEY = os.getenv("RENDI_API_KEY")
RENDI_BASE_URL = "https://api.rendi.dev/v1"

# ─── Image API Keys (add to your .env file) ─────────────────────
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY")   # optional

app = FastAPI(title="StarFilm")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Directories ───────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TMP_DIR    = Path("/tmp/starfilm")

STATIC_DIR.mkdir(exist_ok=True, parents=True)
TMP_DIR.mkdir(exist_ok=True, parents=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── Job Store (in-memory) ─────────────────────────────────────
jobs: dict = {}
cancel_flags: dict = {}


# ═══════════════════════════════════════════════════════════════
#  SUPABASE DATABASE HELPERS
# ═══════════════════════════════════════════════════════════════

def db_get_user(email: str):
    res = supabase.table("users").select("*").eq("email", email).execute()
    return res.data[0] if res.data else None

def db_create_user(email: str, name: str, password_hash: str):
    supabase.table("users").insert({
        "email": email, "name": name,
        "password_hash": password_hash,
        "is_verified": 1, "is_blocked": 0,
    }).execute()

def db_verify_user(email: str):
    supabase.table("users").update({"is_verified": 1}).eq("email", email).execute()

def db_update_password(email: str, password_hash: str):
    supabase.table("users").update({"password_hash": password_hash}).eq("email", email).execute()

def db_get_session(token: str):
    res = supabase.table("sessions").select("email").eq("token", token).execute()
    return res.data[0] if res.data else None

def db_create_session(token: str, email: str):
    supabase.table("sessions").insert({"token": token, "email": email}).execute()

def db_delete_session(token: str):
    supabase.table("sessions").delete().eq("token", token).execute()

def db_create_verify_token(token: str, email: str):
    supabase.table("verify_tokens").insert({"token": token, "email": email}).execute()

def db_get_verify_token(token: str):
    res = supabase.table("verify_tokens").select("*").eq("token", token).execute()
    return res.data[0] if res.data else None

def db_delete_verify_token(token: str):
    supabase.table("verify_tokens").delete().eq("token", token).execute()

def db_create_reset_token(token: str, email: str):
    supabase.table("reset_tokens").delete().eq("email", email).execute()
    supabase.table("reset_tokens").insert({"token": token, "email": email}).execute()

def db_get_reset_token(token: str):
    res = supabase.table("reset_tokens").select("*").eq("token", token).execute()
    return res.data[0] if res.data else None

def db_delete_reset_token(token: str):
    supabase.table("reset_tokens").delete().eq("token", token).execute()

def db_get_admin(email: str):
    res = supabase.table("admins").select("*").eq("email", email).execute()
    return res.data[0] if res.data else None

def db_create_admin(email: str, name: str, password_hash: str):
    supabase.table("admins").insert({
        "email": email, "name": name, "password_hash": password_hash,
    }).execute()

def db_save_video(job_id, email, topic, video_url, title, tags):
    supabase.table("videos").insert({
        "job_id": job_id, "email": email, "topic": topic,
        "video_url": video_url, "title": title, "tags": tags,
    }).execute()

def db_get_videos(email: str):
    res = supabase.table("videos").select("*").eq("email", email).order("created_at", desc=True).execute()
    return res.data or []

def db_delete_video(job_id: str, email: str):
    supabase.table("videos").delete().eq("job_id", job_id).eq("email", email).execute()

def db_get_all_users():
    res = supabase.table("users").select("*").order("created_at", desc=True).execute()
    return res.data or []

def db_get_all_videos():
    res = supabase.table("videos").select("*").order("created_at", desc=True).execute()
    return res.data or []

def db_delete_user(email: str):
    supabase.table("sessions").delete().eq("email", email).execute()
    supabase.table("videos").delete().eq("email", email).execute()
    supabase.table("users").delete().eq("email", email).execute()

def db_admin_delete_video(job_id: str):
    supabase.table("videos").delete().eq("job_id", job_id).execute()

def db_get_stats():
    total_users  = len(supabase.table("users").select("email").execute().data or [])
    total_videos = len(supabase.table("videos").select("job_id").execute().data or [])
    return total_users, total_videos


# ─── Startup ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    create_default_admin()


# ═══════════════════════════════════════════════════════════════
#  RENDI FFmpeg HELPERS
# ═══════════════════════════════════════════════════════════════

def rendi_run_ffmpeg(command: str, inputs: list) -> str:
    headers = {
        "x-api-key": RENDI_API_KEY,
        "Content-Type": "application/json",
    }

    input_files = {}
    for item in inputs:
        key = f"in_{item['name'].replace('.', '_').replace('-', '_')}"
        input_files[key] = item["url"]

    payload = {
        "ffmpeg_command": command,
        "input_files": input_files,
        "output_files": {"out_output_mp4": "out_output.mp4"}
    }

    res = requests.post(
        f"{RENDI_BASE_URL}/run-ffmpeg-command",
        headers=headers,
        json=payload,
        timeout=60
    )
    res.raise_for_status()
    job = res.json()
    job_id = job.get("command_id")
    print(f"  Rendi job submitted: {job_id}")

    for attempt in range(400):
        time.sleep(3)
        status_res = requests.get(
            f"{RENDI_BASE_URL}/commands/{job_id}",
            headers=headers,
            timeout=20
        )
        status_res.raise_for_status()
        status_data = status_res.json()

        state = status_data.get("status", "").upper()
        print(f"  Rendi status: {state} (attempt {attempt+1})")

        if state in ("COMPLETED", "SUCCESS"):
            output_files = status_data.get("output_files", {})
            print(f"  Output files received: {output_files}")

            if output_files:
                for key, value in output_files.items():
                    if isinstance(value, dict):
                        url = value.get("url") or value.get("storage_url") or value.get("download_url") or value.get("secure_url")
                    else:
                        url = str(value)
                    
                    if url and ("http" in url or "https" in url):
                        print(f"  ✅ Video ready: {url}")
                        return url

            print("⚠️ Success but URL not found in response. Full response:")
            print(status_data)

        if state in ("FAILED", "ERROR", "CANCELLED"):
            error_msg = status_data.get("error", "Unknown error")
            print(f"Rendi Full Error: {status_data}")
            raise RuntimeError(f"Rendi FFmpeg failed: {error_msg}")

    raise RuntimeError("Rendi: Job timeout after many attempts")


def upload_to_rendi(file_path: str, filename: str) -> str:
    """Upload file to Rendi (Correct Endpoint)"""
    url = f"{RENDI_BASE_URL}/files"

    headers = {
        "Authorization": f"Bearer {RENDI_API_KEY}",
    }

    try:
        with open(file_path, "rb") as f:
            files = {
                "file": (filename, f, "audio/mpeg")
            }
            
            res = requests.post(url, headers=headers, files=files, timeout=90)
            
            print(f"Rendi Upload Status: {res.status_code}")
            
            if not res.ok:
                print(f"Rendi Upload Error Response: {res.text}")
                res.raise_for_status()

            data = res.json()
            
            voice_url = (data.get("url") or 
                        data.get("file_url") or 
                        data.get("download_url") or 
                        data.get("storage_url"))
            
            if not voice_url:
                raise ValueError("No URL returned from Rendi")
                
            print(f"  Voice uploaded successfully: {voice_url[:80]}...")
            return voice_url

    except Exception as e:
        print(f"Rendi Upload Failed: {e}")
        raise


# ─── Email Helper ────────────────────────────────────────────────
def send_email(to_email: str, subject: str, html_body: str):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        raise ValueError("GMAIL credentials not set")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"StarFilm <{gmail_user}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_email, msg.as_string())
    print(f"  Email sent to {to_email}")


def email_template(title: str, body_html: str, btn_text: str, btn_url: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#000;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#000;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0"
        style="background:#0e0d0b;border:1px solid rgba(201,168,124,0.25);border-radius:4px;overflow:hidden;">
        <tr><td style="background:linear-gradient(90deg,#a07d52,#dfc49c);height:2px;"><tr></table>
        <tr><td style="padding:32px 36px 20px;text-align:center;border-bottom:1px solid rgba(201,168,124,0.15);">
          <div style="font-size:22px;font-weight:700;letter-spacing:0.22em;color:#c9a87c;text-transform:uppercase;">StarFilm</div>
          <div style="font-size:10px;letter-spacing:0.2em;color:#7a6e5a;margin-top:6px;text-transform:uppercase;">AI Shorts Production</div>
        </td>
      </tr>
      <tr>
        <td style="padding:32px 36px;">
          <h2 style="color:#dfc49c;font-size:16px;font-weight:600;margin:0 0 16px;">{title}</h2>
          <div style="color:#a09070;font-size:13px;line-height:1.7;">{body_html}</div>
          <table cellpadding="0" cellspacing="0" style="margin:28px 0 0;">
            <tr>
              <td style="border:1px solid #c9a87c;border-radius:4px;">
                <a href="{btn_url}"
                  style="display:inline-block;padding:12px 28px;color:#c9a87c;font-size:11px;font-weight:700;letter-spacing:0.22em;text-transform:uppercase;text-decoration:none;">{btn_text}</a>
              </td>
            </tr>
          </table>
          <p style="color:#4a4035;font-size:11px;margin-top:20px;">
            If you did not request this, please ignore this email.<br>
            This link expires in <strong style="color:#7a6e5a;">24 hours</strong>.
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:16px 36px;border-top:1px solid rgba(201,168,124,0.1);text-align:center;">
          <div style="font-size:10px;color:#4a4035;">StarFilm · AI Shorts Production</div>
        </td>
      </tr>
    </table>
  </td>
</tr>
</table>
</body></html>"""


# ─── Auth Helpers ───────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def create_session(email: str) -> str:
    token = str(uuid.uuid4())
    db_create_session(token, email)
    return token

def get_session_user(request: Request):
    token = request.cookies.get("sf_session")
    if not token:
        return None
    row = db_get_session(token)
    return row["email"] if row else None

def get_current_user_or_redirect(request: Request):
    user = get_session_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return user


# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/google94a976b56e3b917b.html")
async def google_verification():
    return FileResponse("google94a976b56e3b917b.html")

@app.get("/")
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/login")
def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))

# Protected routes
@app.get("/generate")
async def generate_page(request: Request):
    auth = get_current_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return FileResponse(str(STATIC_DIR / "generate.html"))

@app.get("/dashboard")
async def dashboard_page(request: Request):
    auth = get_current_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return FileResponse(str(STATIC_DIR / "dashboard.html"))

# Public info pages
@app.get("/about")
async def about_page():
    return FileResponse(str(STATIC_DIR / "about.html"))

@app.get("/privacy-policy")
async def privacy_page():
    return FileResponse(str(STATIC_DIR / "privacy-policy.html"))

@app.get("/terms-conditions")
async def terms_page():
    return FileResponse(str(STATIC_DIR / "terms-conditions.html"))

@app.get("/contact-us")
async def contact_page():
    return FileResponse(str(STATIC_DIR / "contact-us.html"))

@app.get("/faq")
async def faq_page():
    return FileResponse(str(STATIC_DIR / "faq.html"))

# Blog routes
@app.get("/ai-video-trends-2026")
async def ai_video_trends_page():
    return FileResponse(str(STATIC_DIR / "ai-video-trends-2026.html"))

@app.get("/emotional-ai-storytelling")
async def emotional_ai_storytelling_page():
    return FileResponse(str(STATIC_DIR / "emotional-ai-storytelling.html"))

@app.get("/voiceover-tips-shorts")
async def voiceover_tips_page():
    return FileResponse(str(STATIC_DIR / "voiceover-tips-shorts.html"))

@app.get("/shorts-algorithm-2026")
async def shorts_algorithm_page():
    return FileResponse(str(STATIC_DIR / "shorts-algorithm-2026.html"))

@app.get("/from-idea-to-viral")
async def from_idea_to_viral_page():
    return FileResponse(str(STATIC_DIR / "from-idea-to-viral.html"))

@app.get("/ai-vs-human-creativity")
async def ai_vs_human_creativity_page():
    return FileResponse(str(STATIC_DIR / "ai-vs-human-creativity.html"))

# Admin
@app.get("/admin")
async def admin_login_page():
    return FileResponse(str(STATIC_DIR / "admin-login.html"))

@app.get("/admin/dashboard")
async def admin_dashboard_page(request: Request):
    if not get_admin_user(request):
        return RedirectResponse(url="/admin?error=login_required")
    return FileResponse(str(STATIC_DIR / "admin-dashboard.html"))


# ═══════════════════════════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/api/signup")
async def signup(payload: dict, response: Response, request: Request):
    name     = payload.get("name", "").strip()
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "").strip()

    if not name or not email or not password:
        return JSONResponse({"error": "All fields are required"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"}, status_code=400)

    existing = db_get_user(email)
    if existing:
        return JSONResponse({"error": "Email already registered"}, status_code=400)

    db_create_user(email, name, hash_password(password))
    return {"success": True, "name": name, "verify": False}


@app.post("/api/login")
async def login(payload: dict, response: Response):
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "").strip()

    if not email or not password:
        return JSONResponse({"error": "Email and password are required"}, status_code=400)

    user = db_get_user(email)
    if not user or user["password_hash"] != hash_password(password):
        return JSONResponse({"error": "Invalid email or password"}, status_code=401)

    token = create_session(email)
    response.set_cookie(key="sf_session", value=token, httponly=True, max_age=86400*7)
    return {"success": True, "name": user["name"]}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("sf_session")
    if token:
        db_delete_session(token)
    response.delete_cookie("sf_session")
    return {"success": True}


@app.get("/api/me")
def me(request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    user = db_get_user(email)
    return {"email": email, "name": user["name"] if user else "User"}


@app.get("/api/stats")
def get_stats(request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    user   = db_get_user(email)
    videos = db_get_videos(email)
    return {
        "name":         user["name"] if user else "",
        "email":        email,
        "member_since": (user.get("created_at") or "")[:10],
        "total_videos": len(videos),
        "recent":       videos,
    }


@app.post("/api/change-password")
async def change_password(payload: dict, request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    current  = payload.get("current_password", "").strip()
    new_pass = payload.get("new_password", "").strip()
    if not current or not new_pass:
        return JSONResponse({"error": "All fields are required"}, status_code=400)
    if len(new_pass) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"}, status_code=400)
    user = db_get_user(email)
    if not user or user["password_hash"] != hash_password(current):
        return JSONResponse({"error": "Current password is incorrect"}, status_code=400)
    db_update_password(email, hash_password(new_pass))
    return {"success": True}


@app.post("/api/forgot-password")
async def forgot_password(payload: dict, request: Request):
    email = payload.get("email", "").strip().lower()
    if not email:
        return JSONResponse({"error": "Email is required"}, status_code=400)
    user = db_get_user(email)
    if not user:
        return {"success": True}
    r_token = secrets.token_urlsafe(32)
    db_create_reset_token(r_token, email)
    base_url  = str(request.base_url).rstrip("/")
    reset_url = f"{base_url}/reset-password?token={r_token}"
    try:
        send_email(email, "Reset your StarFilm password",
            email_template("Reset Your Password",
                f"Hi <strong style='color:#c9a87c'>{user['name']}</strong>,<br><br>Click below to reset your password.",
                "Reset Password", reset_url))
    except Exception as e:
        print(f"Email error: {e}")
        return JSONResponse({"error": "Could not send email."}, status_code=500)
    return {"success": True}


@app.get("/reset-password")
async def reset_password_page(token: str):
    row = db_get_reset_token(token)
    if not row:
        return RedirectResponse(url="/login?msg=invalid_token")
    return FileResponse(str(STATIC_DIR / "reset-password.html"))


@app.post("/api/reset-password")
async def do_reset_password(payload: dict):
    token        = payload.get("token", "").strip()
    new_password = payload.get("password", "").strip()
    if not token or not new_password:
        return JSONResponse({"error": "All fields are required"}, status_code=400)
    if len(new_password) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"}, status_code=400)
    row = db_get_reset_token(token)
    if not row:
        return JSONResponse({"error": "Invalid or expired reset link"}, status_code=400)
    db_update_password(row["email"], hash_password(new_password))
    db_delete_reset_token(token)
    return {"success": True}


@app.post("/api/start")
async def start_job(payload: dict, request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    topic = payload.get("topic", "").strip()
    if not topic:
        return JSONResponse({"error": "Topic is required"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "step": "Starting...", "progress": 0}
    cancel_flags[job_id] = False

    voice = payload.get("voice", "hi-IN-SwaraNeural")
    task  = asyncio.create_task(
        run_pipeline(job_id, topic,
                     payload.get("niche", ""),
                     payload.get("video_type", "Shorts"),
                     email, voice)
    )
    jobs[job_id]["task"] = task
    return {"job_id": job_id}


@app.post("/api/contact")
async def contact_form(payload: dict, request: Request):
    name    = payload.get("name", "").strip()
    email   = payload.get("email", "").strip()
    subject = payload.get("subject", "").strip()
    message = payload.get("message", "").strip()

    if not all([name, email, subject, message]):
        return JSONResponse({"error": "All fields are required"}, status_code=400)

    admin_email = os.getenv("ADMIN_EMAIL", "hamailsyed139@gmail.com")

    html_body = f"""
    <div style="font-family: monospace; background:#0e0d0b; padding:20px; color:#f0ead8;">
        <h2 style="color:#c9a87c;">New Contact Form Submission</h2>
        <p><strong>Name:</strong> {name}</p>
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Subject:</strong> {subject}</p>
        <p><strong>Message:</strong><br>{message.replace(chr(10), '<br>')}</p>
        <hr>
        <p style="font-size:12px; color:#7a6e5a;">Sent via StarFilm Contact Form</p>
    </div>
    """

    try:
        send_email(
            to_email=admin_email,
            subject=f"StarFilm Contact: {subject}",
            html_body=html_body
        )
        return {"success": True, "message": "Your message has been sent. We'll reply soon."}
    except Exception as e:
        print(f"Contact email error: {e}")
        return JSONResponse({"error": "Failed to send message. Please try again later."}, status_code=500)


@app.post("/api/cancel-job/{job_id}")
def cancel_job(job_id: str, request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if job_id not in jobs:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    cancel_flags[job_id] = True
    jobs[job_id]["status"]   = "canceled"
    jobs[job_id]["step"]     = "Canceled by user"
    jobs[job_id]["progress"] = 0
    return {"success": True}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    if job_id not in jobs:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    job = jobs[job_id].copy()
    job.pop("task", None)
    return job


@app.get("/api/history")
def get_history(request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return {"videos": db_get_videos(email)}


@app.delete("/api/history/{job_id}")
def delete_history(job_id: str, request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    db_delete_video(job_id, email)
    return {"success": True}


@app.get("/api/download/{job_id}")
async def download_video(job_id: str):
    result = supabase.table("videos").select("video_url, title").eq("job_id", job_id).execute()
    
    if not result.data:
        return JSONResponse({"error": "Video not found"}, status_code=404)
    
    video_url = result.data[0]["video_url"]
    title = result.data[0].get("title", f"starfilm_{job_id}") or f"starfilm_{job_id}"
    
    filename = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_') + ".mp4"
    
    try:
        response = requests.get(video_url, stream=True, timeout=60)
        response.raise_for_status()
        
        return StreamingResponse(
            response.iter_content(chunk_size=8192),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": response.headers.get("content-length", ""),
            }
        )
        
    except Exception as e:
        print(f"Download error: {e}")
        return RedirectResponse(url=video_url)


# ═══════════════════════════════════════════════════════════════
#  API CLIENT CLASSES FOR IMAGE FETCHING (with fallback)
# ═══════════════════════════════════════════════════════════════

class APIProvider:
    """Base class for image API providers"""
    def __init__(self, name, api_key):
        self.name = name
        self.api_key = api_key
    
    async def fetch_images(self, query, num_images=10):
        """Fetch images; return list of URLs or None on failure"""
        pass

class PixabayAPI(APIProvider):
    """Pixabay API – Priority 1"""
    def __init__(self):
        super().__init__("Pixabay", PIXABAY_API_KEY)
    
    async def fetch_images(self, query, num_images=10):
        if not self.api_key:
            print(f"⚠️ {self.name}: No API key provided.")
            return None
        try:
            short_query = query[:100]
            encoded_query = requests.utils.quote(short_query)
            url = (f"https://pixabay.com/api/?key={self.api_key}"
                   f"&q={encoded_query}&image_type=photo&orientation=vertical"
                   f"&per_page={num_images}&min_width=608&safesearch=true")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            images = data.get("hits", [])
            if images:
                image_urls = [img["webformatURL"] for img in images[:num_images]]
                print(f"✅ {self.name}: Found {len(image_urls)} images")
                return image_urls
            else:
                print(f"⚠️ {self.name}: No images found for '{query[:50]}...'")
                return None
        except Exception as e:
            print(f"❌ {self.name}: Request failed - {str(e)}")
            return None

class PexelsAPI(APIProvider):
    """Pexels API – Priority 2"""
    def __init__(self):
        super().__init__("Pexels", PEXELS_API_KEY)
    
    async def fetch_images(self, query, num_images=10):
        if not self.api_key:
            print(f"⚠️ {self.name}: No API key provided.")
            return None
        try:
            url = "https://api.pexels.com/v1/search"
            headers = {"Authorization": self.api_key}
            params = {"query": query, "per_page": num_images, "orientation": "portrait"}
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            images = data.get("photos", [])
            if images:
                image_urls = [img["src"]["original"] for img in images[:num_images]]
                print(f"✅ {self.name}: Found {len(image_urls)} images")
                return image_urls
            else:
                print(f"⚠️ {self.name}: No images found for '{query[:50]}...'")
                return None
        except Exception as e:
            print(f"❌ {self.name}: Request failed - {str(e)}")
            return None

class UnsplashAPI(APIProvider):
    """Unsplash API – Priority 3"""
    def __init__(self):
        super().__init__("Unsplash", UNSPLASH_ACCESS_KEY)
    
    async def fetch_images(self, query, num_images=10):
        if not self.api_key:
            print(f"⚠️ {self.name}: No API key provided.")
            return None
        try:
            url = "https://api.unsplash.com/search/photos"
            headers = {"Authorization": f"Client-ID {self.api_key}"}
            params = {"query": query, "per_page": num_images, "orientation": "portrait"}
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            images = data.get("results", [])
            if images:
                image_urls = [img["urls"]["raw"] for img in images[:num_images]]
                print(f"✅ {self.name}: Found {len(image_urls)} images")
                return image_urls
            else:
                print(f"⚠️ {self.name}: No images found for '{query[:50]}...'")
                return None
        except Exception as e:
            print(f"❌ {self.name}: Request failed - {str(e)}")
            return None

class PollinationsAPI(APIProvider):
    """Pollinations AI – Priority 4 (fallback)"""
    def __init__(self):
        super().__init__("Pollinations", POLLINATIONS_API_KEY)
    
    async def fetch_images(self, query, num_images=10):
        try:
            image_urls = []
            prompt_base = (f"cinematic vertical 9:16 image for a short video about: {query[:150]}, "
                           f"photorealistic, dramatic lighting")
            for i in range(1, num_images + 1):
                prompt = f"{prompt_base} scene number {i}"
                encoded_prompt = requests.utils.quote(prompt)
                url = (f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                       f"?width=608&height=1080&nologo=true&seed={i*777}")
                image_urls.append(url)
                await asyncio.sleep(0.2)  # avoid hitting rate limits
            print(f"✅ {self.name}: Generated {len(image_urls)} images")
            return image_urls
        except Exception as e:
            print(f"❌ {self.name}: Failed - {str(e)}")
            return None


# ═══════════════════════════════════════════════════════════════
#  PIPELINE (with multi-API fallback)
# ═══════════════════════════════════════════════════════════════

def update_job(job_id: str, step: str, progress: int):
    jobs[job_id]["step"]     = step
    jobs[job_id]["progress"] = progress
    print(f"[{job_id}] {progress}% - {step}")

def is_canceled(job_id: str) -> bool:
    return cancel_flags.get(job_id, False)


async def run_pipeline(job_id, topic, niche, video_type, user_email, voice):
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(exist_ok=True, parents=True)
    try:
        update_job(job_id, "Writing script...", 15)
        script = await asyncio.to_thread(generate_script, topic, niche, voice)
        if is_canceled(job_id): return

        update_job(job_id, "Generating voice...", 30)
        voice_path = str(job_dir / "voice.mp3")
        await asyncio.to_thread(generate_voice, script.get("voice_text"), voice_path, voice)
        if is_canceled(job_id): return

        # ---------- GET EXACT VOICE DURATION ----------
        voice_duration = 45.0  # fallback
        try:
            import subprocess
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', voice_path],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                voice_duration = float(result.stdout.strip())
            else:
                # fallback to file size estimation
                file_size_mb = os.path.getsize(voice_path) / (1024 * 1024)
                voice_duration = max(30.0, min(60.0, file_size_mb * 48))
        except Exception as e:
            print(f"Could not get exact duration: {e}, using estimation")
            file_size_mb = os.path.getsize(voice_path) / (1024 * 1024)
            voice_duration = max(30.0, min(60.0, file_size_mb * 48))

        # Clamp duration between 30 and 60 seconds
        voice_duration = max(30.0, min(60.0, voice_duration))
        print(f"✅ Voice duration: {voice_duration:.1f} seconds")

        # ---------- BUILD RELEVANT SEARCH PHRASE ----------
        script_text = script.get("hook","") + " " + script.get("body","")
        stop_words = {'the','and','for','with','that','this','from','are','was','were','his','her','their','they','she','he','it','its','you','your','our','my','a','an','is','of','to','in','on','at','by','be','as','or','but','if','so','then','just','can','will','would','could','should','have','has','had','been','also','very','only','one','two','three','more','some','such','into','than','then','them','these','those','what','which','when','where','who','whom','whose','why','how'}
        words = re.findall(r'\b[a-zA-Z]{4,}\b', script_text.lower())
        keywords = [w for w in words if w not in stop_words]
        keyword_part = ' '.join(keywords[:6]) if keywords else topic
        search_phrase = f"{topic} {niche} {keyword_part}".strip()[:100]
        print(f"🔍 Search phrase: {search_phrase}")

        # ---------- FETCH 10 RELEVANT IMAGES ----------
        update_job(job_id, "Fetching relevant images...", 45)
        providers = [PixabayAPI(), PexelsAPI(), UnsplashAPI(), PollinationsAPI()]
        image_urls = []
        for p in providers:
            urls = await p.fetch_images(search_phrase, 10)
            if urls and len(urls) >= 5:
                image_urls = urls
                print(f"✅ Using {p.name} – {len(image_urls)} images")
                break
        # If we got less than 10 images, pad with Picsum (topic-based)
        if len(image_urls) < 10:
            print(f"⚠️ Only {len(image_urls)} images from API, padding to 10 with Picsum")
            seed = abs(hash(topic)) % 1000
            # Keep the existing images, then add more
            while len(image_urls) < 10:
                idx = len(image_urls) + 1
                image_urls.append(f"https://picsum.photos/id/{(seed + idx) % 1000}/1080/1920")
        # Ensure exactly 10 (trim if too many)
        image_urls = image_urls[:10]
        print(f"✅ Final image count: {len(image_urls)}")
        if is_canceled(job_id): return

        # ---------- UPLOAD VOICE ----------
        update_job(job_id, "Uploading voice...", 60)
        def upload_voice(path):
            result = cloudinary.uploader.upload(path, resource_type="video", folder="starfilm/voices")
            return result["secure_url"]
        voice_url = await asyncio.to_thread(upload_voice, voice_path)

        # ---------- VIDEO COMPILATION WITH DYNAMIC TIMING ----------
        update_job(job_id, "Compiling video...", 75)
        n = 10
        img_dur = voice_duration / n  # each image duration
        print(f"🎬 Voice: {voice_duration:.1f}s → {n} images × {img_dur:.2f}s each = total {voice_duration:.1f}s video")

        # Build input arguments
        input_args = ""
        for i in range(n):
            input_args += f"-loop 1 -t {img_dur} -i {{{{in_img{i+1}_jpg}}}} "
        input_args += f"-i {{{{in_voice_mp3}}}}"

        # Force exact 1080x1920 portrait aspect ratio by scaling to fill and cropping the center
        filters = []
        for i in range(n):
            filters.append(f"[{i:d}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,setpts=PTS-STARTPTS[v{i:d}]")
        
        concat_input = "".join([f"[v{i:d}]" for i in range(n)])
        filters.append(f"{concat_input}concat=n={n:d}:v=1:a=0,format=yuv420p[vfinal]")
        filters.append(f"[{n:d}:a]volume=2.0[aout]")
        filter_complex = ";".join(filters)

        # FFmpeg command: cut video exactly to voice_duration seconds
        ffmpeg_cmd = (
            f"{input_args} -filter_complex \"{filter_complex}\" "
            f"-map [vfinal] -map [aout] -c:v libx264 -preset fast -crf 23 "
            f"-c:a aac -b:a 128k -movflags +faststart -pix_fmt yuv420p "
            f"-t {voice_duration} -shortest {{{{out_output_mp4}}}}"
        )

        rendi_inputs = [{"url": url, "name": f"img{i+1}.jpg"} for i, url in enumerate(image_urls)]
        rendi_inputs.append({"url": voice_url, "name": "voice.mp3"})
        video_url = await asyncio.to_thread(rendi_run_ffmpeg, ffmpeg_cmd, rendi_inputs)

        # ---------- FINALIZE ----------
        meta = generate_metadata(topic)
        jobs[job_id]["status"] = "done"
        jobs[job_id]["step"] = "Video ready!"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["video_url"] = video_url
        jobs[job_id]["metadata"] = meta
        jobs[job_id]["script"] = script

        if user_email:
            db_save_video(job_id, user_email, topic, video_url, meta.get("title", topic), meta.get("tags", ""))
        import shutil
        shutil.rmtree(str(job_dir), ignore_errors=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["step"] = f"Error: {str(e)}"


# ═══════════════════════════════════════════════════════════════
#  STEP FUNCTIONS (unchanged)
# ═══════════════════════════════════════════════════════════════

def generate_script(topic, niche, voice="hi-IN-SwaraNeutral"):
    language = "Hindi" if "hi-IN" in voice else "English"
    prompt = f"""You are a professional Shorts script writer.
Topic: {topic}
Niche: {niche}
Language: {language}

Write a voiceover script that will take exactly 60 seconds to speak (≈ 170-190 words).
Write emotional, engaging story with hook, body, and CTA.

Return ONLY valid JSON:
{{
  "hook": "...",
  "body": "...",
  "cta": "...",
  "full_script": "...",
  "voice_text": "..."
}}"""
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=1800
        )
        content = response.choices[0].message.content.strip()
        # clean JSON
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].strip()
        return json.loads(content)
    except:
        return {
            "hook": f"{topic} ki emotional kahani...",
            "body": "Yeh story ek insaan ki hai jisne kabhi haar nahi maani...",
            "cta": "Subscribe karein aur like karein!",
            "full_script": f"{topic} - 60 second emotional story",
            "voice_text": f"Ek ladki jo roz insult hoti thi. Usne AI seekhna shuru kiya. 5 saal baad woh apni khud ki company ki CEO ban gayi. Wohi log jo usko insult karte the, ab uske office mein job interview dene aaye. {topic} ki yeh kahani aapko inspire karegi. Agar aapko pasand aaye to channel subscribe karein."
        }


def generate_voice(text, output_path, voice="hi-IN-SwaraNeural"):
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        asyncio.run(communicate.save(output_path))
        print(f"  Voice generated: {voice}")
        return True
    except Exception as e:
        print(f"Edge-TTS Error: {e}")
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("No voice service available!")
        
        voice_id = "pNInz6obpgDQGcFmaJgB"
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(resp.content)
        print("  Voice generated: ElevenLabs")
        return True


CATEGORY_PROMPTS = {
    "ai_emotional_story": [
        "{base} futuristic emotional portrait, cyberpunk cinematic lighting",
        "{base} AI human emotional connection scene",
        "{base} robot with realistic emotions, ultra detailed",
        "{base} emotional futuristic city atmosphere",
        "{base} sad AI realization moment, cinematic mood",
        "{base} emotional android close-up, glowing eyes",
        "{base} futuristic storytelling scene, high detail",
        "{base} peaceful AI and human interaction",
        "{base} cinematic sci-fi emotional action shot",
        "{base} heart touching futuristic ending scene"
    ],
    "motivation": [
        "{base} inspiring cinematic portrait, powerful expression",
        "{base} success mindset moment, golden lighting",
        "{base} emotional victory scene, ultra realistic",
        "{base} confident close-up face, motivational mood",
        "{base} never give up moment, cinematic atmosphere",
        "{base} powerful speech scene, dramatic lighting",
        "{base} emotional breakthrough moment, inspiring vibe",
        "{base} achieving impossible goal, cinematic realism",
        "{base} dynamic action success shot, high energy",
        "{base} heart touching inspirational ending scene"
    ],
    "horror": [
        "{base} terrifying dark atmosphere, horror cinematic lighting",
        "{base} scary close-up face, dramatic shadows",
        "{base} abandoned haunted place, ultra realistic",
        "{base} creepy emotional moment, dark foggy scene",
        "{base} monster reveal scene, cinematic horror style",
        "{base} intense fear expression, detailed eyes",
        "{base} suspense thriller shot, realistic lighting",
        "{base} ghostly emotional scene, horror movie vibe",
        "{base} cinematic chase scene, dark environment",
        "{base} shocking final horror moment, ultra detailed"
    ],
    "romance": [
        "{base} romantic cinematic close-up, soft lighting",
        "{base} emotional love confession scene",
        "{base} couple emotional moment, warm sunset tones",
        "{base} sad breakup cinematic shot",
        "{base} deep emotional eye contact scene",
        "{base} romantic storytelling atmosphere, ultra realistic",
        "{base} heartfelt emotional hug scene",
        "{base} peaceful romantic walk, dreamy lighting",
        "{base} cinematic romantic action shot",
        "{base} emotional happy ending love scene"
    ],
    "sad_story": [
        "{base} heartbreaking emotional close-up",
        "{base} lonely atmosphere, cinematic rain",
        "{base} emotional crying scene, ultra realistic",
        "{base} painful emotional expression",
        "{base} dramatic loss moment, cinematic lighting",
        "{base} deep emotional storytelling atmosphere",
        "{base} emotional silence scene",
        "{base} nostalgic emotional memory shot",
        "{base} tragic cinematic moment",
        "{base} emotional heartbreaking ending"
    ],
    "fantasy": [
        "{base} magical fantasy world, cinematic lighting",
        "{base} enchanted forest atmosphere",
        "{base} mystical creature reveal scene",
        "{base} fantasy warrior cinematic portrait",
        "{base} glowing magical powers, ultra detailed",
        "{base} fantasy castle environment",
        "{base} epic mythical storytelling shot",
        "{base} magical emotional scene",
        "{base} cinematic dragon action scene",
        "{base} legendary fantasy ending moment"
    ],
    "superhero": [
        "{base} superhero cinematic portrait",
        "{base} epic heroic action pose",
        "{base} dramatic cape movement scene",
        "{base} city rescue cinematic atmosphere",
        "{base} glowing superpowers ultra detailed",
        "{base} emotional hero close-up",
        "{base} legendary battle scene",
        "{base} cinematic flying action shot",
        "{base} heroic sacrifice emotional moment",
        "{base} epic superhero ending"
    ],
}


def generate_images(topic, niche, job_dir, job_id=""):
    # This function is kept for compatibility but not used in new pipeline.
    pass


def generate_metadata(topic):
    import random
    titles = [
        f"This AI Story Will SHOCK You! | {topic} #shorts",
        f"What Happened Next Will Break Your Heart... | {topic} #shorts",
        f"This AI Story Will Make You CRY | {topic} #shorts",
        f"Nobody Expected This... {topic} #shorts",
        f"The Most EMOTIONAL AI Story Ever | {topic} #shorts",
    ]
    tags = ["AI story", "emotional story", "youtube shorts", "AI shorts", "viral shorts", topic.lower()]
    return {
        "title": random.choice(titles),
        "description": f"{topic} - A powerful emotional AI short story.\nLIKE & SUBSCRIBE!\n#AIStory #Shorts",
        "tags": ", ".join(tags)
    }


# ═══════════════════════════════════════════════════════════════
#  ADMIN
# ═══════════════════════════════════════════════════════════════

def create_default_admin():
    admin_email = os.getenv("ADMIN_EMAIL", "hamailsyed139@gmail.com")
    admin_password = os.getenv("ADMIN_PASSWORD")

    if not admin_password:
        print("⚠️ Warning: ADMIN_PASSWORD environment variable not set!")
        return

    if not db_get_admin(admin_email):
        db_create_admin(admin_email, "Super Admin", hash_password(admin_password))
        print("✅ Default Admin Created")

def get_admin_user(request: Request):
    token = request.cookies.get("admin_session")
    if not token: return None
    row = db_get_session(token)
    return row["email"] if row else None

@app.post("/api/admin/login")
async def admin_login(payload: dict, response: Response):
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    admin    = db_get_admin(email)
    if not admin or admin["password_hash"] != hash_password(password):
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)
    token = str(uuid.uuid4())
    db_create_session(token, email)
    response.set_cookie(key="admin_session", value=token, httponly=True, max_age=86400*7)
    return {"success": True, "name": admin["name"]}

@app.get("/api/admin/stats")
def admin_stats(request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    total_users, total_videos = db_get_stats()
    return {"total_users": total_users, "total_videos": total_videos, "today_videos": 0}

@app.get("/api/admin/users")
def admin_users(request: Request, search: str = ""):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    users = db_get_all_users()
    if search:
        users = [u for u in users if search.lower() in u["email"].lower() or search.lower() in u["name"].lower()]
    return {"users": users}

@app.get("/api/admin/videos")
def admin_videos(request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return {"videos": db_get_all_videos()}

@app.delete("/api/admin/video/{job_id}")
def admin_delete_video(job_id: str, request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    db_admin_delete_video(job_id)
    return {"success": True}

@app.delete("/api/admin/user/{email}")
def admin_delete_user(email: str, request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    db_delete_user(email)
    return {"success": True}


# ─── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    create_default_admin()
    print("\nStarFilm is running!")
    print("Open: http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
