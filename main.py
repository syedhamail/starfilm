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
    """
    Rendi API ko FFmpeg command bhejo.
    Returns: output file URL
    """
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
        timeout=30
    )
    res.raise_for_status()
    job = res.json()
    job_id = job["command_id"]

    print(f"  Rendi job submitted: {job_id}")

    # Poll for completion
    for attempt in range(200):  # Increased timeout (10+ minutes)
        time.sleep(3)
        status_res = requests.get(
            f"{RENDI_BASE_URL}/commands/{job_id}",
            headers=headers,
            timeout=15
        )
        status_res.raise_for_status()
        status_data = status_res.json()

        state = status_data.get("status", "").upper()
        print(f"  Rendi status: {state} (attempt {attempt+1})")

        if state in ("COMPLETED", "SUCCESS"):
            output_files = status_data.get("output_files", {})
            if output_files:
                first = list(output_files.values())[0]
                if isinstance(first, dict):
                    url = first.get("url") or first.get("storage_url") or first.get("download_url")
                else:
                    url = first
                if url:
                    print(f"  Video ready: {url}")
                    return url
            raise RuntimeError("Rendi: Output URL not found even after success")

        if state in ("FAILED", "ERROR", "CANCELLED"):
            error_msg = status_data.get("error", "Unknown error")
            raise RuntimeError(f"Rendi FFmpeg failed: {error_msg}")

    raise RuntimeError("Rendi: Job timeout after 10 minutes")


def upload_to_rendi(file_path: str, filename: str) -> str:
    """Upload file to Rendi (Correct Endpoint)"""
    url = f"{RENDI_BASE_URL}/files"   # ← Correct Endpoint

    headers = {
        "Authorization": f"Bearer {RENDI_API_KEY}",
    }

    try:
        with open(file_path, "rb") as f:
            files = {
                "file": (filename, f, "audio/mpeg")   # mp3 ke liye
            }
            
            res = requests.post(url, headers=headers, files=files, timeout=90)
            
            print(f"Rendi Upload Status: {res.status_code}")
            
            if not res.ok:
                print(f"Rendi Upload Error Response: {res.text}")
                res.raise_for_status()

            data = res.json()
            
            # Possible response keys
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
        <tr><td style="background:linear-gradient(90deg,#a07d52,#dfc49c);height:2px;"></td></tr>
        <tr><td style="padding:32px 36px 20px;text-align:center;border-bottom:1px solid rgba(201,168,124,0.15);">
          <div style="font-size:22px;font-weight:700;letter-spacing:0.22em;color:#c9a87c;text-transform:uppercase;">StarFilm</div>
          <div style="font-size:10px;letter-spacing:0.2em;color:#7a6e5a;margin-top:6px;text-transform:uppercase;">AI Shorts Production</div>
        </td></tr>
        <tr><td style="padding:32px 36px;">
          <h2 style="color:#dfc49c;font-size:16px;font-weight:600;margin:0 0 16px;">{title}</h2>
          <div style="color:#a09070;font-size:13px;line-height:1.7;">{body_html}</div>
          <table cellpadding="0" cellspacing="0" style="margin:28px 0 0;">
            <tr><td style="border:1px solid #c9a87c;border-radius:4px;">
              <a href="{btn_url}"
                style="display:inline-block;padding:12px 28px;color:#c9a87c;font-size:11px;font-weight:700;letter-spacing:0.22em;text-transform:uppercase;text-decoration:none;">{btn_text}</a>
            </td></tr>
          </table>
          <p style="color:#4a4035;font-size:11px;margin-top:20px;">
            If you did not request this, please ignore this email.<br>
            This link expires in <strong style="color:#7a6e5a;">24 hours</strong>.
          </p>
        </td></tr>
        <tr><td style="padding:16px 36px;border-top:1px solid rgba(201,168,124,0.1);text-align:center;">
          <div style="font-size:10px;color:#4a4035;">StarFilm · AI Shorts Production</div>
        </td></tr>
      </table>
    </td></tr>
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


# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/google94a976b56e3b917b.html")
async def google_verification():
    return Response(content="google-site-verification: google94a976b56e3b917b.html", media_type="text/plain")

@app.get("/")
def root(request: Request):
    user = get_session_user(request)
    if user:
        return FileResponse(str(STATIC_DIR / "index.html"))
    return RedirectResponse(url="/login")

@app.get("/login")
def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))

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


@app.get("/dashboard")
def dashboard_page(request: Request):
    if not get_session_user(request):
        return RedirectResponse(url="/login")
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


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
    # Get video URL from database
    result = supabase.table("videos").select("video_url, title").eq("job_id", job_id).execute()
    
    if not result.data:
        return JSONResponse({"error": "Video not found"}, status_code=404)
    
    video_url = result.data[0]["video_url"]
    title = result.data[0].get("title", f"starfilm_{job_id}") or f"starfilm_{job_id}"
    
    # Clean title for filename
    filename = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_') + ".mp4"
    
    try:
        # Fetch video from Rendi storage
        response = requests.get(video_url, stream=True, timeout=60)
        response.raise_for_status()
        
        # Return as downloadable file
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
        # Fallback: simple redirect (agar streaming fail ho)
        return RedirectResponse(url=video_url)


# ═══════════════════════════════════════════════════════════════
#  PIPELINE
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
        # ── 1. SCRIPT ──────────────────────────────────────────
        update_job(job_id, "Writing emotional script...", 15)
        script = await asyncio.to_thread(generate_script, topic, niche, voice)
        if is_canceled(job_id): return

        # ── 2. VOICE ───────────────────────────────────────────
        update_job(job_id, "Generating voice...", 30)
        voice_path = str(job_dir / "voice.mp3")
        await asyncio.to_thread(
            generate_voice,
            script.get("voice_text", script.get("full_script", topic)),
            voice_path, voice
        )
        if is_canceled(job_id): return

        # Voice duration estimate
        voice_duration = 45.0
        try:
            file_size_mb = os.path.getsize(voice_path) / (1024 * 1024)
            voice_duration = max(35.0, min(65.0, file_size_mb * 48))
            print(f"  Estimated voice duration: {voice_duration:.1f} sec")
        except:
            pass

        # ── 3. IMAGES ──────────────────────────────────────────
        update_job(job_id, "Generating images...", 45)
        await asyncio.to_thread(generate_images, topic, niche, str(job_dir), job_id)
        if is_canceled(job_id): return

        # ── 4. UPLOAD VOICE ────────────────────────────────────
        update_job(job_id, "Uploading voice to cloud...", 60)
        def upload_voice_cloudinary(voice_path):
            result = cloudinary.uploader.upload(
                voice_path,
                resource_type="video",
                folder="starfilm/voices",
            )
            return result["secure_url"]

        voice_url = await asyncio.to_thread(upload_voice_cloudinary, voice_path)
        print(f"  Voice uploaded: {voice_url}")

        # ── 5. IMAGE URLs ──────────────────────────────────────
        update_job(job_id, "Preparing cinematic images...", 65)

        niche_key = niche.lower().replace(" ", "_").replace("-", "_")
        base = f"Ultra photorealistic vertical 9:16 image about {topic}, cinematic lighting, detailed, 8k"
        
        raw_prompts = CATEGORY_PROMPTS.get(niche_key, [
            f"{base}, emotional close-up portrait",
            f"{base}, intense dramatic moment",
            f"{base}, surprised emotional reaction",
            f"{base}, sad emotional scene",
            f"{base}, happy emotional moment",
            f"{base}, dramatic storytelling",
            f"{base}, close-up with strong emotion",
            f"{base}, peaceful powerful moment",
            f"{base}, dynamic cinematic shot",
            f"{base}, heart touching ending scene"
        ])[:10]

        image_urls = []
        for i, prompt in enumerate(raw_prompts, 1):
            final_prompt = prompt.replace("{base}", base)
            encoded = requests.utils.quote(final_prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded}?width=608&height=1080&nologo=true&seed={i*777}"
            image_urls.append(url)

        if is_canceled(job_id): return

        # ── 6. RENDI VIDEO COMPILATION ─────────────────────────
        update_job(job_id, "Compiling video with Rendi...", 75)

        n = len(image_urls)
        img_dur = max(3.0, min(8.0, (voice_duration + 3.0) / n))

        rendi_inputs = []
        for i, url in enumerate(image_urls):
            rendi_inputs.append({"url": url, "name": f"image{i+1}.jpg"})
        rendi_inputs.append({"url": voice_url, "name": "voice.mp3"})

        # FFmpeg Command
        input_args = "".join([f"-loop 1 -t {img_dur} -i {{{{in_image{i+1}_jpg}}}} " for i in range(n)])
        input_args += "-i {{in_voice_mp3}}"

        filters = [f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2[v{i}]" for i in range(n)]

        prev = "v0"
        xfade = ""
        for i in range(1, n):
            out_label = f"v0{i}" if i < n-1 else "vout"
            offset = (i * img_dur) - 0.8
            xfade += f"[{prev}][v{i}]xfade=transition=fade:duration=0.7:offset={offset}[{out_label}];"
            prev = out_label

        filter_complex = ";".join(filters) + ";" + xfade + f"[vout]fps=30[vfinal];[{n}:a]volume=2.0[aout]"

        ffmpeg_command = (
            f"{input_args} -filter_complex \"{filter_complex}\" "
            f"-map [vfinal] -map [aout] -c:v libx264 -preset ultrafast -crf 26 "
            f"-c:a aac -b:a 128k -shortest -pix_fmt yuv420p "
            f"{{{{out_output_mp4}}}}"
        )

        video_url = await asyncio.to_thread(
            rendi_run_ffmpeg, ffmpeg_command, rendi_inputs
        )

        # ── 7. FINALIZE ────────────────────────────────────────
        meta = generate_metadata(topic)

        jobs[job_id]["status"]    = "done"
        jobs[job_id]["step"]      = "Video ready!"
        jobs[job_id]["progress"]  = 100
        jobs[job_id]["video_url"] = video_url
        jobs[job_id]["metadata"]  = meta
        jobs[job_id]["script"]    = script

        if user_email:
            db_save_video(job_id, user_email, topic, video_url, 
                         meta.get("title", topic), meta.get("tags", ""))

        # Cleanup
        import shutil
        shutil.rmtree(str(job_dir), ignore_errors=True)

    except Exception as e:
        import traceback
        traceback.print_exc()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["step"]   = f"Error: {str(e)}"
        jobs[job_id]["error"]  = str(e)


# ═══════════════════════════════════════════════════════════════
#  STEP FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def generate_script(topic, niche, voice="hi-IN-SwaraNeural"):
    language = "Hindi" if "hi-IN" in voice else "English"
    
    prompt = f"""
You are a professional Shorts script writer.
Topic: {topic}
Niche: {niche}
Language: {language}

Write a **detailed emotional story** that is long enough for a **40-50 second video**.
Make it engaging with good storytelling, emotions, and a strong ending.

Requirements:
- Total speaking time should be around 50-60 seconds when spoken naturally at normal pace.
- voice_text field mein kam az kam 200-250 words likhna zaroori hai.
- Include hook, emotional body, and powerful CTA.

Return ONLY valid JSON:
{{
  "hook": "...",
  "body": "...",
  "cta": "...",
  "full_script": "...",
  "voice_text": "..."
}}
"""

    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.85, 
            max_tokens=1500,   # Increased
        )
        content = response.choices[0].message.content.strip()
        
        # Clean JSON
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].strip()
            
        return json.loads(content)
        
    except Exception as e:
        print(f"Script Error: {e}")
        # Fallback longer script
        return {
            "hook": f"{topic} ki ek emotional aur inspiring kahani...",
            "body": "Yeh story ek aise insaan ki hai jo bohot mushkilon se guzra... har din insult hota tha... lekin usne haar nahi maani... secretly mehnat karta raha... aur ek din uski zindagi badal gayi...",
            "cta": "Agar yeh story pasand aayi to like aur subscribe kar dena! Comment mein batao aapki kya soch hai.",
            "full_script": f"{topic} - Ek dilchasp aur emotional kahani jo aapko sochne par majboor kar degi...",
            "voice_text": f"Ek delivery boy jo roz insult hota tha... secretly AI tools seekh raha tha... 5 saal baad woh billion dollar company ka CEO ban gaya... aur wohi log jo usko insult karte the, ab uske office mein job interview dene aaye... {topic} ki yeh emotional story aapko zaroor inspire karegi."
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
        # ElevenLabs fallback
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("No voice service available!")
        
        # ElevenLabs Hindi voice ID
        voice_id = "pNInz6obpgDQGcFmaJgB"  # Adam voice
        
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
    base      = f"Ultra photorealistic vertical 9:16 image about {topic}, cinematic lighting, detailed, 8k"
    niche_key = niche.lower().replace(" ", "_").replace("-", "_")
    raw_prompts = CATEGORY_PROMPTS.get(niche_key, [
        f"{base}, emotional close-up portrait",
        f"{base}, intense dramatic moment",
        f"{base}, surprised emotional reaction",
        f"{base}, sad emotional scene",
        f"{base}, happy emotional moment",
        f"{base}, dramatic storytelling",
        f"{base}, close-up with strong emotion",
        f"{base}, peaceful powerful moment",
        f"{base}, dynamic cinematic shot",
        f"{base}, heart touching ending scene"
    ])[:10]

    image_paths = []
    print("Generating 10 images...")

    for i, prompt in enumerate(raw_prompts, 1):
        if job_id and is_canceled(job_id):
            return image_paths
        final_prompt = prompt.replace("{base}", base)
        encoded      = requests.utils.quote(final_prompt)
        img_url      = f"https://image.pollinations.ai/prompt/{encoded}?width=608&height=1080&nologo=true&seed={i*777}"
        out_path     = os.path.join(job_dir, f"image{i}.jpg")
        for attempt in range(2):
            try:
                r = requests.get(img_url, timeout=50)
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    f.write(r.content)
                image_paths.append(out_path)
                print(f"  Image {i}/10 saved")
                break
            except:
                time.sleep(1.5)

    while len(image_paths) < 10 and image_paths:
        image_paths.append(image_paths[-1])
    return image_paths[:10]


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

@app.get("/admin")
def admin_login_page():
    return FileResponse(str(STATIC_DIR / "admin-login.html"))

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

@app.get("/admin/dashboard")
def admin_dashboard(request: Request):
    if not get_admin_user(request):
        return RedirectResponse(url="/admin?error=login_required", status_code=303)
    return FileResponse(str(STATIC_DIR / "admin-dashboard.html"))

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
