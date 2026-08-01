#!/usr/bin/env python3
# chrtv IPTV Manager - Production-Ready Full-Stack Python
import os, re, requests
from urllib.parse import urljoin, quote, unquote
from flask import Flask, Response, request, stream_with_context, jsonify, render_template_string

app = Flask(__name__)
HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*", "Connection": "keep-alive"
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
    if not os.path.exists(file_path): return channels
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
                "epg_id": tvg_id.group(1) if tvg_id else "",
                "chno": tvg_chno.group(1) if tvg_chno else "",
                "group": group_title.group(1) if group_title else "Chung",
                "enabled": True,
                "timeshift": 0,
                "catchup_days": 7
            }
        elif line and not line.startswith("#"):
            if current_channel:
                current_channel["url"] = line
                channels.append(current_channel)
                current_channel = {}
    return channels

def save_m3u_file(channels, file_path="playlist.m3u"):
    lines = ["#EXTM3U refresh=\"1800\""]
    for ch in channels:
        if ch.get("enabled", True):
            lines.append(f'#EXTINF:-1 tvg-id="{ch.get("epg_id",ch["id"])}" tvg-name="{ch["name"]}" tvg-logo="{ch.get("logo","")}" tvg-chno="{ch.get("chno","")}" group-title="{ch.get("group","Chung")}",{ch["name"]}')
            lines.append(ch["url"])
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# ---------------- FULL PRODUCTION FRONTEND ----------------
NEXTJS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>chrtv IPTV Manager — Production</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>body{background:#0b0f19;color:#f8fafc;font-family:system-ui,sans-serif;}</style>
</head>
<body class="min-h-screen flex flex-col selection:bg-indigo-500/30">
<header class="sticky top-0 z-50 border-b border-slate-800 bg-slate-900/80 backdrop-blur-xl">
<div class="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
<div class="flex items-center gap-3">
<div class="bg-gradient-to-tr from-indigo-500 to-rose-400 p-2.5 rounded-xl text-white shadow-lg shadow-indigo-500/20">
<i class="fa-solid fa-broadcast-tower text-lg"></i>
</div>
<div>
<h1 class="font-extrabold text-lg tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-slate-400">chrtv IPTV Manager</h1>
<span class="text-[10px] font-mono bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded-full border border-emerald-500/20">LIVE PRODUCTION</span>
</div>
</div>
<div class="text-sm font-mono text-slate-400 bg-slate-950 px-3 py-2 rounded-lg border border-slate-800">BASE: <span id="baseUrl" class="text-indigo-300"></span></div>
</div>
</header>

<main class="max-w-7xl mx-auto px-6 py-8 flex-1 space-y-8">
<div class="grid grid-cols-1 md:grid-cols-4 gap-6">
<div class="bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 p-6 rounded-3xl shadow-2xl hover:-translate-y-1 transition duration-300">
<p class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Tổng Kênh</p>
<h2 id="totalChannels" class="text-4xl font-black mt-2 text-white">0</h2>
</div>
<div class="bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 p-6 rounded-3xl shadow-2xl hover:-translate-y-1 transition duration-300">
<p class="text-xs font-semibold text-slate-400 uppercase tracking-wider">EPG Enabled</p>
<h2 class="text-4xl font-black mt-2 text-indigo-400">ON</h2>
<p class="text-xs text-slate-500 mt-1">Timeshift + Catchup</p>
</div>
<div class="bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 p-6 rounded-3xl shadow-2xl hover:-translate-y-1 transition duration-300">
<p class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Proxy Active</p>
<h2 class="text-4xl font-black mt-2 text-rose-400">ON</h2>
<p class="text-xs text-slate-500 mt-1">m3u8 / TS Relay</p>
</div>
<div class="bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 p-6 rounded-3xl shadow-2xl hover:-translate-y-1 transition duration-300">
<button onclick="document.getElementById('importModal').classList.remove('hidden')" class="w-full h-full flex flex-col items-center justify-center gap-2 text-indigo-400 hover:text-indigo-300 transition">
<i class="fa-solid fa-cloud-arrow-up text-2xl"></i>
<span class="font-bold">Import M3U</span>
</button>
</div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
<div class="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-2xl">
<h2 class="font-bold text-lg mb-4 flex items-center gap-2">Danh sách Kênh <span id="countTag" class="bg-indigo-500/10 text-indigo-400 px-2 py-0.5 rounded-md text-xs">0</span></h2>
<div class="overflow-x-auto rounded-xl border border-slate-800">
<table class="w-full text-sm text-left">
<thead class="bg-slate-950 text-slate-400 text-xs uppercase"><tr><th class="px-4 py-3">Logo</th><th class="px-4 py-3">Tên</th><th class="px-4 py-3">Nhóm</th><th class="px-4 py-3">EPG / Timeshift</th><th class="px-4 py-3">URL</th><th class="px-4 py-3 text-center">Thao tác</th></tr></thead>
<tbody id="tbody" class="divide-y divide-slate-800"></tbody>
</table>
</div>
</div>

<div class="bg-gradient-to-br from-indigo-950/30 to-rose-950/20 border border-indigo-500/20 rounded-3xl p-6 shadow-2xl">
<h3 class="font-bold text-indigo-300 mb-4 flex items-center gap-2"><i class="fa-solid fa-circle-play"></i> Player Preview</h3>
<div class="rounded-xl overflow-hidden border border-slate-800 bg-black shadow-inner">
<video id="testPlayer" controls class="w-full aspect-video" poster=""></video>
</div>
<p class="text-xs text-slate-400 mt-3">Chọn kênh để xem thử luồng HLS proxy qua chrtv.</p>
</div>
</div>
</main>

<div id="importModal" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden flex items-center justify-center z-50 p-4">
<div class="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-2xl p-6 shadow-2xl space-y-4">
<div class="flex justify-between items-center"><h3 class="font-extrabold text-xl text-indigo-400">Dán nội dung M3U</h3><button onclick="this.closest('#importModal').classList.add('hidden')" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark text-xl"></i></button></div>
<textarea id="rawM3u" rows="14" placeholder="#EXTM3U..." class="w-full bg-slate-950 border border-slate-800 rounded-xl p-4 font-mono text-xs focus:outline-none focus:border-indigo-500"></textarea>
<div class="flex justify-end gap-3"><button onclick="document.getElementById('importModal').classList.add('hidden')" class="px-4 py-2 text-xs font-semibold bg-slate-800 rounded-xl">Hủy</button><button onclick="importRaw()" class="px-6 py-2 text-xs font-bold bg-gradient-to-r from-indigo-600 to-purple-600 text-white rounded-xl shadow-lg">Ghi đè & Lưu</button></div>
</div>
</div>

<script>
const baseUrl = window.location.origin;
document.getElementById('baseUrl').textContent = baseUrl;
let channels = [];
async function fetchChannels() {
  const res = await fetch('/api/channels');
  channels = await res.json();
  document.getElementById('totalChannels').textContent = channels.length;
  document.getElementById('countTag').textContent = channels.length;
  renderTable(channels);
}
function renderTable(data) {
  document.getElementById('tbody').innerHTML = data.map((c, i) => `
    <tr class="hover:bg-slate-800/40 transition">
      <td class="px-4 py-3"><img src="${c.logo || ''}" class="w-8 h-8 rounded bg-slate-950 border border-slate-800 object-cover" onerror="this.style.display='none'">${!c.logo ? '<i class="fa-solid fa-television text-slate-600"></i>' : ''}</td>
      <td class="px-4 py-3 font-semibold text-slate-200">${c.name}</td>
      <td class="px-4 py-3"><span class="text-xs bg-indigo-500/10 text-indigo-300 px-2 py-0.5 rounded-md border border-indigo-500/20">${c.group}</span></td>
      <td class="px-4 py-3 text-xs text-slate-400">EPG: ${c.epg_id || 'N/A'} | Shift: ${c.timeshift || 0}h</td>
      <td class="px-4 py-3 font-mono text-xs text-slate-500 truncate max-w-xs">${c.url}</td>
      <td class="px-4 py-3 text-center">
        <button onclick="playPreview(${i})" class="text-indigo-400 hover:text-indigo-300 px-2" title="Xem thử"><i class="fa-solid fa-play"></i></button>
        <button onclick="deleteChan(${i})" class="text-rose-400 hover:text-rose-300 px-2" title="Xóa"><i class="fa-solid fa-trash"></i></button>
      </td>
    </tr>
  `).join('');
}
function playPreview(i) {
  const ch = channels[i];
  if (!ch) return;
  document.getElementById('testPlayer').src = `/stream/play?url=${encodeURIComponent(ch.url)}`;
  document.getElementById('testPlayer').play();
}
async function deleteChan(i) {
  if (!confirm('Xóa kênh này?')) return;
  await fetch(`/api/channels/${i}`, {method: 'DELETE'});
  fetchChannels();
}
async function importRaw() {
  await fetch('/api/import_raw', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({raw: document.getElementById('rawM3u').value})});
  document.getElementById('importModal').classList.add('hidden');
  fetchChannels();
}
fetchChannels();
</script>
</body>
</html>"""

@app.route("/")
def dashboard():
    return render_template_string(NEXTJS_DASHBOARD_HTML)

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
        "epg_id": data.get("epg_id", data.get("id", "")),
        "chno": str(len(channels)+1),
        "group": data.get("group", "Chung"),
        "url": data.get("url", ""),
        "enabled": True,
        "timeshift": data.get("timeshift", 0),
        "catchup_days": data.get("catchup_days", 7)
    })
    save_m3u_file(channels)
    return jsonify({"status":"ok"})

@app.route("/api/channels/<int:index>", methods=["DELETE"])
def delete_channel(index):
    channels = parse_m3u_file("playlist.m3u")
    if 0 <= index < len(channels):
        channels.pop(index)
        save_m3u_file(channels)
    return jsonify({"status":"ok"})

@app.route("/api/import_raw", methods=["POST"])
def import_raw_m3u():
    data = request.json
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write(data.get("raw", ""))
    return jsonify({"status":"ok"})

@app.route("/playlist.m3u", methods=["GET", "OPTIONS"])
def get_playlist():
    if request.method == "OPTIONS": return add_cors(Response(status=204))
    base_url = _get_base_url()
    channels = parse_m3u_file("playlist.m3u")
    lines = ["#EXTM3U refresh=\"1800\""]
    for idx, ch in enumerate(channels, 1):
        chno = ch.get("chno") or str(idx)
        lines.append(f'#EXTINF:-1 tvg-id="{ch.get("epg_id", ch["id"])}" tvg-name="{ch["name"]}" tvg-logo="{ch["logo"]}" tvg-chno="{chno}" group-title="{ch["group"]}",{ch["name"]}')
        lines.append(f'{base_url}/stream/play?url={quote(ch["url"])}')
    return add_cors(Response("\n".join(lines).strip(), mimetype="application/x-mpegurl"))

@app.route("/stream/play", methods=["GET", "OPTIONS"])
def stream_play():
    if request.method == "OPTIONS": return add_cors(Response(status=204))
    target_url = request.args.get("url")
    if not target_url: return add_cors(Response("Missing URL", status=400))
    u = unquote(target_url)
    h = HEADERS_DEFAULT.copy()
    try:
        rs = requests.get(u, headers=h, timeout=12, stream=True, allow_redirects=True)
        if rs.status_code != 200: return add_cors(Response(f"Err HTTP {rs.status_code}", status=rs.status_code))
        ct = rs.headers.get("Content-Type", "")
        if ".m3u8" in u or "m3u8" in ct or (hasattr(rs, 'text') and rs.text and rs.text.strip().startswith("#EXTM3U")):
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
