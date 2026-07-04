import os
import re
import json
import uuid
import requests
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Pinterest Download API")

# ── Auth ──────────────────────────────────────────────────────────────────────
API_PASSWORD = "SHUVO-apis"

# ── Cookies ───────────────────────────────────────────────────────────────────
_cookies_file = os.path.join(os.path.dirname(__file__), "cookies.json")
with open(_cookies_file) as _f:
    COOKIES = json.load(_f)

# ── Downloads directory ────────────────────────────────────────────────────────
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── HTTP session ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "Accept": "application/json, text/javascript, */*, q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "X-Pinterest-PWS-Handler": "www/search/[scope].js",
    "Referer": "https://www.pinterest.com/",
}
if "csrftoken" in COOKIES:
    HEADERS["X-CSRFToken"] = COOKIES["csrftoken"]

SESSION = requests.Session()
SESSION.cookies.update(COOKIES)
SESSION.headers.update(HEADERS)


# ── Auth dependency ───────────────────────────────────────────────────────────
def check_auth(x_api_key: str = Header(default=None)):
    if x_api_key != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


# ── Request models ────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    type: str = "image"   # "image" or "video"
    limit: int = 10


class UrlRequest(BaseModel):
    url: str


# ── Health / root ─────────────────────────────────────────────────────────────
@app.get("/api/healthz")
@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "Pinterest Download API",
        "auth": "Header X-API-Key required on all /api/* routes",
        "endpoints": {
            "search (/pin, /pinv)": {
                "method": "POST",
                "path": "/api/search",
                "body": {"query": "string", "type": "image | video", "limit": 10},
            },
            "download (/download)": {
                "method": "POST",
                "path": "/api/download",
                "body": {"url": "pin page link OR direct image/video link"},
            },
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
def _file_response_with_cleanup(filepath, media_type, filename):
    cleanup = BackgroundTasks()
    cleanup.add_task(lambda p=filepath: os.remove(p) if os.path.exists(p) else None)
    return FileResponse(filepath, media_type=media_type, filename=filename, background=cleanup)


def _pin_data_extract(pin):
    """Pull best-quality image/video url out of a raw pin dict."""
    pin_id = pin.get("id")
    title = pin.get("title") or pin.get("grid_title") or ""
    pin_url = f"https://www.pinterest.com/pin/{pin_id}/"

    image_url = None
    images = pin.get("images") or {}
    for key in ("orig", "736x", "474x"):
        if key in images:
            image_url = images[key].get("url")
            break

    video_url = None
    videos = pin.get("videos") or {}
    video_list = videos.get("video_list") or {}
    if video_list:
        mp4_keys = [k for k in video_list if "HLS" not in k.upper()]
        hls_keys = [k for k in video_list if "HLS" in k.upper()]
        candidates = {k: video_list[k] for k in (mp4_keys or hls_keys)}
        if candidates:
            best = sorted(candidates.values(), key=lambda v: v.get("width", 0), reverse=True)
            video_url = best[0].get("url")

    return {
        "pin_id": pin_id,
        "title": title,
        "pin_url": pin_url,
        "image_url": image_url,
        "video_url": video_url,
    }


def _search_pins(query, want_video, limit):
    results = []
    bookmark = None
    pages_tried = 0
    scope = "videos" if want_video else "pins"

    while len(results) < limit and pages_tried < 10:
        pages_tried += 1

        bookmarks = [bookmark] if bookmark else [""]
        options = {"query": query, "scope": scope, "bookmarks": bookmarks}
        data = {"options": options, "context": {}}
        source_url = f"/search/pins/?q={requests.utils.quote(query)}&rs=typed"
        params = {"source_url": source_url, "data": json.dumps(data)}

        try:
            res = SESSION.get(
                "https://www.pinterest.com/resource/BaseSearchResource/get/",
                params=params,
                timeout=20,
            )
            payload = res.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Pinterest search failed: {e}")

        resource_response = payload.get("resource_response", {})
        data_obj = resource_response.get("data", {}) or {}
        pins = data_obj.get("results", []) or []
        bookmark = data_obj.get("bookmark")

        if not pins:
            break

        for pin in pins:
            if not isinstance(pin, dict):
                continue
            item = _pin_data_extract(pin)
            if want_video and not item["video_url"]:
                continue
            if not want_video and not item["image_url"]:
                continue
            results.append(item)
            if len(results) >= limit:
                break

        if not bookmark:
            break

    return results[:limit]


def _resolve_pin_url(pin_page_url):
    """
    Given a pinterest.com/pin/<id>/ URL extract the best media.
    Returns: (media_url, is_video, fetch_mode)
    """
    import yt_dlp

    m = re.search(r"(https?://(?:www\.)?pinterest\.[a-z]+/pin/\d+/)", pin_page_url)
    clean_url = m.group(1) if m else pin_page_url

    # Try yt-dlp (video pins)
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
        formats = info.get("formats") or []
        video_fmts = [f for f in formats if f.get("height") and f.get("url")]
        if video_fmts:
            best = sorted(video_fmts, key=lambda f: f.get("height", 0), reverse=True)[0]
            return best["url"], True, "hls"
    except Exception:
        pass

    # Fallback: image from HTML
    try:
        page = SESSION.get(clean_url, timeout=20)
        images = re.findall(
            r'https://i\.pinimg\.com/(?:originals|736x|474x)/[^\s"\\]+\.(?:jpg|jpeg|png|webp|gif)',
            page.text,
        )
        if images:
            return images[0], False, "direct"
    except Exception:
        pass

    return None, False, None


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/api/search", dependencies=[Depends(check_auth)])
def search(req: SearchRequest):
    want_video = req.type == "video"
    limit = max(1, min(req.limit, 100))
    results = _search_pins(req.query, want_video, limit)
    return {"query": req.query, "type": req.type, "count": len(results), "results": results}


@app.post("/api/download", dependencies=[Depends(check_auth)])
def download(req: UrlRequest):
    import yt_dlp

    url = req.url.strip()

    # ---- Pinterest pin page or short link ----
    if "pinterest.com/pin/" in url or "pin.it/" in url:
        # Resolve pin.it short links
        if "pin.it/" in url:
            try:
                r = SESSION.get(url, allow_redirects=True, timeout=15)
                url = r.url
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Could not resolve short link: {e}")

        media_url, is_video, fetch_mode = _resolve_pin_url(url)
        if not media_url:
            raise HTTPException(status_code=400, detail="No downloadable media found on this pin")

        file_id = str(uuid.uuid4())

        if is_video and fetch_mode == "hls":
            filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "outtmpl": filepath,
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            }
            m = re.search(r"(https?://(?:www\.)?pinterest\.[a-z]+/pin/\d+/)", url)
            clean_url = m.group(1) if m else url
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([clean_url])
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Video download failed: {e}")
            return _file_response_with_cleanup(filepath, "video/mp4", f"pinterest_{file_id}.mp4")

        # Image — direct download
        try:
            r = SESSION.get(media_url, stream=True, timeout=60)
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Image download failed: {e}")

        ext = media_url.split(".")[-1].split("?")[0][:4] or "jpg"
        if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
            ext = "jpg"
        filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.{ext}")
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        media_type = f"image/{'jpeg' if ext == 'jpg' else ext}"
        return _file_response_with_cleanup(filepath, media_type, f"pinterest_{file_id}.{ext}")

    # ---- Direct media URL ----
    is_video = bool(re.search(r"\.(mp4|m3u8)(\?|$)", url, re.IGNORECASE))
    try:
        r = SESSION.get(url, stream=True, timeout=60)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")

    ext = "mp4" if is_video else (url.split(".")[-1].split("?")[0][:4] or "jpg")
    if ext not in ("mp4", "jpg", "jpeg", "png", "webp", "gif"):
        ext = "mp4" if is_video else "jpg"

    file_id = str(uuid.uuid4())
    filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.{ext}")
    with open(filepath, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)

    media_type = "video/mp4" if is_video else f"image/{'jpeg' if ext == 'jpg' else ext}"
    return _file_response_with_cleanup(filepath, media_type, f"pinterest_{file_id}.{ext}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
