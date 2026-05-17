"""
YouTube Shorts Maker - Main Backend
=====================================
Topic → Script → Voice → Images → Video → Final Video
"""

import os
import re
import time
import json
import uuid
import hashlib
import sqlite3
import asyncio
import secrets
import smtplib
import requests
import subprocess
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq

load_dotenv()

# SSL Fix
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['CURL_CA_BUNDLE'] = ''

app = FastAPI(title="Shorts Maker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Directories ───────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


# ─── Job Status Store (in-memory) ──────────────────────────────
jobs: dict = {}

# ─── Cancel Store ──────────────────────────────
cancel_flags: dict = {}

# ─── Database Setup (SQLite — persistent) ──────────────────────
DB_PATH = BASE_DIR / "users.db"

def get_db():
    """Get a DB connection — call this inside each function."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    # Users table — is_verified added
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email         TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_verified   INTEGER DEFAULT 0,
            is_blocked    INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    # Sessions
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Email verification tokens
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verify_tokens (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Password reset tokens
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reset_tokens (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # === NEW TABLES -- Admins ===
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            email         TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    # Saved videos history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            job_id     TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            topic      TEXT NOT NULL,
            video_url  TEXT NOT NULL,
            title      TEXT,
            tags       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Active Jobs (for persistence across pages)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_jobs (
            job_id       TEXT PRIMARY KEY,
            email        TEXT NOT NULL,
            topic        TEXT NOT NULL,
            status       TEXT DEFAULT 'running',
            progress     INTEGER DEFAULT 0,
            step         TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        )
    """)

    conn.commit()
    conn.close()
    print(f"  Database ready: {DB_PATH}")


# ─── Email Helper ────────────────────────────────────────────────
def send_email(to_email: str, subject: str, html_body: str):
    """Send email via Gmail SMTP."""
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        raise ValueError("GMAIL_USER or GMAIL_APP_PASSWORD not set in .env")

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
    """StarFilm branded email template."""
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#000000;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#000;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#0e0d0b;border:1px solid rgba(201,168,124,0.25);border-radius:4px;overflow:hidden;">
        <!-- Gold top bar -->
        <tr><td style="background:linear-gradient(90deg,#a07d52,#dfc49c);height:2px;"></td></tr>
        <!-- Header -->
        <tr><td style="padding:32px 36px 20px;text-align:center;border-bottom:1px solid rgba(201,168,124,0.15);">
          <div style="font-size:22px;font-weight:700;letter-spacing:0.22em;color:#c9a87c;text-transform:uppercase;">StarFilm</div>
          <div style="font-size:10px;letter-spacing:0.2em;color:#7a6e5a;margin-top:6px;text-transform:uppercase;">AI Shorts Production</div>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:32px 36px;">
          <h2 style="color:#dfc49c;font-size:16px;font-weight:600;letter-spacing:0.08em;margin:0 0 16px;">{title}</h2>
          <div style="color:#a09070;font-size:13px;line-height:1.7;font-weight:300;">{body_html}</div>
          <!-- Button -->
          <table cellpadding="0" cellspacing="0" style="margin:28px 0 0;">
            <tr><td style="background:transparent;border:1px solid #c9a87c;border-radius:4px;">
              <a href="{btn_url}" style="display:inline-block;padding:12px 28px;color:#c9a87c;font-size:11px;font-weight:700;letter-spacing:0.22em;text-transform:uppercase;text-decoration:none;">{btn_text}</a>
            </td></tr>
          </table>
          <p style="color:#4a4035;font-size:11px;margin-top:20px;line-height:1.6;">
            If you did not request this, please ignore this email.<br>
            This link expires in <strong style="color:#7a6e5a;">24 hours</strong>.
          </p>
        </td></tr>
        <!-- Footer -->
        <tr><td style="padding:16px 36px;border-top:1px solid rgba(201,168,124,0.1);text-align:center;">
          <div style="font-size:10px;color:#4a4035;letter-spacing:0.12em;">StarFilm &nbsp;·&nbsp; AI Shorts Production</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

# ─── Auth Helpers ───────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def create_session(email: str) -> str:
    token = str(uuid.uuid4())
    conn  = get_db()
    conn.execute("INSERT INTO sessions (token, email) VALUES (?, ?)", (token, email))
    conn.commit()
    conn.close()
    return token

def get_session_user(request: Request):
    token = request.cookies.get("sf_session")
    if not token:
        return None
    conn = get_db()
    row  = conn.execute("SELECT email FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row["email"] if row else None

# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/")
def root(request: Request):
    # If logged in → app, else → login page
    user = get_session_user(request)
    if user:
        index = STATIC_DIR / "index.html"
        return FileResponse(str(index))
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

    conn = get_db()
    existing = conn.execute("SELECT email FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"error": "Email already registered"}, status_code=400)

    conn.execute(
        "INSERT INTO users (email, name, password_hash, is_verified) VALUES (?, ?, ?, 0)",
        (email, name, hash_password(password))
    )

    # Create verification token
    v_token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO verify_tokens (token, email) VALUES (?, ?)", (v_token, email))
    conn.commit()
    conn.close()

    # Send verification email
    base_url = str(request.base_url).rstrip("/")
    verify_url = f"{base_url}/verify-email?token={v_token}"
    try:
        send_email(
            to_email=email,
            subject="Verify your StarFilm account",
            html_body=email_template(
                title="Verify Your Email",
                body_html=f"Hi <strong style='color:#c9a87c'>{name}</strong>,<br><br>Welcome to StarFilm! Please verify your email address to activate your account.",
                btn_text="Verify Email Address",
                btn_url=verify_url,
            )
        )
    except Exception as e:
        print(f"  Email error: {e}")
        return JSONResponse({"error": "Account created but email could not be sent. Check GMAIL settings."}, status_code=500)

    return {"success": True, "name": name, "verify": True}

@app.post("/api/login")
async def login(payload: dict, response: Response):
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "").strip()

    if not email or not password:
        return JSONResponse({"error": "Email and password are required"}, status_code=400)

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()

    if not user or user["password_hash"] != hash_password(password):
        return JSONResponse({"error": "Invalid email or password"}, status_code=401)

    if not user["is_verified"]:
        return JSONResponse({"error": "Please verify your email first. Check your inbox."}, status_code=403)

    token = create_session(email)
    response.set_cookie(key="sf_session", value=token, httponly=True, max_age=86400*7)
    return {"success": True, "name": user["name"]}

@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("sf_session")
    if token:
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    response.delete_cookie("sf_session")
    return {"success": True}

@app.get("/api/me")
def me(request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    conn = get_db()
    user = conn.execute("SELECT name FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return {"email": email, "name": user["name"] if user else "User"}


@app.get("/dashboard")
def dashboard_page(request: Request):
    user = get_session_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return FileResponse(str(STATIC_DIR / "dashboard.html"))

@app.get("/api/stats")
def get_stats(request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    conn = get_db()
    
    # User Info
    user = conn.execute("""
        SELECT name, email, created_at 
        FROM users WHERE email = ?
    """, (email,)).fetchone()
    
    # Total Videos
    total_videos = conn.execute("""
        SELECT COUNT(*) as c FROM videos WHERE email = ?
    """, (email,)).fetchone()
    
    # Recent Videos (Full History - No Limit)
    recent = conn.execute("""
        SELECT 
            job_id,
            topic,
            title,
            video_url,
            '/output/' || job_id || '/image1.jpg' as thumbnail_url,
            created_at
        FROM videos 
        WHERE email = ? 
        ORDER BY created_at DESC
    """, (email,)).fetchall()

    conn.close()
    
    return {
        "name":         user["name"] if user else "",
        "email":        email,
        "member_since": user["created_at"][:10] if user and user["created_at"] else "",
        "total_videos": total_videos["c"] if total_videos else 0,
        "recent":       [dict(r) for r in recent]
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
        return JSONResponse({"error": "New password must be at least 6 characters"}, status_code=400)
    conn = get_db()
    user = conn.execute("SELECT password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if not user or user["password_hash"] != hash_password(current):
        conn.close()
        return JSONResponse({"error": "Current password is incorrect"}, status_code=400)
    conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (hash_password(new_pass), email))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/verify-email")
async def verify_email(token: str):
    """User clicks link in email → verify account."""
    conn = get_db()
    row  = conn.execute("SELECT * FROM verify_tokens WHERE token = ?", (token,)).fetchone()

    if not row:
        conn.close()
        return RedirectResponse(url="/login?msg=invalid_token")

    conn.execute("UPDATE users SET is_verified = 1 WHERE email = ?", (row["email"],))
    conn.execute("DELETE FROM verify_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/login?msg=verified")


@app.post("/api/forgot-password")
async def forgot_password(payload: dict, request: Request):
    email = payload.get("email", "").strip().lower()
    if not email:
        return JSONResponse({"error": "Email is required"}, status_code=400)

    conn  = get_db()
    user  = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if not user:
        conn.close()
        # Don't reveal if email exists
        return {"success": True}

    # Delete old reset tokens for this email
    conn.execute("DELETE FROM reset_tokens WHERE email = ?", (email,))

    r_token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO reset_tokens (token, email) VALUES (?, ?)", (r_token, email))
    conn.commit()
    conn.close()

    base_url   = str(request.base_url).rstrip("/")
    reset_url  = f"{base_url}/reset-password?token={r_token}"

    try:
        send_email(
            to_email=email,
            subject="Reset your StarFilm password",
            html_body=email_template(
                title="Reset Your Password",
                body_html=f"Hi <strong style='color:#c9a87c'>{user['name']}</strong>,<br><br>We received a request to reset your StarFilm password. Click the button below to set a new password.",
                btn_text="Reset Password",
                btn_url=reset_url,
            )
        )
    except Exception as e:
        print(f"  Email error: {e}")
        return JSONResponse({"error": "Could not send email. Check GMAIL settings."}, status_code=500)

    return {"success": True}


@app.get("/reset-password")
async def reset_password_page(token: str):
    """Validate token then show reset password page."""
    conn = get_db()
    row  = conn.execute("SELECT * FROM reset_tokens WHERE token = ?", (token,)).fetchone()
    conn.close()
    if not row:
        return RedirectResponse(url="/login?msg=invalid_token")
    return FileResponse(str(STATIC_DIR / "reset-password.html"))


@app.post("/api/reset-password")
async def do_reset_password(payload: dict):
    token       = payload.get("token", "").strip()
    new_password = payload.get("password", "").strip()

    if not token or not new_password:
        return JSONResponse({"error": "All fields are required"}, status_code=400)
    if len(new_password) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"}, status_code=400)

    conn = get_db()
    row  = conn.execute("SELECT * FROM reset_tokens WHERE token = ?", (token,)).fetchone()

    if not row:
        conn.close()
        return JSONResponse({"error": "Invalid or expired reset link"}, status_code=400)

    conn.execute(
        "UPDATE users SET password_hash = ? WHERE email = ?",
        (hash_password(new_password), row["email"])
    )
    conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()

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

    # Save job for persistence
    conn = get_db()
    conn.execute("""
        INSERT INTO active_jobs (job_id, email, topic, status, progress, step)
        VALUES (?, ?, ?, 'running', 0, 'Starting...')
    """, (job_id, email, topic))
    conn.commit()
    conn.close()

    jobs[job_id] = {"status": "running", "step": "Starting...", "progress": 0}
    cancel_flags[job_id] = False

    voice = payload.get("voice", "hi-IN-SwaraNeural")

    # Create async task
    task = asyncio.create_task(
        run_pipeline(
            job_id,
            topic,
            payload.get("niche", ""),
            payload.get("video_type", "Shorts"),
            email,
            voice
        )
    )

    # Save task reference
    jobs[job_id]["task"] = task

    return {"job_id": job_id}

# ====================== CANCEL JOB ======================
@app.post("/api/cancel-job/{job_id}")
def cancel_job(job_id: str, request: Request):
    email = get_session_user(request)

    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if job_id not in jobs:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    # Cancel flag ON
    cancel_flags[job_id] = True

    # Memory update
    jobs[job_id]["status"] = "canceled"
    jobs[job_id]["step"] = "Canceled by user"
    jobs[job_id]["progress"] = 0

    # Database update
    try:
        conn = get_db()

        conn.execute("""
            UPDATE active_jobs
            SET status = 'canceled',
                step = 'Canceled by user'
            WHERE job_id = ? AND email = ?
        """, (job_id, email))

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"[{job_id}] DB Cancel Error: {e}")

    print(f"[{job_id}] ❌ Job canceled successfully")

    return {
        "success": True,
        "message": "Generation canceled"
    }


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):

    if job_id not in jobs:
        return JSONResponse(
            {"error": "Job not found"},
            status_code=404
        )

    job = jobs[job_id].copy()

    # Remove asyncio task before JSON response
    if "task" in job:
        del job["task"]

    return job


@app.get("/api/history")
def get_history(request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    rows = conn.execute(
        "SELECT job_id, topic, video_url, title, tags, created_at FROM videos WHERE email = ? ORDER BY created_at DESC",
        (email,)
    ).fetchall()
    conn.close()
    videos = []
    for r in rows:
        # Check if video file still exists
        video_path = OUTPUT_DIR / r["job_id"] / "final_shorts.mp4"
        if video_path.exists():
            videos.append({
                "job_id":     r["job_id"],
                "topic":      r["topic"],
                "video_url":  r["video_url"],
                "title":      r["title"],
                "tags":       r["tags"],
                "created_at": r["created_at"],
            })
    return {"videos": videos}


@app.delete("/api/history/{job_id}")
def delete_history(job_id: str, request: Request):
    email = get_session_user(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    # Only delete if it belongs to this user
    conn.execute("DELETE FROM videos WHERE job_id = ? AND email = ?", (job_id, email))
    conn.commit()
    conn.close()
    # Also delete files
    import shutil
    job_dir = OUTPUT_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(str(job_dir))
    return {"success": True}


@app.get("/api/download/{job_id}")
def download_video(job_id: str):
    video_path = OUTPUT_DIR / job_id / "final_shorts.mp4"
    if not video_path.exists():
        return JSONResponse({"error": "Video not ready"}, status_code=404)
    return FileResponse(
        str(video_path),
        media_type="video/mp4",
        filename=f"shorts_{job_id}.mp4",
    )


# ═══════════════════════════════════════════════════════════════
#  PIPELINE
# ═══════════════════════════════════════════════════════════════

def update_job(job_id: str, step: str, progress: int):
    jobs[job_id]["step"]     = step
    jobs[job_id]["progress"] = progress
    print(f"[{job_id}] {progress}% - {step}")

def is_job_canceled(job_id: str) -> bool:
    return cancel_flags.get(job_id, False)


async def run_pipeline(
    job_id: str,
    topic: str,
    niche: str,
    video_type: str,
    user_email: str = "",
    voice: str = "hi-IN-SwaraNeural"
):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:

        # ─────────────────────────────
        # 1. SCRIPT
        # ─────────────────────────────
        update_job(job_id, "Writing emotional script...", 15)

        script = await asyncio.to_thread(
            generate_script,
            topic,
            niche,
            voice
        )

        if is_job_canceled(job_id):
            jobs[job_id]["status"] = "canceled"
            jobs[job_id]["step"] = "Canceled by user"
            return

        # ─────────────────────────────
        # 2. VOICE
        # ─────────────────────────────
        update_job(job_id, "Generating natural voice...", 30)

        voice_path = job_dir / "voice.mp3"

        await asyncio.to_thread(
            generate_voice,
            script.get("voice_text", script.get("full_script", topic)),
            str(voice_path),
            voice
        )

        if is_job_canceled(job_id):
            jobs[job_id]["status"] = "canceled"
            jobs[job_id]["step"] = "Canceled by user"
            return

        # ─────────────────────────────
        # 3. IMAGES
        # ─────────────────────────────
        update_job(job_id, "Generating images...", 45)

        image_paths = await asyncio.to_thread(
            generate_images,
            topic,
            niche,
            str(job_dir),
            job_id
        )

        if is_job_canceled(job_id):
            jobs[job_id]["status"] = "canceled"
            jobs[job_id]["step"] = "Canceled by user"
            return

        # ─────────────────────────────
        # 4. VIDEO
        # ─────────────────────────────
        update_job(job_id, "Compiling video (FFmpeg)...", 70)

        final_video = job_dir / "final_shorts.mp4"

        await asyncio.to_thread(
            compile_video,
            image_paths,
            str(voice_path),
            str(final_video),
            job_id
        )

        if is_job_canceled(job_id):
            jobs[job_id]["status"] = "canceled"
            jobs[job_id]["step"] = "Canceled by user"
            return

        # ─────────────────────────────
        # 5. METADATA
        # ─────────────────────────────
        meta = generate_metadata(topic)

        (job_dir / "metadata.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        # ─────────────────────────────
        # SUCCESS
        # ─────────────────────────────
        jobs[job_id]["status"] = "done"
        jobs[job_id]["step"] = "Video ready!"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["video_url"] = f"/output/{job_id}/final_shorts.mp4"
        jobs[job_id]["metadata"] = meta
        jobs[job_id]["script"] = script

        # Save history
        if user_email:

            try:
                conn = get_db()

                conn.execute(
                    """
                    INSERT INTO videos
                    (job_id, email, topic, video_url, title, tags)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        user_email,
                        topic,
                        f"/output/{job_id}/final_shorts.mp4",
                        meta.get("title", topic),
                        meta.get("tags", "")
                    )
                )

                conn.commit()
                conn.close()

            except Exception as db_err:
                print(f"History save error: {db_err}")

    except Exception as e:
        import traceback

        traceback.print_exc()

        jobs[job_id]["status"] = "error"
        jobs[job_id]["step"] = f"Error: {str(e)}"
        jobs[job_id]["error"] = str(e)


# ═══════════════════════════════════════════════════════════════
#  STEP FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def generate_script(topic: str, niche: str, voice: str = "hi-IN-SwaraNeural"):
    """Generate script in Hindi or English based on selected voice"""
    
    language = "Hindi" if "hi-IN" in voice else "English"
    script_style = "emotional, heart touching YouTube Shorts style" if "hi-IN" in voice else "emotional, engaging YouTube Shorts style"
    
    prompt = f"""
You are a professional YouTube Shorts script writer.

Topic: {topic}
Niche: {niche}
Language: {language}
Style: {script_style}

Write a powerful emotional short story script in **{language}** language only.

Structure:
1. Hook (first 3-5 seconds - very catchy)
2. Body (story development with emotions)
3. Twist / Climax
4. Emotional ending + CTA

Return response in valid JSON format only:
{{
  "hook": "...",
  "body": "...",
  "cta": "...",
  "full_script": "...",
  "voice_text": "..."   // This text will be used for TTS
}}

Full script should be natural and spoken style. Use emotional language.
"""

    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.85,
            max_tokens=1200,
        )
        
        content = response.choices[0].message.content.strip()
        
        # Clean JSON if needed
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].strip()
        
        meta = json.loads(content)
        return meta
        
    except Exception as e:
        print(f"Script Generation Error: {e}")
        # Fallback
        return {
            "hook": f"{topic} ki emotional kahani...",
            "body": "Yeh ek bohot hi dilchasp aur emotional story hai...",
            "cta": "Video poori dekhiye aur like + subscribe karna na bhooliye!",
            "full_script": f"{topic} - Ek emotional kahani...",
            "voice_text": f"{topic} ki yeh emotional story aapko zaroor rulayegi..."
        }


def generate_voice(text: str, output_path: str, voice: str = "hi-IN-SwaraNeural"):
    """Generate natural voice using Edge-TTS (Free)"""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        asyncio.run(communicate.save(output_path))
        print(f"  Voice generated: {voice}")
        return True
    except Exception as e:
        print(f"Edge-TTS Error: {e}. Falling back to VoiceRSS...")
        return generate_voice_voicerss(text, output_path)


def generate_voice_voicerss(text: str, output_path: str):
    """Fallback method"""
    api_key = os.getenv("VOICERSS_API_KEY")
    if not api_key:
        raise ValueError("No voice service available!")
    
    url = f"https://api.voicerss.org/?key={api_key}&hl=en-us&c=MP3&src={requests.utils.quote(text)}&r=0&f=48khz_16bit_stereo"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return True


# Category-wise Prompts (Aapke diye gaye)
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
    "animal_story": [
        "{base} cute emotional animal close-up, Pixar style",
        "{base} emotional pet moment, soft cinematic lighting",
        "{base} sad animal expression, ultra detailed eyes",
        "{base} happy animal friendship scene, warm tones",
        "{base} lost animal emotional scene, dramatic mood",
        "{base} brave animal hero moment, cinematic storytelling",
        "{base} emotional reunion with animal, realistic lighting",
        "{base} peaceful nature animal scene, beautiful atmosphere",
        "{base} cinematic animal rescue action scene",
        "{base} touching emotional animal ending scene"
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
    "action": [
        "{base} explosive cinematic action scene",
        "{base} intense combat moment, ultra realistic",
        "{base} fast-paced chase sequence, movie style",
        "{base} dramatic battlefield atmosphere",
        "{base} heroic action pose, cinematic lighting",
        "{base} high energy fighting scene",
        "{base} slow-motion cinematic shot",
        "{base} intense emotional action close-up",
        "{base} epic destruction scene, detailed effects",
        "{base} legendary final battle moment"
    ],
    "comedy": [
        "{base} funny exaggerated expression, cartoon vibe",
        "{base} hilarious reaction moment",
        "{base} goofy cinematic scene, colorful atmosphere",
        "{base} funny accident scene, high detail",
        "{base} cheerful comedy storytelling moment",
        "{base} meme-worthy expression, ultra realistic",
        "{base} awkward funny situation",
        "{base} playful comedic atmosphere",
        "{base} energetic comedy action scene",
        "{base} happy hilarious ending moment"
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
    "sci_fi": [
        "{base} futuristic sci-fi cinematic portrait",
        "{base} neon cyberpunk city atmosphere",
        "{base} advanced technology environment",
        "{base} spaceship cinematic scene",
        "{base} glowing futuristic armor, ultra detailed",
        "{base} alien world storytelling shot",
        "{base} emotional sci-fi character close-up",
        "{base} high-tech action sequence",
        "{base} cinematic futuristic battle scene",
        "{base} epic sci-fi ending moment"
    ],
    "thriller": [
        "{base} suspenseful cinematic atmosphere",
        "{base} mysterious dark alley scene",
        "{base} intense thriller close-up expression",
        "{base} hidden danger cinematic shot",
        "{base} psychological tension moment",
        "{base} dramatic shadows and lighting",
        "{base} suspense storytelling environment",
        "{base} cinematic mystery action scene",
        "{base} shocking revelation moment",
        "{base} edge-of-seat thriller ending"
    ],
    "historical": [
        "{base} historical cinematic portrait",
        "{base} ancient civilization atmosphere",
        "{base} royal historical environment",
        "{base} historical battlefield scene",
        "{base} vintage cinematic storytelling",
        "{base} emotional historical moment",
        "{base} traditional clothing ultra detailed",
        "{base} epic ancient action sequence",
        "{base} legendary historical atmosphere",
        "{base} emotional historical ending"
    ],
    "spiritual": [
        "{base} peaceful spiritual atmosphere",
        "{base} divine cinematic lighting",
        "{base} emotional spiritual meditation scene",
        "{base} heavenly glowing environment",
        "{base} calm emotional portrait",
        "{base} spiritual awakening cinematic shot",
        "{base} peaceful nature storytelling",
        "{base} emotional faith moment",
        "{base} cinematic spiritual journey",
        "{base} heart touching peaceful ending"
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
    "kids_story": [
        "{base} colorful cartoon storytelling scene",
        "{base} cute happy animated character",
        "{base} magical playful atmosphere",
        "{base} cheerful kids adventure moment",
        "{base} adorable emotional expression",
        "{base} fantasy cartoon environment",
        "{base} bright joyful cinematic lighting",
        "{base} playful action storytelling shot",
        "{base} friendship emotional scene",
        "{base} happy fairytale ending"
    ]
}


def generate_images(topic: str, niche: str, job_dir: str, job_id: str = "") -> list:
    """Generate 10 images - Optimized for Speed"""

    base = f"Ultra photorealistic vertical 9:16 image about {topic}, cinematic lighting, detailed, 8k"

    niche_key = niche.lower().replace(" ", "_").replace("-", "_")

    if niche_key in CATEGORY_PROMPTS:
        raw_prompts = CATEGORY_PROMPTS[niche_key][:10]
    else:
        raw_prompts = [
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
        ]

    image_paths = []

    print("🎨 Generating 10 images (Fast Mode)...")

    for i, prompt in enumerate(raw_prompts, 1):

        # ✅ CANCEL CHECK
        if job_id and is_job_canceled(job_id):
            print(f"[{job_id}] Image generation canceled")
            return image_paths

        final_prompt = prompt.replace("{base}", base)

        encoded = requests.utils.quote(final_prompt)

        img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=608&height=1080&nologo=true&enhance=false&seed={i*777}"

        out_path = os.path.join(job_dir, f"image{i}.jpg")

        for attempt in range(2):

            try:
                r = requests.get(img_url, timeout=50)

                r.raise_for_status()

                with open(out_path, "wb") as f:
                    f.write(r.content)

                image_paths.append(out_path)

                print(f"  ✅ Image {i}/10 saved")

                break

            except:
                time.sleep(1.5)

    while len(image_paths) < 10 and image_paths:
        image_paths.append(image_paths[-1])

    return image_paths[:10]


def compile_video(image_paths: list, voice_path: str, output_path: str, job_id: str = ""):

    # ✅ CANCEL CHECK
    if job_id and is_job_canceled(job_id):
        print(f"[{job_id}] Video compilation canceled")
        return

    if not image_paths:
        raise RuntimeError("No images provided for video compilation")

    valid_images = []

    for img_path in image_paths:

        if os.path.exists(img_path) and os.path.getsize(img_path) > 5000:
            valid_images.append(img_path)

        else:
            print(f"⚠️ Skipping corrupted/missing image: {img_path}")

    if len(valid_images) < 3:
        raise RuntimeError(f"Too few valid images ({len(valid_images)}). Cannot compile video.")

    image_paths = valid_images

    n = len(image_paths)

    img_dur = 4.2

    print(f"Compiling video with {n} valid images...")

    inputs = []

    for p in image_paths:
        inputs += ["-loop", "1", "-t", str(img_dur), "-i", p]

    inputs += ["-i", voice_path]

    filters = []

    for i in range(n):

        filters.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            f"zoompan=z='if(eq(on,1),1.0,zoom+0.002)':d={int(img_dur*30)}:x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':s=1080x1920[v{i}]"
        )

    prev = "v0"

    xfade = ""

    for i in range(1, n):

        out_label = f"v0{i}" if i < n-1 else "vout"

        offset = (i * img_dur) - 0.8

        xfade += f"[{prev}][v{i}]xfade=transition=fade:duration=0.7:offset={offset}[{out_label}];"

        prev = out_label

    filter_complex = ";".join(filters) + ";" + xfade + "[vout]fps=30[vfinal];" + f"[{n}:a]volume=2.0[aout]"

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vfinal]", "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:

        error_msg = result.stderr[-800:] if result.stderr else "Unknown FFmpeg error"

        print(f"❌ FFmpeg Error: {error_msg}")

        raise RuntimeError(f"FFmpeg Error: {error_msg}")

    print(f"  ✅ Video compiled successfully with {n} images")


def generate_metadata(topic: str) -> dict:
    """Generate YouTube title, description and tags."""
    import random
    titles = [
        f"This AI Story Will SHOCK You! | {topic} #shorts",
        f"What Happened Next Will Break Your Heart... | {topic} #shorts",
        f"This AI Story Will Make You CRY | {topic} #shorts",
        f"Nobody Expected This... {topic} #shorts",
        f"The Most EMOTIONAL AI Story Ever | {topic} #shorts",
    ]
    title = random.choice(titles)
    description = (
        f"{topic} - A powerful and emotional AI short story that will leave you speechless.\n\n"
        "Welcome to our channel where we share the most emotional AI stories every day!\n\n"
        f"This story is about: {topic}\n\n"
        "Watch till the end - the twist will shock you!\n\n"
        "---\n"
        "LIKE if it touched your heart\n"
        "SUBSCRIBE for daily emotional AI stories\n"
        "COMMENT your reaction below\n"
        "---\n\n"
        "#AIStory #EmotionalStory #YouTubeShorts #AIShorts #ViralShorts"
    )
    tags = [
        "AI story", "emotional story", "youtube shorts", "AI shorts",
        "viral shorts", "heart touching", "AI emotional", "shorts",
        "mind blowing", topic.lower(),
    ]
    return {"title": title, "description": description, "tags": ", ".join(tags)}

# ====================== DEFAULT ADMIN ======================
def create_default_admin():
    conn = get_db()
    admin_email = "hamailsyed139@gmail.com"
    
    existing = conn.execute("SELECT email FROM admins WHERE email=?", (admin_email,)).fetchone()
    
    if not existing:
        conn.execute(
            "INSERT INTO admins (email, name, password_hash) VALUES (?, ?, ?)",
            (admin_email, "Super Admin", hash_password("hamailsyed139"))
        )
        conn.commit()
        print("✅ Default Admin Created → hamailsyed139@gmail.com / hamailsyed139")
    else:
        print("Admin already exists.")
    
    conn.close()

# ====================== ADMIN ROUTES ======================

@app.get("/admin")
def admin_login_page():
    return FileResponse(str(STATIC_DIR / "admin-login.html"))

@app.post("/api/admin/login")
async def admin_login(payload: dict, response: Response):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")

    conn = get_db()
    admin = conn.execute("SELECT * FROM admins WHERE email = ?", (email,)).fetchone()
    conn.close()

    if not admin or admin["password_hash"] != hash_password(password):
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

    token = str(uuid.uuid4())
    conn = get_db()
    conn.execute("INSERT INTO sessions (token, email) VALUES (?, ?)", (token, email))
    conn.commit()
    conn.close()

    response.set_cookie(key="admin_session", value=token, httponly=True, max_age=86400*7)
    return {"success": True, "name": admin["name"]}


def get_admin_user(request: Request):
    token = request.cookies.get("admin_session")
    if not token: return None
    conn = get_db()
    row = conn.execute("SELECT email FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row["email"] if row else None


@app.get("/admin/dashboard")
def admin_dashboard(request: Request):
    admin_email = get_admin_user(request)
    if not admin_email:
        # Strong redirect
        response = RedirectResponse(url="/admin?error=login_required", status_code=303)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    
    return FileResponse(str(STATIC_DIR / "admin-dashboard.html"))


@app.get("/api/admin/stats")
def admin_stats(request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    today_videos = conn.execute("SELECT COUNT(*) FROM videos WHERE date(created_at) = date('now')").fetchone()[0]
    conn.close()

    return {
        "total_users": total_users,
        "total_videos": total_videos,
        "today_videos": today_videos
    }


@app.get("/api/admin/users")
def admin_users(request: Request, search: str = ""):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    conn = get_db()
    if search:
        rows = conn.execute("""
            SELECT email, name, is_verified, is_blocked, created_at 
            FROM users 
            WHERE email LIKE ? OR name LIKE ? 
            ORDER BY created_at DESC
        """, (f"%{search}%", f"%{search}%")).fetchall()
    else:
        rows = conn.execute("SELECT email, name, is_verified, is_blocked, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}


@app.get("/api/admin/videos")
def admin_videos(request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    conn = get_db()
    rows = conn.execute("""
        SELECT v.job_id, v.topic, v.title, v.video_url, v.created_at, u.name as user_name, u.email
        FROM videos v
        LEFT JOIN users u ON v.email = u.email
        ORDER BY v.created_at DESC
    """).fetchall()
    conn.close()
    return {"videos": [dict(r) for r in rows]}


@app.delete("/api/admin/video/{job_id}")
def admin_delete_video(job_id: str, request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    conn = get_db()
    conn.execute("DELETE FROM videos WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()

    # Delete folder
    import shutil
    folder = OUTPUT_DIR / job_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    return {"success": True}


@app.delete("/api/admin/user/{email}")
def admin_delete_user(email: str, request: Request):
    if not get_admin_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    conn = get_db()
    
    # User ko completely delete + uski sessions clear
    conn.execute("DELETE FROM users WHERE email = ?", (email,))
    conn.execute("DELETE FROM videos WHERE email = ?", (email,))
    conn.execute("DELETE FROM sessions WHERE email = ?", (email,))      # ← Important (logout force)
    conn.execute("DELETE FROM active_jobs WHERE email = ?", (email,))   # ← Extra safety
    conn.commit()
    conn.close()

    return {"success": True}


# ─── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()                    # ← Pehle tables create ho
    create_default_admin()       # ← Phir admin create ho
    
    import uvicorn
    print("\nStarFilm is running!")
    print("Open in browser: http://localhost:8000")
    print("Admin Login → http://localhost:8000/admin")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)