"""
StarFilm - YouTube Shorts Maker (Public Version)
No login, no Supabase. Just enter a topic and generate a video.
"""

import os
import re
import time
import json
import uuid
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
from fastapi.responses import StreamingResponse
import re
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

# ─── Rendi Setup ───────────────────────────────────────────────
RENDI_API_KEY = os.getenv("RENDI_API_KEY")
RENDI_BASE_URL = "https://api.rendi.dev/v1"

# ─── Image API Keys ─────────────────────────────────────────────
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
    url = f"{RENDI_BASE_URL}/files"
    headers = {"Authorization": f"Bearer {RENDI_API_KEY}"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (filename, f, "audio/mpeg")}
            res = requests.post(url, headers=headers, files=files, timeout=90)
            res.raise_for_status()
            data = res.json()
            voice_url = (data.get("url") or data.get("file_url") or 
                         data.get("download_url") or data.get("storage_url"))
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
        <tr><td style="background:linear-gradient(90deg,#a07d52,#dfc49c);height:2px;"></tr>
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


# ═══════════════════════════════════════════════════════════════
#  ROUTES (ALL PUBLIC – NO AUTH)
# ═══════════════════════════════════════════════════════════════

@app.get("/google94a976b56e3b917b.html")
async def google_verification():
    return FileResponse("google94a976b56e3b917b.html")

@app.get("/")
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))

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

# Video generation page (public)
@app.get("/generate")
async def generate_page():
    return FileResponse(str(STATIC_DIR / "generate.html"))


# ═══════════════════════════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/api/start")
async def start_job(payload: dict, request: Request):
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
                     voice)
    )
    jobs[job_id]["task"] = task
    return {"job_id": job_id}


@app.post("/api/contact")
async def contact_form(payload: dict):
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
def cancel_job(job_id: str):
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


@app.get("/api/download/{job_id}")
async def download_video(job_id: str):
    if job_id not in jobs:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    job = jobs[job_id]
    if job.get("status") != "done":
        return JSONResponse({"error": "Video not ready yet"}, status_code=400)
    video_url = job.get("video_url")
    if not video_url:
        return JSONResponse({"error": "Video URL not found"}, status_code=404)

    # Fetch and stream the video
    try:
        response = requests.get(video_url, stream=True, timeout=60)
        response.raise_for_status()
        filename = f"starfilm_{job_id}.mp4"
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
    def __init__(self, name, api_key):
        self.name = name
        self.api_key = api_key

    async def fetch_images(self, query, num_images=10):
        pass

class PixabayAPI(APIProvider):
    def __init__(self):
        super().__init__("Pixabay", PIXABAY_API_KEY)

    async def fetch_images(self, query, num_images=10):
        if not self.api_key:
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
            return None
        except Exception as e:
            print(f"❌ {self.name}: {e}")
            return None

class PexelsAPI(APIProvider):
    def __init__(self):
        super().__init__("Pexels", PEXELS_API_KEY)

    async def fetch_images(self, query, num_images=10):
        if not self.api_key:
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
            return None
        except Exception as e:
            print(f"❌ {self.name}: {e}")
            return None

class UnsplashAPI(APIProvider):
    def __init__(self):
        super().__init__("Unsplash", UNSPLASH_ACCESS_KEY)

    async def fetch_images(self, query, num_images=10):
        if not self.api_key:
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
            return None
        except Exception as e:
            print(f"❌ {self.name}: {e}")
            return None

class PollinationsAPI(APIProvider):
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
                await asyncio.sleep(0.2)
            print(f"✅ {self.name}: Generated {len(image_urls)} images")
            return image_urls
        except Exception as e:
            print(f"❌ {self.name}: {e}")
            return None


# ═══════════════════════════════════════════════════════════════
#  PIPELINE (public version – no user email)
# ═══════════════════════════════════════════════════════════════

def update_job(job_id: str, step: str, progress: int):
    jobs[job_id]["step"]     = step
    jobs[job_id]["progress"] = progress
    print(f"[{job_id}] {progress}% - {step}")

def is_canceled(job_id: str) -> bool:
    return cancel_flags.get(job_id, False)


async def run_pipeline(job_id, topic, niche, video_type, voice):
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(exist_ok=True, parents=True)

    try:
        update_job(job_id, "Writing emotional script...", 15)
        script = await asyncio.to_thread(generate_script, topic, niche, voice)
        if is_canceled(job_id): return

        update_job(job_id, "Generating voice...", 30)
        voice_path = str(job_dir / "voice.mp3")
        await asyncio.to_thread(generate_voice, script.get("voice_text"), voice_path, voice)
        if is_canceled(job_id): return

        # ---------- GET EXACT VOICE DURATION ----------
        voice_duration = 45.0
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', voice_path],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                voice_duration = float(result.stdout.strip())
            else:
                file_size_mb = os.path.getsize(voice_path) / (1024 * 1024)
                voice_duration = max(30.0, min(60.0, file_size_mb * 48))
        except:
            file_size_mb = os.path.getsize(voice_path) / (1024 * 1024)
            voice_duration = max(30.0, min(60.0, file_size_mb * 48))

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

        if len(image_urls) < 10:
            print(f"⚠️ Only {len(image_urls)} images from API, padding to 10 with Picsum")
            seed = abs(hash(topic)) % 1000
            while len(image_urls) < 10:
                idx = len(image_urls) + 1
                image_urls.append(f"https://picsum.photos/id/{(seed + idx) % 1000}/1080/1920")
        image_urls = image_urls[:10]
        print(f"✅ Final image count: {len(image_urls)}")
        if is_canceled(job_id): return

        # ---------- UPLOAD VOICE ----------
        update_job(job_id, "Uploading voice...", 60)
        def upload_voice(path):
            result = cloudinary.uploader.upload(path, resource_type="video", folder="starfilm/voices")
            return result["secure_url"]
        voice_url = await asyncio.to_thread(upload_voice, voice_path)

        # ---------- VIDEO COMPILATION ----------
        update_job(job_id, "Compiling video...", 75)
        n = 10
        img_dur = voice_duration / n
        print(f"🎬 Voice: {voice_duration:.1f}s → {n} images × {img_dur:.2f}s each = total {voice_duration:.1f}s video")

        input_args = ""
        for i in range(n):
            input_args += f"-loop 1 -t {img_dur} -i {{{{in_img{i+1}_jpg}}}} "
        input_args += f"-i {{{{in_voice_mp3}}}}"

        filters = []
        for i in range(n):
            filters.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,setpts=PTS-STARTPTS[v{i}]")
        concat_input = "".join([f"[v{i}]" for i in range(n)])
        filters.append(f"{concat_input}concat=n={n}:v=1:a=0,format=yuv420p[vfinal]")
        filters.append(f"[{n}:a]volume=2.0[aout]")
        filter_complex = ";".join(filters)

        ffmpeg_cmd = (
            f"{input_args} -filter_complex \"{filter_complex}\" "
            f"-map [vfinal] -map [aout] -c:v libx264 -preset ultrafast -crf 30 "
            f"-c:a aac -b:a 128k -pix_fmt yuv420p "
            f"-t {voice_duration} -shortest {{{{out_output_mp4}}}}"
        )

        rendi_inputs = [{"url": url, "name": f"img{i+1}.jpg"} for i, url in enumerate(image_urls)]
        rendi_inputs.append({"url": voice_url, "name": "voice.mp3"})
        video_url = await asyncio.to_thread(rendi_run_ffmpeg, ffmpeg_cmd, rendi_inputs)

        # ---------- FINALIZE ----------
        meta = generate_metadata(topic)
        jobs[job_id]["status"]    = "done"
        jobs[job_id]["step"]      = "Video ready!"
        jobs[job_id]["progress"]  = 100
        jobs[job_id]["video_url"] = video_url
        jobs[job_id]["metadata"]  = meta
        jobs[job_id]["script"]    = script

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

Write a voiceover script that will take between 30 and 40 seconds to speak (approx. 130-170 words).
Write an emotional, engaging story with a strong hook, emotional body, and a clear call to action.

The voice_text must contain at least 130 words. Keep the story natural and conversational.

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
            temperature=0.85, max_tokens=2000
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].strip()
        script_data = json.loads(content)
        if len(script_data.get("voice_text", "")) < 600:
            script_data["voice_text"] += " Yahi kahani hai is video ki. Agar aapko pasand aaye to like aur share karein. Aur aisi aur inspiring kahaniyon ke liye channel ko subscribe karna na bhoolen."
        return script_data
    except Exception as e:
        print(f"Script Error: {e}")
        return {
            "hook": f"{topic} ki emotional aur inspiring kahani suniye...",
            "body": "Yeh kahani ek aise insaan ki hai jo kabhi haar nahi maanta. Har roz usay naye mushkilon ka saamna karna padta tha. Log usay beizat karte the, lekin usne apna hausla nahi khoya. Usne raat raat bhar mehnat ki, naye haathyaar seekhay. Woh roz subah 4 baje uthta aur naye haathyaar seekhta. Phir ek din, woh apni manzil tak pohanch gaya. Aaj woh apni kahani suna raha hai aur sab ko inspire kar raha hai. Woh kehta hai, agar aap dil se mehnat karo to koi bhi manzil mushkil nahi. Bas apne sapnon par bharosa rakho aur lagatar mehnat karte raho. Aakhir kaamyaabi tumhare kadam chumegi.",
            "cta": "Agar aapko yeh kahani pasand aayi to channel subscribe karein aur video like karein!",
            "full_script": f"{topic} - 30 second emotional story",
            "voice_text": f"{topic} ki kahani aapko rula degi. Ek ladka jo roz insult hota tha, usne AI seekhna shuru kiya. 5 saal baad woh apni khud ki company ka CEO ban gaya. Wohi log jo usko insult karte the, ab uski company mein naukri dhoond rahe hain. Yeh kahani sikhayegi ke mehnat kabhi zaya nahi jaati. Agar aap bhi apni zindagi mein kuch badalna chahte hain, to aaj hi AI seekhna shuru karein. Subscribe karein aur aisi aur kahaniyon ke liye bell icon dabaayein."
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
        headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(resp.content)
        print("  Voice generated: ElevenLabs")
        return True


CATEGORY_PROMPTS = { ... }  # (keep as is – I'll omit for brevity, but it's unchanged)

def generate_images(topic, niche, job_dir, job_id=""):
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


# ─── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("\nStarFilm is running (public version)!")
    print("Open: http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)


