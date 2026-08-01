import os
import re
import requests
from urllib.parse import urljoin, quote, unquote
from flask import Flask, Response, request, stream_with_context

app = Flask(__name__)

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://thinkkast.dpdns.org/",
    "Origin": "https://thinkkast.dpdns.org",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

def _get_base_url():
    return os.environ.get("PUBLIC_BASE_URL", "http://chrtv.duckdns.org:55801")

def add_cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS, HEAD"
    r.headers["Access-Control-Allow-Headers"] = "*"
    return r

def parse_m3u_file(file_path="playlist.m3u"):
    channels = []
    if not os.path.exists(file_path):
        return channels

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    current_channel = {}
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            tvg_id = re.search(r'tvg-id="([^"]*)"', line)
            tvg_name = re.search(r'tvg-name="([^"]*)"', line)
            tvg_logo = re.search(r'tvg-logo="([^"]*)"', line)
            tvg_chno = re.search(r'tvg-chno="([^"]*)"', line)
            group_title = re.search(r'group-title="([^"]*)"', line)
            
            name_match = line.split(",")[-1].strip()

            current_channel = {
                "id": tvg_id.group(1) if tvg_id else (tvg_name.group(1) if tvg_name else name_match),
                "name": name_match,
                "logo": tvg_logo.group(1) if tvg_logo else "",
                "chno": tvg_chno.group(1) if tvg_chno else "",
                "group": group_title.group(1) if group_title else "Chung",
            }
        elif line and not line.startswith("#"):
            if current_channel:
                current_channel["url"] = line
                channels.append(current_channel)
                current_channel = {}

    return channels

@app.route("/")
def home():
    return f"chrtv IPTV Engine Active! Base URL: {_get_base_url()}", 200

@app.route("/playlist.m3u", methods=["GET", "OPTIONS"])
def get_playlist():
    if request.method == "OPTIONS":
        return add_cors(Response(status=204))

    base_url = _get_base_url()
    channels = parse_m3u_file("playlist.m3u")
    
    m3u_lines = ["#EXTM3U refresh=\"1800\""]
    for idx, ch in enumerate(channels, 1):
        chno = ch.get("chno") or str(idx)
        m3u_lines.append(
            f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" tvg-logo="{ch["logo"]}" tvg-chno="{chno}" group-title="{ch["group"]}",{ch["name"]}'
        )
        m3u_lines.append(f'{base_url}/stream/play?url={quote(ch["url"])}')

    return add_cors(Response("\n".join(m3u_lines).strip(), mimetype="application/x-mpegurl"))

@app.route("/stream/play", methods=["GET", "OPTIONS"])
def stream_play():
    if request.method == "OPTIONS":
        return add_cors(Response(status=204))

    target_url = request.args.get("url")
    if not target_url:
        return add_cors(Response("Missing URL", status=400))

    u = unquote(target_url)
    h = HEADERS_DEFAULT.copy()

    if "103.152.216.26" in u:
        h["Host"] = "103.152.216.26:8443"

    try:
        rs = requests.get(u, headers=h, timeout=12, stream=True, allow_redirects=True)
        if rs.status_code != 200:
            return add_cors(Response(f"Err HTTP {rs.status_code}", status=rs.status_code))

        ct = rs.headers.get("Content-Type", "")
        if ".m3u8" in u or "m3u8" in ct or rs.text.strip().startswith("#EXTM3U"):
            rw = []
            for line in rs.text.splitlines():
                if line and not line.startswith("#"):
                    abs_url = urljoin(rs.url, line)
                    rw.append(f"/stream/play?url={quote(abs_url)}")
                else:
                    rw.append(line)
            
            res = Response("\n".join(rw).strip(), status=200, mimetype="application/x-mpegurl")
            res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return add_cors(res)
        else:
            res = Response(stream_with_context(rs.iter_content(chunk_size=16384)), status=200, mimetype="video/mp2t")
            return add_cors(res)

    except Exception as e:
        return add_cors(Response(f"Exception: {str(e)}", status=502))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)