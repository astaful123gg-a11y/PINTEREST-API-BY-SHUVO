# Pinterest Download API (fixed)

## What was fixed
- Search was reading the wrong response shape (`resource_response.data` as a list),
  Pinterest actually nests it as `resource_response.data.results`. Fixed.
- Search scope now switches to `"videos"` when searching for video pins.
- Video pin downloads now resolve through `yt-dlp` (Pinterest wraps videos in HLS),
  image pins download directly.
- Auth simplified back to hardcoded (no required env vars — same as TikTok/YouTube APIs).
- Downloaded files auto-delete after being sent (no disk buildup on Render).

## Auth
All `/api/*` calls need header:
```
X-API-Key: SHUVO-apis
```

## Endpoints
- `GET /` — index of endpoints (no auth, safe for UptimeRobot)
- `POST /api/search` — `{"query": "naruto", "type": "image"|"video", "limit": 10}` (max 100)
- `POST /api/download` — `{"url": "..."}` — pin page link, `pin.it` short link, or direct image/video URL

## Setup
1. `pip install -r requirements.txt`
2. `cookies.json` already included.
3. Run: `python main.py`

## Deploy on Render
Push to a **private** repo (cookies.json has session tokens).
Build: `pip install -r requirements.txt`
Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Note
Scraping Pinterest's internal (unofficial) API — if Pinterest changes their internal
endpoint shape again or the session expires, re-export cookies.json the same way as before.
