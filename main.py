import os
import re
import json
import uuid
import glob
import requests
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Pinterest Download API")

API_PASSWORD = "SHUVO-apis"
COOKIES_JSON = os.path.join(os.path.dirname(__file__), "cookies.json")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

with open(COOKIES_JSON) as f:
    COOKIES = json.load(f)

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


def check_auth(x_api_key: str = Header(default=None)):
    if x_api_key != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


class SearchRequest(BaseModel):
    query: str
    type: str = "image"   # "image" or "video"
    limit: int = 10


class UrlRequest(BaseModel):
    url: str


@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "Pinterest Download API",
        "auth": "Header X-API-Key: SHUVO-apis (required on all /api/* routes)",
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


def _pin_data_extract(pin):
    """Pull best-quality image/video url out of a raw pin dict from Pinterest's API."""
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
        # pick the highest resolution mp4
        best = sorted(video_list.values(), key=lambda v: v.get("width", 0), reverse=True)
        if best:
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

    while len(results) < limit and pages_tried < 10:
        pages_tried += 1
        options = {"query": query, "scope": "pins", "bookmarks": [bookmark] if bookmark else [""]}
        data = {"options": options, "context": {}}
        source_url = f"/search/pins/?q={requests.utils.quote(query)}"

        params = {
            "source_url": source_url,
            "data": json.dumps(data),
        }

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
        pins = resource_response.get("data", []) or []
        if not pins:
            break

        for pin in pins:
            if not isinstance(pin, dict) or pin.get("type") != "pin":
                continue
            item = _pin_data_extract(pin)
            if want_video and not item["video_url"]:
                continue
            if not want_video and not item["image_url"]:
                continue
            results.append(item)
            if len(results) >= limit:
                break

        bookmark = resource_response.get("bookmark")
        if not bookmark:
            break

    return results[:limit]


@app.post("/api/search", dependencies=[Depends(check_auth)])
def search(req: SearchRequest):
    want_video = req.type == "video"
    limit = max(1, min(req.limit, 100))
    results = _search_pins(req.query, want_video, limit)
    return {"query": req.query, "type": req.type, "count": len(results), "results": results}


def _resolve_pin_page(url):
    """Given a pinterest.com/pin/<id>/ link, fetch full pin data via PinResource."""
    m = re.search(r"/pin/(\d+)", url)
    if not m:
        return None
    pin_id = m.group(1)

    options = {"id": pin_id, "field_set_key": "detailed"}
    data = {"options": options, "context": {}}
    params = {"source_url": f"/pin/{pin_id}/", "data": json.dumps(data)}

    try:
        res = SESSION.get(
            "https://www.pinterest.com/resource/PinResource/get/",
            params=params,
            timeout=20,
        )
        payload = res.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Pinterest pin lookup failed: {e}")

    pin = payload.get("resource_response", {}).get("data")
    if not pin:
        return None
    return _pin_data_extract(pin)


@app.post("/api/download", dependencies=[Depends(check_auth)])
def download(req: UrlRequest):
    url = req.url.strip()
    media_url = None
    is_video = False

    if "pinterest.com/pin/" in url or "pin.it/" in url:
        # follow short links (pin.it) first
        if "pin.it/" in url:
            try:
                r = SESSION.get(url, allow_redirects=True, timeout=15)
                url = r.url
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Could not resolve short link: {e}")

        item = _resolve_pin_page(url)
        if not item:
            raise HTTPException(status_code=400, detail="Could not resolve pin")
        if item["video_url"]:
            media_url = item["video_url"]
            is_video = True
        elif item["image_url"]:
            media_url = item["image_url"]
        else:
            raise HTTPException(status_code=400, detail="No downloadable media found on this pin")
    else:
        # assume it's already a direct media url (i.pinimg.com / v1.pinimg.com etc.)
        media_url = url
        is_video = bool(re.search(r"\.(mp4|m3u8)(\?|$)", url, re.IGNORECASE))

    try:
        r = SESSION.get(media_url, stream=True, timeout=60)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")

    ext = "mp4" if is_video else (media_url.split(".")[-1].split("?")[0][:4] or "jpg")
    if ext not in ("mp4", "jpg", "jpeg", "png", "webp", "gif"):
        ext = "mp4" if is_video else "jpg"

    file_id = str(uuid.uuid4())
    filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.{ext}")
    with open(filepath, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)

    media_type = "video/mp4" if is_video else f"image/{ext if ext != 'jpg' else 'jpeg'}"
    return FileResponse(filepath, media_type=media_type, filename=f"pinterest_{file_id}.{ext}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
