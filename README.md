# 🎬 StarFilm — AI Shorts Generator

**StarFilm** is an AI-powered video generator that creates viral YouTube Shorts, Instagram Reels, and TikTok videos in minutes.  
Simply enter a topic → AI writes a script → generates voiceover → fetches relevant images → compiles a ready‑to‑publish 9:16 video.

---

## ✨ Features
- 🧠 **AI Script Writing** (Groq – Llama 3.3 70B) – emotional, engaging stories with hook, body & CTA.
- 🎤 **Neural Voiceovers** (Edge‑TTS / ElevenLabs) – natural‑sounding Hindi & English voices.
- 🖼️ **Smart Image Fetching** – automatic fallback between **Pexels → Pixabay → Unsplash → Pollinations** (topic‑relevant).
- 🎬 **Cloud Video Compilation** (Rendi API) – fast FFmpeg processing, no local encoding.
- ☁️ **Cloud Storage** (Cloudinary) – voice files securely stored.
- 🔐 **User Authentication** – signup/login, dashboard, video history.
- 📱 **Fully Responsive UI** – works on desktop & mobile.
- 🚀 **Ready to Deploy** – on Vercel or any FastAPI hosting.

---

## 🧰 Requirements
- Python 3.9+
- A [Vercel](https://vercel.com) account (for deployment)
- API keys (all free to start):
  - [Groq](https://console.groq.com) – free tier with generous limits
  - [Pexels](https://www.pexels.com/api/) – free, 200 req/hour
  - [Pixabay](https://pixabay.com/api/docs/) – free, 100 req/min
  - [Unsplash](https://unsplash.com/developers) – free, 50 req/hour (production 5k/hour)
  - [Cloudinary](https://cloudinary.com) – free tier for voice storage
  - [Rendi](https://rendi.dev) – free trial (60 sec FFmpeg processing)
  - [Supabase](https://supabase.com) – free for PostgreSQL + auth
  - (Optional) ElevenLabs – fallback for voice
  - (Optional) Pollinations – last‑resort image fallback

---

## 📦 Installation

### 1. Clone the repository
```bash
git clone https://github.com/syedhamail/starfilm.git
cd starfilm
```

### 2. Create virtual environment & install dependencies
```bash
python -m venv venv
source venv/bin/activate   # Mac/Linux
venv\Scripts\activate      # Windows

pip install -r requirements.txt
```

### 3. Set up environment variables
Create a `.env` file in the project root with the following keys:

```ini
# --- Supabase ---
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key

# --- Rendi (FFmpeg) ---
RENDI_API_KEY=your_rendi_api_key

# --- Cloudinary (voice storage) ---
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_cloud_api_key
CLOUDINARY_API_SECRET=your_cloud_api_secret

# --- Image APIs ---
PEXELS_API_KEY=your_pexels_api_key
PIXABAY_API_KEY=your_pixabay_api_key
UNSPLASH_ACCESS_KEY=your_unsplash_access_key
# POLLINATIONS_API_KEY=optional

# --- Groq (AI script) ---
GROQ_API_KEY=your_groq_api_key

# --- Email (Gmail) ---
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_app_password

# --- Admin (optional) ---
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=admin_password

# --- Optional fallbacks ---
ELEVENLABS_API_KEY=your_elevenlabs_key
```

### 4. Run the app locally
```bash
python main.py
```
Open [http://localhost:8000](http://localhost:8000)

---

## 🔑 Where to get the API keys (all free)

| Service | Purpose | How to get | Free limits |
|---------|---------|------------|--------------|
| **Groq** | AI script writing | [console.groq.com](https://console.groq.com) → create API key | 30‑50 requests/min |
| **Rendi** | FFmpeg video compilation | [rendi.dev](https://app.rendi.dev) → sign up → API keys | 60 sec processing time |
| **Cloudinary** | Voice file storage | [cloudinary.com](https://cloudinary.com) → dashboard → account details | 25 credits/month |
| **Supabase** | Database & auth | [supabase.com](https://supabase.com) → new project → get URL & anon key | 500 MB database |
| **Pexels** | Stock images (priority 1) | [pexels.com/api](https://www.pexels.com/api) → create app | 200 req/hour |
| **Pixabay** | Stock images (priority 2) | [pixabay.com/api/docs](https://pixabay.com/api/docs) → API key | 100 req/min |
| **Unsplash** | Stock images (priority 3) | [unsplash.com/developers](https://unsplash.com/developers) → register app | 50 req/hour (can be raised) |
| **Gmail** | Send password reset emails | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) | free with your Gmail |

---

## 📂 Project Structure
```
starfilm/
├── main.py                 # FastAPI backend
├── requirements.txt        # Python dependencies
├── .env                    # API keys (not in repo)
├── static/                 # Frontend files
│   ├── index.html
│   ├── generate.html
│   ├── dashboard.html
│   ├── login.html
│   ├── about.html
│   ├── contact-us.html
│   ├── faq.html
│   ├── privacy-policy.html
│   ├── terms-conditions.html
│   └── blog/*.html
└── vercel.json             # Deployment config
```

---

## 🚀 Deployment on Vercel

1. Push your code to a GitHub repository.
2. Go to [vercel.com](https://vercel.com) → Import Project → select your repo.
3. **Environment Variables** – add all keys from your `.env` file.
4. Set **Build Command** to `pip install -r requirements.txt`
5. Set **Output Directory** to leave blank.
6. Deploy! Vercel automatically runs `main.py` as a serverless function.

> ⚠️ Note: Rendi and Cloudinary will work normally on Vercel. The free tier of Vercel may have cold starts, but video generation remains functional.

---

## 🧪 How to generate a video

1. **Sign up / Log in** (users are required for privacy).
2. On the `/generate` page, enter:
   - **Topic / Story Idea** (e.g., *A little girl saved by an AI*)
   - **Niche** (e.g., *AI Emotional Story Shorts*)
   - **Video Format** (YouTube Shorts / Reel / TikTok)
   - **Voice Language** (Hindi / English – male/female)
3. Click **Generate Video**.
4. The process takes **1-2 minutes** (most steps are async).
5. When ready, you can:
   - Download the MP4 (9:16 vertical)
   - Copy the YouTube title, description, and tags
   - View the full script in tabs (Hook, Body, CTA)

---

## 🛠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| **FFmpeg timeout on Rendi** | The free tier only allows 60 seconds of processing. Reduce the number of images or upgrade your Rendi plan. |
| **No images appear** | Check your Pexels/Pixabay/Unsplash API keys. The system will fall back to placeholders if all fail. |
| **Voice is too short** | The script prompt was tightened to produce 20‑30 seconds of speech. If still short, check Groq API usage. |
| **Login redirect loop** | Clear browser cookies or restart the server. |
| **Video not 9:16** | Our FFmpeg command forces `scale=1080:1920` – it stretches to fill. No black bars. |

---

## 📜 License
This project is open‑source under the MIT License.

---

## 👨‍💻 Credits
**Made by Syed Hamail Mohi Uddin Qazi**  
Built with FastAPI, Groq, Edge‑TTS, Cloudinary, Rendi, Supabase, and the free image APIs.  
For questions: [hamailsyed139@gmail.com](mailto:hamailsyed139@gmail.com)

---

## 🌟 Support
If you find this project useful, give it a ⭐⭐⭐⭐⭐ on GitHub!
