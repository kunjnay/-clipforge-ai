# ClipForge Free — launch and test checklist

## 1. Install local tools

- [ ] Python 3.11+
- [ ] FFmpeg + FFprobe
- [ ] `pip install -r requirements.txt`
- [ ] Copy `.env.example` to `.env`
- [ ] Set `APP_PASSWORD` before exposing the dashboard through a public tunnel

## 2. Start the app

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` and confirm yt-dlp, Faster-Whisper and FFmpeg show READY.

## 3. Test one source first

1. Paste one public YouTube URL that you own or are authorised to repurpose.
2. Set the requested clip count to `1`.
3. Choose a short target duration and click **Analyse link & create clips**.
4. Wait for READY; inspect any error if processing fails.
5. Review the rendered MP4, audio, crop and subtitle timings in Clip Library.
6. Try **Schedule BOTH** with a date/time a few minutes in the future. This creates one Instagram and one YouTube queue entry but does not publish unless those integrations are connected.

## 4. Test a multi-clip batch

- Set `requested clips` to 3 or 5 and use a sufficiently long source.
- Confirm the outputs cover different, non-overlapping sections.
- Open **Schedule all to both platforms** and verify it creates exactly two schedule rows per clip.
- Only use 30 after reviewing a smaller batch. A short source may produce fewer clips than requested.

## 5. Optional publishing setup

- YouTube: Google Cloud project + YouTube Data API v3 + OAuth credentials.
- Instagram: Instagram Professional account linked to a Facebook Page + Meta developer app + publishing permissions + public HTTPS media URL.
- For a no-cost temporary HTTPS URL during tests, run `cloudflared tunnel --url http://127.0.0.1:8000`, copy its generated URL into `APP_BASE_URL`, and restart ClipForge. Keep the tunnel process running. Quick Tunnels are temporary and not production hosting.
- Keep the computer awake and ClipForge running for scheduled posts.

## 6. Costs and safety

- ClipForge does not use a paid clipping or AI API.
- Local models run on your machine; first-time model download requires internet data and disk space.
- Platform APIs may apply quotas, reviews, permissions and account limits.
- Only download/edit/republish videos you own or have permission to use. Public links can still be subject to platform terms or rights restrictions.
- Keep `.env`, `data/`, source files, rendered media, tokens and model caches out of GitHub.
