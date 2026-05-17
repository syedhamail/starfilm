# 🎬 Shorts Maker — Setup Guide

## Requirements
- Python 3.9+
- FFmpeg (must be installed on your system)
- Gemini API Key (free)
- VoiceRSS API Key (free)

---

## Step 1: Project Setup

### Open VS Code
```
File → Open Folder → select the shorts-maker folder
```

### Open Terminal (Ctrl + `)
```bash
# Virtual environment banayein
python -m venv venv

# Activate karein
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# Packages install karein
pip install -r requirements.txt
```

---

## Step 2: Install FFmpeg 

### Windows:
1. Download FFmpeg from https://ffmpeg.org/download.html
2. Extract it (e.g. C:\ffmpeg)
3. Add this path to your System PATH: C:\ffmpeg\bin
4. Check in the terminal: `ffmpeg -version`

### Mac:
```bash
brew install ffmpeg
```

### Linux:
```bash
sudo apt install ffmpeg
```

---

## Step 3: Set API Keys 

Create `.env` file:

Now open the `.env` file and add your API keys:

```
GEMINI_API_KEY=your_gemini_api_key_here
VOICERSS_API_KEY=your_voicerss_api_key_here
```

### Gemini API Key — Where to Get It? (FREE)
1. Go to https://aistudio.google.com/app/apikey
2. Click "Create API Key"
3. Copy the key and paste it into your .env file

### VoiceRSS API Key — Where to Get It? (FREE - 350 requests/day)
1. Go to https://www.voicerss.org/registration.aspx
2. Create an account and verify your email
3. Copy the API key from the dashboard

---

## Step 4: App Chalayein

```bash
python main.py
```

Open in Browser:
```
http://localhost:8000
```

---

## Step 5: Generate the Video

1. The app will open in your browser
2. Enter a topic (e.g. “A dog that saved a family from fire using AI”)
3. Click the “Generate Video” button
4. The process may take around 15 minutes.
5. Your video will be ready! Use the download button to save it
6. You can also copy the YouTube title, description, and tags


---

## Troubleshooting

**FFmpeg not found error:**
- Add FFmpeg to your system PATH (check Step 2 again)

**Gemini API error:**
- Check your GEMINI_API_KEY in the .env file
- Make sure the key is active in Google AI Studio

**VoiceRSS error:**
- The free plan has a daily limit of 350 requests
- Make sure your API key is correct

**Image download slow:**
- Pollinations.ai is a free service, so it may be a bit slow
- Check your internet connection

**Faster-whisper is not working:**
- Run: `pip install faster-whisper`
- If you still get an error, run: `pip install faster-whisper --upgrade`
- A fallback SRT file will be generated automatically

---

## Folder Structure

```
youtube-shorts-maker/
├── main.py              ← Backend (FastAPI)
├── requirements.txt     ← Python packages
├── .env                 ← API keys
├── static/
│   └── admin-dashboard.html       ← Frontend UI
│   └── admin-login.html       ← Frontend UI
│   └── dashboard.html       ← Frontend UI
│   └── index.html       ← Frontend UI
│   └── login.html       ← Frontend UI
│   └── reset-password.html       ← Frontend UI
└── output/
    └── [job_id]/
        ├── voice.mp3
        ├── image1.jpg ... image10.jpg
        ├── output.mp4
        ├── final_shorts.mp4   ← FINAL VIDEO
        └── metadata.json      ← title/desc/tags
```

---

## Want to deploy in production?

You can deploy it on Railway, Render, or a VPS.
```bash
# For Render or Railway:
uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

Made with ❤️ | Made by Syed Hamail Mohi Uddin Qazi
