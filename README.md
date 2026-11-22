# ClipForge Free — One YouTube Link to Instagram Reels + YouTube Shorts

**One YouTube video URL → up to 30 distinct edited clips per India-time calendar day → the same clips can be scheduled to both Instagram Reels and YouTube Shorts.**

ClipForge runs on your own computer. It uses open-source tools for downloading publicly accessible videos, local speech recognition and editing. There is no Klap account, no OpenAI key, no paid clipping API, no paid AI model API and no required paid hosting service.

## The exact workflow

1. Paste **one YouTube video / Shorts / replay URL**. You do not upload a video file.
2. Choose how many clips to make: 1–30 per batch. Submit batches as often as you want.
3. Choose clip duration, output format, crop, caption look, caption position, words per caption burst, audio normalisation and an editing brief.
4. ClipForge downloads the public source using `yt-dlp`, transcribes it on your computer using Faster-Whisper and scores different timeline windows locally.
5. FFmpeg renders each selected highlight as a separate MP4. The selector avoids reusing the same section; shorter sources may produce fewer clips than requested.
6. Review and edit each result in Clip Library.
7. Choose **Schedule all to both platforms**. Each clip creates two matching schedule entries: one Instagram Reel and one YouTube Short. The same clip and caption are used for both unless you edit them.
8. Keep ClipForge, your computer and (for Instagram) your public HTTPS tunnel running at the planned times. Connected official accounts will publish the posts automatically.

A batch of 30 completed clips therefore creates **30 Instagram posts + 30 YouTube posts = 60 scheduled posts**. ClipForge imposes no daily clip or scheduling cap. You can choose a longer interval to spread a batch across multiple days.

## What “completely free” means

- No paid clipping provider, subscription, OpenAI API, Klap API or paid cloud AI is part of this project.
- `yt-dlp`, Faster-Whisper, FFmpeg, FastAPI and SQLite are open-source tools.
- Speech-to-text and clip selection run locally. The selector is a transcript-scoring algorithm, not a cloud LLM.
- YouTube and Instagram auto-publishing use their official platform APIs. The project does not charge for them; platform quotas, permissions, developer-app requirements and account eligibility still apply.
- Instagram's API needs Meta to fetch the finished MP4 from a public HTTPS URL. A free Cloudflare Quick Tunnel can provide a temporary URL for testing, but Cloudflare says Quick Tunnels are for testing/development and have no uptime guarantee.
- Your computer supplies the CPU/GPU, storage, internet data and electricity. The first Faster-Whisper model download uses internet data and disk space. These are resource costs, not ClipForge API charges.

**There is no paid plan hidden in the code.** Do not add a paid Render, Klap, OpenAI, or other service to use the local editing flow.

## Limits

- No ClipForge-imposed daily clip cap. Each processing batch requests up to 30 clips; submit additional batches as desired.
- Each submitted YouTube source batch can request up to 30 clips.
- Requested clips are temporarily reserved while a job runs. If the job fails, the reservation is released. If a shorter source produces fewer clips, only the clips actually rendered count against the quota.
- No ClipForge-imposed daily scheduling cap. Cross-posting one clip creates one Instagram slot and one YouTube slot; platform quotas and anti-spam restrictions still apply.

## Important limitations and permissions

- Process only videos you own or have permission to download, edit and republish. Downloading a YouTube video may be restricted by YouTube's terms or the rights holder's terms even when a link is public. Check the applicable terms, copyright, music rights and permissions first.
- Only publicly accessible YouTube links are attempted. Private, age-restricted, members-only, login-gated, DRM-protected or otherwise restricted videos may fail. ClipForge does not read browser cookies, request account passwords, use account sessions or bypass access controls.
- YouTube can change its video delivery format. If extraction stops working, update `yt-dlp` and retry.
- The selector is an offline heuristic, not a human editor or a generative AI model. It looks for speech density, complete sentences, question/emphasis patterns and terms from your brief. Review the clips before posting.
- Faster-Whisper is multilingual, but captions can be inaccurate with background music, noise, accents, names or overlapping speakers. Review captions.
- A source that is too short, has little speech, or does not contain enough separate moments may yield fewer clips than requested. ClipForge will not duplicate the same section just to reach 30.
- Automatic publishing requires you to set up your own Google OAuth / YouTube Data API connection and Meta Instagram publishing credentials. Without these, ClipForge still edits and lets you download videos; scheduled entries wait for the integrations.
- Instagram's publishing API requires an eligible Professional Instagram account and media accessible on a public server. The local `127.0.0.1` URL is not public. See the setup guide before enabling it.
- Keep the app running and the computer awake for scheduled publishing. A restart recovers the queue, but a network/platform outage can cause a post to fail; review the queue and retry only after checking whether the platform already published it.

## Requirements

- Python 3.11 or newer.
- FFmpeg and FFprobe installed and available on `PATH`.
- Internet access to retrieve the source and initially download the local Whisper model.
- Enough disk space for the source video, rendered clips and model cache. Long videos and 30 rendered clips can require several GB.

## Run locally (recommended and free)

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Install FFmpeg/FFprobe using a trusted package manager, for example:

```powershell
winget install Gyan.FFmpeg
```

Close and reopen PowerShell so `ffmpeg` and `ffprobe` are on `PATH`.

Edit `.env`. Keep the dashboard local unless you have configured a strong password:

```env
APP_TIMEZONE=Asia/Kolkata
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
APP_PASSWORD=choose-a-long-private-password
```

Start ClipForge:

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. The first run downloads the local `base` Whisper model and may take a while. Confirm yt-dlp, Faster-Whisper and FFmpeg show READY in Settings.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
# Install FFmpeg using your OS package manager.
uvicorn app:app --host 127.0.0.1 --port 8000
```

## First test (do this before a 30-clip batch)

1. Use a short public YouTube source that you own or are authorised to repurpose.
2. Choose 1 clip and a 20–30 second target.
3. Click **Analyse link & create clips**.
4. Wait for the job to show READY. If it fails, read the displayed reason.
5. Open Clip Library, play the MP4, check the start/end, crop, audio and captions.
6. Use **Schedule BOTH** on one clip to create one Instagram and one YouTube schedule entry. Do not use live publishing until both accounts are connected and you have checked the output.
7. Only then try a larger requested count.

## Optional automatic publishing (still no paid ClipForge API)

You can use editing, manual downloads and saved schedules without connecting any accounts. Automatic posting uses your own official developer connections.

### YouTube Shorts

1. Create a Google Cloud project and enable YouTube Data API v3.
2. Create an OAuth client for a Web application.
3. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` and `TOKEN_ENCRYPTION_KEY` in `.env`.
4. The default local callback is `http://127.0.0.1:8000/auth/youtube/callback`. Add that exact URL to the OAuth client.
5. Generate an encryption key:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Put the result in `TOKEN_ENCRYPTION_KEY`, restart ClipForge, then click **Connect / reconnect YouTube** in Settings.
6. Approve the requested YouTube upload permission for your own channel.

Google's current YouTube Data API documentation lists a separate default daily bucket of 100 `videos.insert` uploads. ClipForge has no local daily cap, but Google can change quotas or apply account/project restrictions. Do not promise an upload will succeed until the connection is tested.

### Instagram Reels

1. Use an Instagram **Professional** account (Business or Creator) connected to a Facebook Page.
2. Create/configure a Meta developer app with Instagram content-publishing permissions and access to the linked account/Page. Complete any Page Publishing Authorization that Meta requires.
3. Set `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN` and `APP_BASE_URL` in `.env`.
4. Meta must be able to fetch the generated MP4 from `APP_BASE_URL + signed media path` over public HTTPS. `localhost` does not work.

**Free temporary HTTPS tunnel for testing:** Install `cloudflared` and run:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Copy the temporary `https://....trycloudflare.com` URL it prints into `APP_BASE_URL` in `.env`, restart ClipForge, and leave the tunnel process running. The tunnel hostname changes when you create a new Quick Tunnel, so update `APP_BASE_URL` when it changes. Anyone with the URL can reach the public app endpoints; set `APP_PASSWORD` and do not share the URL. Quick Tunnels are for development/testing, not guaranteed production hosting.

Meta's publishing guide says the Reel media URL must be publicly accessible and publishes a moving 24-hour API-posting limit. ClipForge does not impose a local daily cap. Meta's own publishing quota and other platform restrictions still apply.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `./data` | Local database, source files and clips |
| `APP_TIMEZONE` | `Asia/Kolkata` | Daily clip and schedule limits |
| `WHISPER_MODEL_SIZE` | `base` | Local model (`tiny`, `base`, `small`, etc.) |
| `WHISPER_DEVICE` | `cpu` | Local inference device (`cpu` or compatible GPU) |
| `WHISPER_COMPUTE_TYPE` | `int8` | CPU memory / speed setting |
| `MAX_SOURCE_MB` | `2048` | Maximum downloaded source size |
| `MAX_CONCURRENT_JOBS` | `1` | CPU/RAM protection; 1 is recommended on ordinary PCs |
| `APP_PASSWORD` | empty | Protects the dashboard when exposed through a public tunnel |
| `TOKEN_ENCRYPTION_KEY` | empty | Encrypts the saved YouTube OAuth token in SQLite |
| `APP_BASE_URL` | empty | Public HTTPS URL required for Instagram publishing |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | empty | Optional YouTube OAuth app |
| `INSTAGRAM_USER_ID` / `INSTAGRAM_ACCESS_TOKEN` | empty | Optional Meta Instagram API credentials |

## GitHub upload checklist

- Upload the source files and folders from this ZIP into the root of your GitHub repository.
- Do **not** upload `.env`, `data/`, source videos, generated clips, OAuth tokens, model files, Python caches or `.pytest_cache`.
- `.gitignore` excludes local secrets, data, media and caches.
- Keep the repository private if you prefer. Never commit API credentials or `.media_secret`.

## Verification

Run the included automated test suite:

```bash
pip install -r requirements-dev.txt
pytest -q
```

The test suite covers the single-YouTube-link intake, no daily clip cap, multi-highlight rendering pipeline using mocked media adapters, paired cross-post scheduling, daily platform schedule caps, safe link validation, signed media URLs and a real FFmpeg render when FFmpeg is installed.

## Useful official references

- YouTube Data API upload method: https://developers.google.com/youtube/v3/docs/videos/insert
- YouTube Data API quota calculator: https://developers.google.com/youtube/data/determine_quota_cost
- Meta Instagram Content Publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing/
- Cloudflare Quick Tunnels: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

## Test status before packaging

See [TEST_REPORT.md](TEST_REPORT.md) for the exact checks completed in the build environment and the live integrations that still require your own machine and account credentials.
