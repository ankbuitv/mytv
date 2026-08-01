import os
import re
import requests
from urllib.parse import urljoin, quote, unquote
from flask import Flask, Response, request, stream_with_context, jsonify, render_template_string

app = Flask(__name__)

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
                "enabled": True
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
        if ch.get("enabled", True):
            lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" tvg-logo="{ch.get("logo","")}" tvg-chno="{ch.get("chno","")}" group-title="{ch.get("group","Chung")}",{ch["name"]}')
            lines.append(ch["url"])
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# --- DASHBOARD GIAO DIỆN STYLE NEXT.JS / ERSATZTV ---
NEXTJS_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>chrtv Ersatz - IPTV Stream & Channel Manager</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
    </style>
</head>
<body class="min-h-screen flex flex-col">
    <!-- Navbar -->
    <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="bg-gradient-to-tr from-indigo-500 to-purple-500 p-2 rounded-xl text-white font-bold">
                    <i class="fa-solid fa-tower-broadcast text-lg"></i>
                </div>
                <div>
                    <span class="font-bold text-lg tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-slate-400">chrtv ErsatzTV</span>
                    <span class="text-xs px-2 py-0.5 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-full ml-2">v2.0</span>
                </div>
            </div>
            <div class="flex items-center gap-4 text-sm font-mono bg-slate-950 px-4 py-2 rounded-lg border border-slate-800 text-slate-400">
                <i class="fa-solid fa-link text-indigo-400"></i>
                <span id="m3uUrl" class="text-indigo-300 select-all"></span>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-6 py-8 flex-1 w-full space-y-6">
        
        <!-- Stats & Actions Bar -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl flex items-center justify-between">
                <div>
                    <p class="text-slate-400 text-sm font-medium">Tổng số kênh</p>
                    <h3 id="totalChannels" class="text-3xl font-bold mt-1 text-white">0</h3>
                </div>
                <div class="w-12 h-12 bg-indigo-500/10 border border-indigo-500/20 rounded-xl flex items-center justify-center text-indigo-400 text-xl">
                    <i class="fa-solid fa-tv"></i>
                </div>
            </div>

            <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl flex items-center justify-between">
                <div>
                    <p class="text-slate-400 text-sm font-medium">Trạng thái Server</p>
                    <h3 class="text-xl font-bold mt-1 text-emerald-400 flex items-center gap-2">
                        <span class="w-2.5 h-2.5 bg-emerald-500 rounded-full animate-pulse"></span> Active
                    </h3>
                </div>
                <div class="w-12 h-12 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex items-center justify-center text-emerald-400 text-xl">
                    <i class="fa-solid fa-server"></i>
                </div>
            </div>

            <!-- Import M3U Section -->
            <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl flex items-center justify-between">
                <div>
                    <p class="text-slate-400 text-sm font-medium">Nạp danh sách M3U</p>
                    <button onclick="document.getElementById('importModal').classList.remove('hidden')" class="mt-2 text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition shadow-lg flex items-center gap-2">
                        <i class="fa-solid fa-file-import"></i> Paste Nội Dung M3U
                    </button>
                </div>
                <div class="w-12 h-12 bg-purple-500/10 border border-purple-500/20 rounded-xl flex items-center justify-center text-purple-400 text-xl">
                    <i class="fa-solid fa-folder-plus"></i>
                </div>
            </div>
        </div>

        <!-- Add Single Channel Form -->
        <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl">
            <h2 class="text-md font-bold mb-4 flex items-center gap-2 text-indigo-400">
                <i class="fa-solid fa-plus-circle"></i> Thêm Kênh Thủ Công
            </h2>
            <form id="addChannelForm" class="grid grid-cols-1 md:grid-cols-5 gap-3">
                <input type="text" id="add_name" placeholder="Tên Kênh (Ví dụ: VTV3 HD)" required class="bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-indigo-500">
                <input type="text" id="add_group" placeholder="Nhóm (Giai Tri / The Thao...)" required class="bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-indigo-500">
                <input type="text" id="add_logo" placeholder="URL Logo (PNG/JPG)" class="bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-indigo-500">
                <input type="url" id="add_url" placeholder="URL Stream (http://...m3u8)" required class="bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-indigo-500 md:col-span-2">
                <button type="submit" class="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-2 px-6 rounded-xl transition md:col-span-5 w-fit ml-auto flex items-center gap-2 text-sm">
                    <i class="fa-solid fa-check"></i> Lưu Kênh
                </button>
            </form>
        </div>

        <!-- Channel Management Table -->
        <div class="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden">
            <div class="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-900/50">
                <h2 class="font-bold text-slate-200 flex items-center gap-2">
                    <i class="fa-solid fa-list-check text-indigo-400"></i> Quản Lý Kênh Streaming
                </h2>
                <input type="text" id="searchBox" onkeyup="filterChannels()" placeholder="Tìm kiếm kênh..." class="bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs w-64 focus:outline-none focus:border-indigo-500">
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-950/80 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                            <th class="p-4 w-12 text-center">STT</th>
                            <th class="p-4 w-16">Logo</th>
                            <th class="p-4">Tên Kênh</th>
                            <th class="p-4">Phân Nhóm</th>
                            <th class="p-4">URL Luồng Nguồn</th>
                            <th class="p-4 text-center">Thao Tác</th>
                        </tr>
                    </thead>
                    <tbody id="channelTbody" class="divide-y divide-slate-800/60 text-sm">
                        <!-- Dynamic content -->
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- Modal Import M3U Raw -->
    <div id="importModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm hidden flex items-center justify-center p-4 z-50">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-2xl w-full p-6 space-y-4">
            <div class="flex justify-between items-center">
                <h3 class="font-bold text-lg text-indigo-400 flex items-center gap-2">
                    <i class="fa-solid fa-file-code"></i> Dán Toàn Bộ File M3U Vừa Nhận
                </h3>
                <button onclick="document.getElementById('importModal').classList.add('hidden')" class="text-slate-400 hover:text-white">
                    <i class="fa-solid fa-xmark text-xl"></i>
                </button>
            </div>
            <textarea id="rawM3uContent" rows="12" placeholder="#EXTM3U&#10;#EXTINF:-1 group-title=&quot;VTV&quot;,VTV1&#10;http://..." class="w-full bg-slate-950 border border-slate-800 rounded-xl p-4 text-xs font-mono focus:outline-none focus:border-indigo-500"></textarea>
            <div class="flex justify-end gap-3">
                <button onclick="document.getElementById('importModal').classList.add('hidden')" class="px-4 py-2 text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg">Hủy</button>
                <button onclick="submitRawM3u()" class="px-4 py-2 text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg">Ghi Đè & Lưu</button>
            </div>
        </div>
    </div>

    <script>
        const baseUrl = window.location.origin;
        document.getElementById('m3uUrl').innerText = `${baseUrl}/playlist.m3u`;

        let allChannels = [];

        async function fetchChannels() {
            const res = await fetch('/api/channels');
            allChannels = await res.json();
            document.getElementById('totalChannels').innerText = allChannels.length;
            renderTable(allChannels);
        }

        function renderTable(channels) {
            const tbody = document.getElementById('channelTbody');
            tbody.innerHTML = channels.map((c, i) => `
                <tr class="hover:bg-slate-800/40 transition">
                    <td class="p-4 text-center font-mono text-xs text-slate-500">${i + 1}</td>
                    <td class="p-4">
                        ${c.logo ? `<img src="${c.logo}" class="w-8 h-8 object-contain rounded bg-slate-950 border border-slate-800">` : `<div class="w-8 h-8 rounded bg-slate-950 flex items-center justify-center text-slate-600 text-xs"><i class="fa-solid fa-tv"></i></div>`}
                    </td>
                    <td class="p-4 font-semibold text-slate-200">${c.name}</td>
                    <td class="p-4"><span class="px-2.5 py-1 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg text-xs font-medium">${c.group}</span></td>
                    <td class="p-4 font-mono text-xs text-slate-400 max-w-xs truncate">${c.url}</td>
                    <td class="p-4 text-center">
                        <button onclick="deleteChan(${i})" class="p-2 text-rose-400 hover:bg-rose-500/10 rounded-lg transition" title="Xóa kênh">
                            <i class="fa-solid fa-trash"></i>
                        </button>
                    </td>
                </tr>
            `).join('');
        }

        function filterChannels() {
            const q = document.getElementById('searchBox').value.toLowerCase();
            const filtered = allChannels.filter(c => c.name.toLowerCase().includes(q) || c.group.toLowerCase().includes(q));
            renderTable(filtered);
        }

        document.getElementById('addChannelForm').onsubmit = async (e) => {
            e.preventDefault();
            const payload = {
                name: document.getElementById('add_name').value,
                group: document.getElementById('add_group').value,
                logo: document.getElementById('add_logo').value,
                url: document.getElementById('add_url').value
            };
            await fetch('/api/channels', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            document.getElementById('addChannelForm').reset();
            fetchChannels();
        };

        async function deleteChan(idx) {
            if(confirm('Xóa kênh này khỏi danh sách?')) {
                await fetch(`/api/channels/${idx}`, {method: 'DELETE'});
                fetchChannels();
            }
        }

        async function submitRawM3u() {
            const raw = document.getElementById('rawM3uContent').value;
            await fetch('/api/import_raw', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({raw})
            });
            document.getElementById('importModal').classList.add('hidden');
            fetchChannels();
        }

        fetchChannels();
    </script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(NEXTJS_DASHBOARD_HTML)

# --- REST APIs ---
@app.route("/api/channels", methods=["GET"])
def get_channels():
    return jsonify(parse_m3u_file("playlist.m3u"))

@app.route("/api/channels", methods=["POST"])
def add_channel():
    data = request.json
    channels = parse_m3u_file("playlist.m3u")
    channels.append({
        "id": data.get("name", "").lower().replace(" ", "_"),
        "name": data.get("name", ""),
        "logo": data.get("logo", ""),
        "chno": str(len(channels) + 1),
        "group": data.get("group", "Chung"),
        "url": data.get("url", ""),
        "enabled": True
    })
    save_m3u_file(channels)
    return jsonify({"status": "ok"})

@app.route("/api/channels/<int:index>", methods=["DELETE"])
def delete_channel(index):
    channels = parse_m3u_file("playlist.m3u")
    if 0 <= index < len(channels):
        channels.pop(index)
        save_m3u_file(channels)
    return jsonify({"status": "ok"})

@app.route("/api/import_raw", methods=["POST"])
def import_raw_m3u():
    data = request.json
    raw_content = data.get("raw", "")
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write(raw_content)
    return jsonify({"status": "ok"})

# --- PLAYLIST EXPORT & PROXY ENGINE ---
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