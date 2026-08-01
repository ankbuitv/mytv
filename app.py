import os
import re
import requests
from urllib.parse import urljoin, quote, unquote
from flask import Flask, Response, request, stream_with_context, jsonify, render_template_string

app = Flask(__name__)

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://thinkkast.dpdns.org/",
    "Origin": "https://thinkkast.dpdns.org",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

def _get_base_url():
    return os.environ.get("PUBLIC_BASE_URL", "http://chrtv.duckdns.org:14211")

def add_cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS, HEAD, POST, DELETE"
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

def save_m3u_file(channels, file_path="playlist.m3u"):
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" tvg-logo="{ch["logo"]}" tvg-chno="{ch.get("chno","")}" group-title="{ch["group"]}",{ch["name"]}')
        lines.append(ch["url"])
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# ----------------- ADMIN DASHBOARD (FRONTEND) -----------------
HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>chrtv IPTV Manager</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8">
    <div class="max-w-6xl mx-auto space-y-6">
        
        <!-- Header -->
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl gap-4">
            <div>
                <h1 class="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-blue-500 flex items-center gap-3">
                    <i class="fa-solid fa-tv text-cyan-400"></i> chrtv Manager
                </h1>
                <p class="text-slate-400 text-sm mt-1">Quản lý danh sách kênh IPTV chuyên nghiệp</p>
            </div>
            <div class="bg-slate-950 px-4 py-2 rounded-xl border border-slate-800 text-xs font-mono text-cyan-400">
                Playlist URL: <span id="playlistUrl" class="text-white select-all"></span>
            </div>
        </div>

        <!-- Add Channel Form -->
        <div class="bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl">
            <h2 class="text-lg font-bold text-slate-200 mb-4 flex items-center gap-2">
                <i class="fa-solid fa-circle-plus text-emerald-400"></i> Thêm Kênh Mới
            </h2>
            <form id="addForm" class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <input type="text" id="name" placeholder="Tên Kênh (Ví dụ: HBO HD)" required class="bg-slate-900 border border-slate-700 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-cyan-500">
                <input type="text" id="group" placeholder="Nhóm (Ví dụ: Giai Tri)" required class="bg-slate-900 border border-slate-700 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-cyan-500">
                <input type="text" id="logo" placeholder="URL Logo (Tùy chọn)" class="bg-slate-900 border border-slate-700 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-cyan-500">
                <input type="url" id="url" placeholder="URL Stream (m3u8...)" required class="bg-slate-900 border border-slate-700 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-cyan-500 md:col-span-3">
                <button type="submit" class="bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 hover:to-teal-700 text-white font-semibold py-2 px-6 rounded-xl transition shadow-lg flex items-center justify-center gap-2">
                    <i class="fa-solid fa-plus"></i> Thêm
                </button>
            </form>
        </div>

        <!-- Channel List Table -->
        <div class="bg-slate-800 rounded-2xl border border-slate-700 shadow-xl overflow-hidden">
            <div class="p-6 border-b border-slate-700 flex justify-between items-center">
                <h2 class="text-lg font-bold text-slate-200 flex items-center gap-2">
                    <i class="fa-solid fa-list text-cyan-400"></i> Danh Sách Kênh (<span id="count">0</span>)
                </h2>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-950/50 text-slate-400 text-xs uppercase tracking-wider">
                            <th class="p-4">Logo</th>
                            <th class="p-4">Tên Kênh</th>
                            <th class="p-4">Nhóm</th>
                            <th class="p-4">Link Stream Nguồn</th>
                            <th class="p-4 text-center">Thao tác</th>
                        </tr>
                    </thead>
                    <tbody id="channelTable" class="divide-y divide-slate-700/50 text-sm">
                        <!-- Loaded dynamically -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        const baseUrl = window.location.origin;
        document.getElementById('playlistUrl').innerText = `${baseUrl}/playlist.m3u`;

        async function loadChannels() {
            const res = await fetch('/api/channels');
            const data = await res.json();
            document.getElementById('count').innerText = data.length;
            const tbody = document.getElementById('channelTable');
            tbody.innerHTML = data.map((c, i) => `
                <tr class="hover:bg-slate-700/30 transition">
                    <td class="p-4">
                        ${c.logo ? `<img src="${c.logo}" class="w-10 h-10 object-contain rounded bg-slate-900 border border-slate-700">` : `<div class="w-10 h-10 rounded bg-slate-900 flex items-center justify-center text-slate-600"><i class="fa-solid fa-tv"></i></div>`}
                    </td>
                    <td class="p-4 font-semibold text-slate-100">${c.name}</td>
                    <td class="p-4"><span class="px-2.5 py-1 bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 rounded-lg text-xs font-medium">${c.group}</span></td>
                    <td class="p-4 font-mono text-xs text-slate-400 max-w-xs truncate">${c.url}</td>
                    <td class="p-4 text-center">
                        <button onclick="deleteChannel(${i})" class="p-2 text-rose-400 hover:bg-rose-500/10 rounded-lg transition" title="Xóa kênh">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </td>
                </tr>
            `).join('');
        }

        document.getElementById('addForm').onsubmit = async (e) => {
            e.preventDefault();
            const payload = {
                name: document.getElementById('name').value,
                group: document.getElementById('group').value,
                logo: document.getElementById('logo').value,
                url: document.getElementById('url').value
            };
            await fetch('/api/channels', {
                method: 'POST',
                headers: {'Content-Type': 'json'},
                body: JSON.stringify(payload)
            });
            document.getElementById('addForm').reset();
            loadChannels();
        };

        async function deleteChannel(index) {
            if(confirm('Xóa kênh này khỏi danh sách?')) {
                await fetch(`/api/channels/${index}`, {method: 'DELETE'});
                loadChannels();
            }
        }

        loadChannels();
    </script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(HTML_DASHBOARD)

# ----------------- REST APIs FOR DASHBOARD -----------------
@app.route("/api/channels", methods=["GET"])
def get_channels_api():
    return jsonify(parse_m3u_file("playlist.m3u"))

@app.route("/api/channels", methods=["POST"])
def add_channel_api():
    data = request.json
    channels = parse_m3u_file("playlist.m3u")
    channels.append({
        "id": data.get("name", "").lower().replace(" ", "_"),
        "name": data.get("name", ""),
        "logo": data.get("logo", ""),
        "chno": str(len(channels) + 1),
        "group": data.get("group", "Chung"),
        "url": data.get("url", "")
    })
    save_m3u_file(channels)
    return jsonify({"status": "ok"})

@app.route("/api/channels/<int:index>", methods=["DELETE"])
def delete_channel_api(index):
    channels = parse_m3u_file("playlist.m3u")
    if 0 <= index < len(channels):
        channels.pop(index)
        save_m3u_file(channels)
    return jsonify({"status": "ok"})

# ----------------- STREAM & PLAYLIST ROUTE -----------------
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