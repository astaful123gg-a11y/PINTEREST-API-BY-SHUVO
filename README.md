# Pinterest Download API

Uses Pinterest's internal (unofficial) search/pin endpoints with your logged-in
session cookies — no official public API for this exists, so this mimics what
the Pinterest website itself calls.

## Auth
All `/api/*` calls need header:
```
X-API-Key: SHUVO-apis
```

## /pin, /pinv -> search
`POST /api/search`
```json
{"query": "naruto", "type": "image", "limit": 10}
```
`type: "video"` → only pins that have a video attached, for `/pinv`.
`limit` can go up to 100 (paginates automatically behind the scenes).

Response:
```json
{
  "query": "naruto", "type": "image", "count": 10,
  "results": [
    {"pin_id": "...", "title": "...", "pin_url": "...", "image_url": "...", "video_url": null}
  ]
}
```

## /download -> any pin link or direct media link
`POST /api/download`
```json
{"url": "https://www.pinterest.com/pin/123456789/"}
```
Also works with:
- Short links (`pin.it/...`) — auto-resolved
- Direct image/video URLs (`i.pinimg.com/...`, `v1.pinimg.com/...`) — downloaded as-is

Returns the actual image or video file.

## Setup
1. `pip install -r requirements.txt`
2. `cookies.json` already included (converted from your Pinterest session cookies).
3. Run: `python main.py`

## Deploy on Render
Push to a **private** repo (cookies.json has session tokens).
Build: `pip install -r requirements.txt`
Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Note
This is scraping Pinterest's internal API, not an official product — if Pinterest
changes their internal endpoint structure or your session expires, search/download
may start failing and cookies.json will need re-exporting (same as we did for
TikTok/YouTube cookies).
