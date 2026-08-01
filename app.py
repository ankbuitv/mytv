import json
import os
import re
import time
import requests
from urllib.parse import parse_qs, quote, urlencode, unquote, urljoin, urlparse
from flask import Flask, Response, request, render_template_string, stream_with_context, jsonify

app = Flask(__name__)

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

PLAYLIST_FILE = "playlist.m3u"
EPG_FILE = "epg.json"
SETTINGS_FILE = "settings.json"
START_TIME = time.time()

DEFAULT_SETTINGS = {
    "custom_headers": {},
    "stream_timeout": 18,
    "allow_redirects": True
}


def _get_base_url():
    return os.environ.get("PUBLIC_BASE_URL", "http://chrtv.duckdns.org:14211")


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_id(value):
    source = str(value or "").strip().lower()
    source = re.sub(r"[^a-z0-9]+", "_", source)
    source = re.sub(r"_+", "_", source).strip("_")
    return source or f"channel_{int(time.time())}"


def parse_extinf(line):
    attrs = {}
    parts = line.split(",", 1)
    if len(parts) == 2:
        header, title = parts
        attrs["name"] = title.strip()
    else:
        header = line
        attrs["name"] = "Unknown Channel"
    for key, value in re.findall(r"(\w+)\s*=\s*\"([^\"]*)\"", header):
        attrs[key] = value
    return attrs


def parse_m3u_content(raw_text):
    channels = []
    current = {}
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            attrs = parse_extinf(line)
            channel_id = attrs.get("tvg-id") or attrs.get("tvg-name") or attrs.get("name")
            current = {
                "id": normalize_id(channel_id),
                "name": attrs.get("tvg-name") or attrs.get("name"),
                "logo": attrs.get("tvg-logo", ""),
                "chno": attrs.get("tvg-chno", ""),
                "group": attrs.get("group-title", "Chung"),
                "enabled": True
            }
        elif line.startswith("#"):
            continue
        else:
            if current:
                current["url"] = line
                channels.append(current)
                current = {}
    return channels


def parse_m3u_file(file_path=PLAYLIST_FILE):
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()
    return parse_m3u_content(raw_text)


def save_m3u_file(channels, file_path=PLAYLIST_FILE):
    lines = ["#EXTM3U refresh=1800"]
    for channel in channels:
        if not channel.get("enabled", True):
            continue
        lines.append(
            f'#EXTINF:-1 tvg-id="{channel.get("id")}" '
            f'tvg-name="{channel.get("name")}" '
            f'tvg-logo="{channel.get("logo", "")}" '
            f'tvg-chno="{channel.get("chno", "")}" '
            f'group-title="{channel.get("group", "Chung")}",{channel.get("name")}'
        )
        lines.append(channel.get("url", ""))
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


def load_epg():
    return load_json_file(EPG_FILE, [])


def save_epg(events):
    save_json_file(EPG_FILE, events)


def load_settings():
    saved = load_json_file(SETTINGS_FILE, DEFAULT_SETTINGS)
    result = {**DEFAULT_SETTINGS, **saved}
    result["custom_headers"] = {**DEFAULT_SETTINGS["custom_headers"], **result.get("custom_headers", {})}
    return result


def save_settings(settings):
    merged = {**DEFAULT_SETTINGS, **settings}
    save_json_file(SETTINGS_FILE, merged)


def find_channel(channels, identifier):
    if identifier is None:
        return None
    for channel in channels:
        if str(channel.get("id")) == str(identifier):
            return channel
    if str(identifier).isdigit():
        idx = int(identifier)
        if 0 <= idx < len(channels):
            return channels[idx]
    return None


def get_request_headers():
    settings = load_settings()
    headers = {**HEADERS_DEFAULT, **settings.get("custom_headers", {})}
    for name, value in request.headers.items():
        if name.lower().startswith("x-stream-"):
            sanitized = name[2:]
            headers[sanitized] = value
    return headers


def apply_timeshift(url, timeshift):
    if not timeshift:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["start"] = [str(timeshift)]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def is_hls_response(response, url):
    content_type = response.headers.get("Content-Type", "").lower()
    text_sample = response.text[:64].strip() if response.text else ""
    return (
        "application/vnd.apple.mpegurl" in content_type
        or "application/x-mpegurl" in content_type
        or "vnd.apple.mpegurl" in content_type
        or url.lower().endswith(".m3u8")
        or text_sample.startswith("#EXTM3U")
    )


def rewrite_hls_playlist(content, base_url, source_url, timeshift=None):
    lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            lines.append(raw_line)
            continue
        absolute = urljoin(source_url, line)
        proxied = f"{base_url}/stream/play?url={quote(absolute, safe='')}"
        if timeshift:
            proxied += f"&timeshift={quote(timeshift, safe='')}"
        lines.append(proxied)
    return "\n".join(lines)

NEXTJS_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>chrtv IPTV Manager</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
        ::selection { background: rgba(99, 102, 241, 0.4); }
    </style>
</head>
<body class="min-h-screen">
    <div class="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
        <header class="rounded-[2rem] border border-slate-800 bg-slate-950/95 p-6 shadow-2xl shadow-slate-950/20 backdrop-blur-xl">
            <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div class="space-y-3">
                    <span class="inline-flex items-center gap-2 rounded-full bg-indigo-600/10 px-4 py-2 text-sm text-indigo-200 font-semibold">chrtv IPTV Manager</span>
                    <h1 class="text-4xl font-semibold tracking-tight text-white sm:text-5xl">Nền tảng quản lý IPTV chuyên nghiệp</h1>
                    <p class="max-w-2xl text-slate-400">Dashboard quản lý kênh, EPG, import M3U, stream proxy, custom headers, preview HLS và timeshift. Thiết kế tối, animation mượt và chức năng production-ready.</p>
                </div>
                <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <div class="rounded-3xl border border-slate-800 bg-slate-900/90 p-4 text-center">
                        <p class="text-slate-400 text-xs uppercase tracking-[0.24em]">Playlist</p>
                        <p id="hostUrl" class="mt-2 text-sm text-indigo-300 break-all">...</p>
                    </div>
                    <div class="rounded-3xl border border-slate-800 bg-slate-900/90 p-4 text-center">
                        <p class="text-slate-400 text-xs uppercase tracking-[0.24em]">Stream</p>
                        <p class="mt-2 text-lg font-semibold text-white">Proxy</p>
                    </div>
                </div>
            </div>
        </header>

        <main class="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
            <section class="space-y-6">
                <div class="grid gap-6 lg:grid-cols-2">
                    <article class="rounded-[2rem] border border-slate-800 bg-slate-900/95 p-6 shadow-2xl shadow-slate-950/10 transition hover:-translate-y-0.5">
                        <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Thống kê</p>
                        <div class="mt-5 grid gap-4 sm:grid-cols-2">
                            <div class="rounded-3xl border border-slate-800 bg-slate-950/80 p-5">
                                <p class="text-slate-400 text-xs uppercase tracking-[0.2em]">Kênh</p>
                                <p id="totalChannels" class="mt-3 text-3xl font-semibold text-white">0</p>
                            </div>
                            <div class="rounded-3xl border border-slate-800 bg-slate-950/80 p-5">
                                <p class="text-slate-400 text-xs uppercase tracking-[0.2em]">EPG</p>
                                <p id="totalEpg" class="mt-3 text-3xl font-semibold text-white">0</p>
                            </div>
                            <div class="rounded-3xl border border-slate-800 bg-slate-950/80 p-5">
                                <p class="text-slate-400 text-xs uppercase tracking-[0.2em]">Nhóm</p>
                                <p id="totalGroups" class="mt-3 text-3xl font-semibold text-white">0</p>
                            </div>
                            <div class="rounded-3xl border border-slate-800 bg-slate-950/80 p-5">
                                <p class="text-slate-400 text-xs uppercase tracking-[0.2em]">Uptime</p>
                                <p id="uptime" class="mt-3 text-3xl font-semibold text-white">0s</p>
                            </div>
                        </div>
                    </article>
                    <article class="rounded-[2rem] border border-slate-800 bg-slate-900/95 p-6 shadow-2xl shadow-slate-950/10">
                        <div class="flex items-center justify-between gap-3">
                            <div>
                                <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Người xem thử</p>
                                <h2 class="mt-3 text-2xl font-semibold text-white">Player HLS</h2>
                            </div>
                            <button onclick="showImportPanel()" class="rounded-3xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 transition">Import M3U</button>
                        </div>
                        <div class="mt-5 rounded-[1.75rem] overflow-hidden border border-slate-800 bg-black">
                            <video id="previewPlayer" class="w-full h-72 bg-black" controls playsinline muted></video>
                        </div>
                        <div class="mt-5 grid gap-3 sm:grid-cols-2">
                            <button onclick="previewSelectedChannel()" class="rounded-3xl bg-cyan-600 px-4 py-3 text-sm font-semibold text-white hover:bg-cyan-500 transition">Preview Kênh</button>
                            <button onclick="exportPlaylist()" class="rounded-3xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-500 transition">Mở Playlist</button>
                        </div>
                    </article>
                </div>

                <article id="channelPanel" class="rounded-[2rem] border border-slate-800 bg-slate-900/95 p-6 shadow-2xl shadow-slate-950/10">
                    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                            <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Danh sách kênh</p>
                            <h2 class="mt-3 text-2xl font-semibold text-white">Quản lý kênh</h2>
                        </div>
                        <button onclick="openChannelModal()" class="inline-flex items-center gap-2 rounded-3xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-500 transition"><i class="fa-solid fa-plus"></i> Thêm kênh</button>
                    </div>
                    <div class="mt-6 grid gap-4 lg:grid-cols-[1fr_auto]">
                        <input id="searchBox" oninput="filterChannels()" placeholder="Search kênh, nhóm hoặc ID..." class="rounded-3xl border border-slate-800 bg-slate-950/80 px-5 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                        <select id="groupFilter" onchange="filterChannels()" class="rounded-3xl border border-slate-800 bg-slate-950/80 px-5 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500">
                            <option value="">Tất cả nhóm</option>
                        </select>
                    </div>
                    <div class="mt-6 overflow-hidden rounded-[1.75rem] border border-slate-800 bg-slate-950/80">
                        <table class="w-full border-collapse text-left text-sm text-slate-300">
                            <thead class="bg-slate-900 text-slate-400 text-xs uppercase tracking-[0.2em]">
                                <tr>
                                    <th class="px-5 py-4">#</th>
                                    <th class="px-5 py-4">Tên</th>
                                    <th class="px-5 py-4">Nhóm</th>
                                    <th class="px-5 py-4">Trạng thái</th>
                                    <th class="px-5 py-4">Hành động</th>
                                </tr>
                            </thead>
                            <tbody id="channelTbody" class="divide-y divide-slate-800"></tbody>
                        </table>
                    </div>
                </article>

                <article class="rounded-[2rem] border border-slate-800 bg-slate-900/95 p-6 shadow-2xl shadow-slate-950/10">
                    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                            <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">EPG</p>
                            <h2 class="mt-3 text-2xl font-semibold text-white">Lịch phát sóng</h2>
                        </div>
                        <button onclick="openEpgModal()" class="inline-flex items-center gap-2 rounded-3xl bg-cyan-600 px-4 py-3 text-sm font-semibold text-white hover:bg-cyan-500 transition"><i class="fa-solid fa-calendar-plus"></i> Thêm EPG</button>
                    </div>
                    <div class="mt-6 overflow-hidden rounded-[1.75rem] border border-slate-800 bg-slate-950/80">
                        <table class="w-full border-collapse text-left text-sm text-slate-300">
                            <thead class="bg-slate-900 text-slate-400 text-xs uppercase tracking-[0.2em]">
                                <tr>
                                    <th class="px-5 py-4">Kênh</th>
                                    <th class="px-5 py-4">Chương trình</th>
                                    <th class="px-5 py-4">Bắt đầu</th>
                                    <th class="px-5 py-4">Kết thúc</th>
                                    <th class="px-5 py-4">Hành động</th>
                                </tr>
                            </thead>
                            <tbody id="epgTbody" class="divide-y divide-slate-800"></tbody>
                        </table>
                    </div>
                </article>
            </section>

            <aside class="space-y-6">
                <article class="rounded-[2rem] border border-slate-800 bg-slate-900/95 p-6 shadow-2xl shadow-slate-950/10">
                    <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Import / Export</p>
                    <div class="mt-5 grid gap-3">
                        <button onclick="openImportPanel()" class="rounded-3xl bg-indigo-600 px-4 py-4 text-sm font-semibold text-white hover:bg-indigo-500 transition">Nhập playlist M3U</button>
                        <button onclick="exportPlaylist()" class="rounded-3xl bg-emerald-600 px-4 py-4 text-sm font-semibold text-white hover:bg-emerald-500 transition">Mở playlist</button>
                    </div>
                    <div class="mt-6 grid gap-3">
                        <button onclick="fetchSettings();" class="rounded-3xl border border-slate-700 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 hover:bg-slate-900 transition">Làm mới cài đặt</button>
                    </div>
                </article>

                <article class="rounded-[2rem] border border-slate-800 bg-slate-900/95 p-6 shadow-2xl shadow-slate-950/10">
                    <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Custom Headers</p>
                    <div class="mt-5 grid gap-3">
                        <input id="customHeaderName" placeholder="Header name" class="rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500" />
                        <input id="customHeaderValue" placeholder="Header value" class="rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500" />
                        <button onclick="addHeader()" class="rounded-3xl bg-cyan-600 px-4 py-4 text-sm font-semibold text-white hover:bg-cyan-500 transition">Lưu header</button>
                    </div>
                    <div id="headersList" class="mt-5 space-y-3"></div>
                </article>
            </aside>
        </main>
    </div>

    <div id="modalBackdrop" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
        <div id="modalDialog" class="w-full max-w-3xl rounded-[2rem] border border-slate-800 bg-slate-950 p-6 shadow-2xl shadow-black/40"></div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.4.0/hls.min.js"></script>
    <script>
        const baseUrl = window.location.origin;
        document.getElementById('hostUrl').innerText = `${baseUrl}/playlist.m3u`;

        let channels = [];
        let epgEvents = [];
        let headers = {};

        async function fetchData() {
            await Promise.all([fetchChannels(), fetchEpg(), fetchSettings(), fetchStatus()]);
        }

        async function fetchStatus() {
            const res = await fetch('/api/system');
            const status = await res.json();
            document.getElementById('totalChannels').innerText = status.channels;
            document.getElementById('totalEpg').innerText = status.epg;
            document.getElementById('totalGroups').innerText = status.groups;
            document.getElementById('uptime').innerText = `${Math.floor(status.uptime / 3600)}h ${Math.floor((status.uptime % 3600) / 60)}m`;
        }

        async function fetchChannels() {
            const res = await fetch('/api/channels');
            channels = await res.json();
            const uniqueGroups = [...new Set(channels.map(c => c.group || 'Chung'))].sort();
            const groupSelect = document.getElementById('groupFilter');
            groupSelect.innerHTML = '<option value="">Tất cả nhóm</option>' + uniqueGroups.map(g => `<option value="${g}">${g}</option>`).join('');
            renderChannels(channels);
        }

        async function fetchEpg() {
            const res = await fetch('/api/epg');
            epgEvents = await res.json();
            const tbody = document.getElementById('epgTbody');
            tbody.innerHTML = epgEvents.map(evt => `
                <tr class="border-b border-slate-800 hover:bg-slate-900/70 transition">
                    <td class="px-5 py-4 text-slate-200">${evt.channel_id}</td>
                    <td class="px-5 py-4 text-slate-200">${evt.title}</td>
                    <td class="px-5 py-4 text-slate-400">${new Date(evt.start).toLocaleString()}</td>
                    <td class="px-5 py-4 text-slate-400">${new Date(evt.end).toLocaleString()}</td>
                    <td class="px-5 py-4 space-x-2">
                        <button onclick="openEpgModal('${evt.id}')" class="rounded-2xl bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 transition">Sửa</button>
                        <button onclick="deleteEpg('${evt.id}')" class="rounded-2xl bg-rose-600 px-3 py-2 text-xs text-white hover:bg-rose-500 transition">Xóa</button>
                    </td>
                </tr>
            `).join('');
        }

        async function fetchSettings() {
            const res = await fetch('/api/settings');
            const data = await res.json();
            headers = data.custom_headers || {};
            renderHeaders();
        }

        function renderHeaders() {
            const list = document.getElementById('headersList');
            const html = Object.entries(headers).map(([key, value]) => `
                <div class="flex items-center justify-between gap-3 rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4">
                    <div>
                        <p class="font-semibold text-white">${key}</p>
                        <p class="text-slate-500 text-sm">${value}</p>
                    </div>
                    <button onclick="removeHeader('${key}')" class="rounded-2xl bg-rose-600 px-3 py-2 text-xs text-white hover:bg-rose-500 transition">Xóa</button>
                </div>
            `).join('');
            list.innerHTML = html || '<p class="text-slate-500 text-sm">Chưa có header tùy chỉnh.</p>';
        }

        function filterChannels() {
            const keyword = document.getElementById('searchBox').value.toLowerCase();
            const group = document.getElementById('groupFilter').value;
            const filtered = channels.filter(channel => {
                const matchText = `${channel.name} ${channel.group} ${channel.id}`.toLowerCase();
                const matchGroup = !group || channel.group === group;
                return matchText.includes(keyword) && matchGroup;
            });
            renderChannels(filtered);
        }

        function renderChannels(list) {
            const tbody = document.getElementById('channelTbody');
            tbody.innerHTML = list.map((channel, index) => `
                <tr class="border-b border-slate-800 hover:bg-slate-900/70 transition">
                    <td class="px-5 py-4 text-slate-400">${index + 1}</td>
                    <td class="px-5 py-4 text-white font-semibold">${channel.name}</td>
                    <td class="px-5 py-4"><span class="inline-flex rounded-full bg-indigo-600/10 px-3 py-1 text-xs font-semibold text-indigo-200">${channel.group}</span></td>
                    <td class="px-5 py-4 text-slate-300">${channel.enabled ? '<span class="text-emerald-400">Active</span>' : '<span class="text-rose-400">Disabled</span>'}</td>
                    <td class="px-5 py-4 flex flex-wrap gap-2">
                        <button onclick="previewChannel('${encodeURIComponent(channel.url)}')" class="rounded-2xl bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 transition">Preview</button>
                        <button onclick="openChannelModal('${channel.id}')" class="rounded-2xl bg-cyan-600 px-3 py-2 text-xs text-white hover:bg-cyan-500 transition">Sửa</button>
                        <button onclick="deleteChannel('${channel.id}')" class="rounded-2xl bg-rose-600 px-3 py-2 text-xs text-white hover:bg-rose-500 transition">Xóa</button>
                    </td>
                </tr>
            `).join('');
        }

        function openImportPanel() {
            const html = `
                <div class="flex items-center justify-between mb-5">
                    <div>
                        <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Import playlist</p>
                        <h2 class="text-2xl font-semibold text-white">Import M3U</h2>
                    </div>
                    <button onclick="closeModal()" class="rounded-full border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800 transition"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="grid gap-4">
                    <textarea id="importRawText" rows="6" placeholder="Dán nội dung M3U..." class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"></textarea>
                    <input id="importRemoteUrl" placeholder="URL M3U từ mạng" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                    <div class="grid gap-3 sm:grid-cols-2">
                        <button onclick="importRawM3u()" class="rounded-3xl bg-indigo-600 px-4 py-4 text-sm font-semibold text-white hover:bg-indigo-500 transition">Nhập nội dung</button>
                        <button onclick="importRemoteM3u()" class="rounded-3xl bg-emerald-600 px-4 py-4 text-sm font-semibold text-white hover:bg-emerald-500 transition">Nhập từ URL</button>
                    </div>
                </div>
            `;
            openModal(html);
        }

        function openChannelModal(channelId = '') {
            const channel = channels.find(c => c.id === channelId) || { id: '', name: '', group: '', logo: '', url: '', enabled: true };
            const html = `
                <div class="flex items-center justify-between mb-5">
                    <div>
                        <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">Kênh</p>
                        <h2 class="text-2xl font-semibold text-white">${channel.id ? 'Chỉnh sửa kênh' : 'Thêm kênh mới'}</h2>
                    </div>
                    <button onclick="closeModal()" class="rounded-full border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800 transition"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="grid gap-4">
                    <input id="modalChannelName" placeholder="Tên kênh" value="${channel.name}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                    <input id="modalChannelGroup" placeholder="Nhóm" value="${channel.group}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                    <input id="modalChannelLogo" placeholder="URL logo" value="${channel.logo}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                    <input id="modalChannelUrl" placeholder="URL stream m3u8 / TS" value="${channel.url}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
                    <label class="inline-flex items-center gap-3 rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200">
                        <input id="modalChannelEnabled" type="checkbox" ${channel.enabled ? 'checked' : ''} class="h-5 w-5 rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-indigo-500" />
                        Kích hoạt kênh
                    </label>
                    <div class="grid gap-3 sm:grid-cols-2">
                        <button onclick="saveChannel('${channel.id}')" class="rounded-3xl bg-indigo-600 px-4 py-4 text-sm font-semibold text-white hover:bg-indigo-500 transition">Lưu</button>
                        <button onclick="closeModal()" class="rounded-3xl border border-slate-700 px-4 py-4 text-sm font-semibold text-slate-200 hover:bg-slate-800 transition">Hủy</button>
                    </div>
                </div>
            `;
            openModal(html);
        }

        function openEpgModal(eventId = '') {
            const event = epgEvents.find(e => e.id === eventId) || { id: '', channel_id: '', title: '', start: '', end: '', description: '' };
            const html = `
                <div class="flex items-center justify-between mb-5">
                    <div>
                        <p class="text-slate-400 uppercase tracking-[0.24em] text-xs">EPG</p>
                        <h2 class="text-2xl font-semibold text-white">${event.id ? 'Chỉnh sửa EPG' : 'Thêm sự kiện EPG'}</h2>
                    </div>
                    <button onclick="closeModal()" class="rounded-full border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800 transition"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="grid gap-4">
                    <select id="modalEpgChannel" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500">
                        <option value="">Chọn kênh</option>
                        ${channels.map(c => `<option value="${c.id}" ${event.channel_id === c.id ? 'selected' : ''}>${c.name}</option>`).join('')}
                    </select>
                    <input id="modalEpgTitle" placeholder="Tên chương trình" value="${event.title}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500" />
                    <div class="grid gap-3 sm:grid-cols-2">
                        <input id="modalEpgStart" type="datetime-local" value="${event.start ? event.start.replace(' ', 'T') : ''}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500" />
                        <input id="modalEpgEnd" type="datetime-local" value="${event.end ? event.end.replace(' ', 'T') : ''}" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500" />
                    </div>
                    <textarea id="modalEpgDesc" rows="4" placeholder="Mô tả chương trình" class="w-full rounded-3xl border border-slate-800 bg-slate-950/80 px-4 py-4 text-sm text-slate-200 focus:outline-none focus:border-cyan-500">${event.description}</textarea>
                    <div class="grid gap-3 sm:grid-cols-2">
                        <button onclick="saveEpg('${event.id}')" class="rounded-3xl bg-cyan-600 px-4 py-4 text-sm font-semibold text-white hover:bg-cyan-500 transition">Lưu EPG</button>
                        <button onclick="closeModal()" class="rounded-3xl border border-slate-700 px-4 py-4 text-sm font-semibold text-slate-200 hover:bg-slate-800 transition">Hủy</button>
                    </div>
                </div>
            `;
            openModal(html);
        }

        function openModal(content) {
            document.getElementById('modalDialog').innerHTML = content;
            document.getElementById('modalBackdrop').classList.remove('hidden');
        }

        function closeModal() {
            document.getElementById('modalBackdrop').classList.add('hidden');
            document.getElementById('modalDialog').innerHTML = '';
        }

        async function saveChannel(channelId = '') {
            const payload = {
                name: document.getElementById('modalChannelName').value.trim(),
                group: document.getElementById('modalChannelGroup').value.trim() || 'Chung',
                logo: document.getElementById('modalChannelLogo').value.trim(),
                url: document.getElementById('modalChannelUrl').value.trim(),
                enabled: document.getElementById('modalChannelEnabled').checked
            };
            if (!payload.name || !payload.url) {
                return alert('Tên kênh và URL không được để trống.');
            }
            if (channelId) {
                await fetch(`/api/channels/${encodeURIComponent(channelId)}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
            } else {
                await fetch('/api/channels', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
            }
            closeModal();
            await refreshData();
        }

        async function deleteChannel(channelId) {
            if (!confirm('Bạn có chắc muốn xóa kênh này?')) return;
            await fetch(`/api/channels/${encodeURIComponent(channelId)}`, { method: 'DELETE' });
            await refreshData();
        }

        async function previewChannel(streamUrl) {
            const decoded = decodeURIComponent(streamUrl);
            const url = `${baseUrl}/stream/play?url=${encodeURIComponent(decoded)}`;
            const video = document.getElementById('previewPlayer');
            if (Hls.isSupported()) {
                const hls = new Hls();
                hls.loadSource(url);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, function() { video.play().catch(() => {}); });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = url;
                video.play().catch(() => {});
            }
        }

        async function previewSelectedChannel() {
            if (!channels.length) return alert('Không có kênh để xem thử.');
            previewChannel(encodeURIComponent(channels[0].url));
        }

        async function importRawM3u() {
            const raw = document.getElementById('importRawText').value.trim();
            if (!raw) return alert('Vui lòng dán nội dung M3U.');
            await fetch('/api/channels/import', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ raw })
            });
            closeModal();
            await refreshData();
        }

        async function importRemoteM3u() {
            const url = document.getElementById('importRemoteUrl').value.trim();
            if (!url) return alert('Vui lòng nhập URL M3U.');
            await fetch('/api/channels/import', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ source_url: url })
            });
            closeModal();
            await refreshData();
        }

        async function addHeader() {
            const name = document.getElementById('customHeaderName').value.trim();
            const value = document.getElementById('customHeaderValue').value.trim();
            if (!name || !value) return alert('Cần điền tên và giá trị header.');
            headers[name] = value;
            await saveSettings();
            document.getElementById('customHeaderName').value = '';
            document.getElementById('customHeaderValue').value = '';
            renderHeaders();
        }

        async function removeHeader(name) {
            delete headers[name];
            await saveSettings();
            renderHeaders();
        }

        async function saveSettings() {
            await fetch('/api/settings', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ custom_headers: headers })
            });
        }

        async function saveEpg(eventId = '') {
            const payload = {
                channel_id: document.getElementById('modalEpgChannel').value,
                title: document.getElementById('modalEpgTitle').value.trim(),
                start: document.getElementById('modalEpgStart').value,
                end: document.getElementById('modalEpgEnd').value,
                description: document.getElementById('modalEpgDesc').value.trim()
            };
            if (!payload.channel_id || !payload.title || !payload.start || !payload.end) {
                return alert('Vui lòng điền đầy đủ thông tin EPG.');
            }
            const startAt = new Date(payload.start);
            const endAt = new Date(payload.end);
            if (startAt >= endAt) {
                return alert('Thời gian bắt đầu phải trước thời gian kết thúc.');
            }
            if (eventId) {
                await fetch(`/api/epg/${encodeURIComponent(eventId)}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
            } else {
                await fetch('/api/epg', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
            }
            closeModal();
            await refreshData();
        }

        async function deleteEpg(eventId) {
            if (!confirm('Xóa sự kiện EPG này?')) return;
            await fetch(`/api/epg/${encodeURIComponent(eventId)}`, { method: 'DELETE' });
            await refreshData();
        }

        async function exportPlaylist() {
            window.open(`${baseUrl}/playlist.m3u`, '_blank');
        }

        async function refreshData() {
            await fetchData();
        }

        window.addEventListener('load', fetchData);
    </script>
</body>
</html>
"""

@app.after_request
def apply_default_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

@app.route("/")
def dashboard():
    return render_template_string(NEXTJS_DASHBOARD_HTML)

@app.route("/admin")
def admin_redirect():
    return dashboard()

@app.route("/api/system", methods=["GET"])
def api_system():
    channels = parse_m3u_file()
    epg = load_epg()
    groups = sorted({ch.get("group", "Chung") for ch in channels})
    return jsonify({
        "channels": len(channels),
        "epg": len(epg),
        "groups": len(groups),
        "base_url": _get_base_url(),
        "uptime": int(time.time() - START_TIME),
        "settings": load_settings(),
    })

@app.route("/api/channels", methods=["GET", "POST"])
def api_channels():
    channels = parse_m3u_file()
    if request.method == "GET":
        return jsonify(channels)
    data = request.get_json(silent=True) or {}
    channel = {
        "id": normalize_id(data.get("id") or data.get("name") or f"channel_{len(channels)+1}"),
        "name": data.get("name", "Untitled Channel").strip(),
        "logo": data.get("logo", "").strip(),
        "chno": str(data.get("chno", len(channels) + 1)).strip(),
        "group": data.get("group", "Chung").strip() or "Chung",
        "url": data.get("url", "").strip(),
        "enabled": bool(data.get("enabled", True))
    }
    if not channel["name"] or not channel["url"]:
        return jsonify({"error": "Channel name and url are required."}), 400
    if find_channel(channels, channel["id"]):
        channel["id"] = f"{channel['id']}_{len(channels)+1}"
    channels.append(channel)
    save_m3u_file(channels)
    return jsonify(channel), 201

@app.route("/api/channels/<channel_id>", methods=["PUT", "DELETE"])
def api_channel_modify(channel_id):
    channels = parse_m3u_file()
    channel = find_channel(channels, channel_id)
    if not channel:
        return jsonify({"error": "Channel not found."}), 404
    if request.method == "DELETE":
        channels = [ch for ch in channels if ch.get("id") != channel.get("id")]
        save_m3u_file(channels)
        return jsonify({"status": "deleted"})
    data = request.get_json(silent=True) or {}
    channel["name"] = data.get("name", channel["name"]).strip()
    channel["logo"] = data.get("logo", channel.get("logo", "")).strip()
    channel["chno"] = str(data.get("chno", channel.get("chno", ""))).strip()
    channel["group"] = data.get("group", channel.get("group", "Chung")).strip() or "Chung"
    channel["url"] = data.get("url", channel.get("url", "")).strip()
    channel["enabled"] = bool(data.get("enabled", channel.get("enabled", True)))
    save_m3u_file(channels)
    return jsonify(channel)

@app.route("/api/channels/import", methods=["POST"])
def api_import_channels():
    payload = request.get_json(silent=True) or {}
    if payload.get("raw"):
        raw = payload["raw"].strip()
        if not raw.startswith("#EXTM3U"):
            return jsonify({"error": "Invalid M3U content."}), 400
        with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
            f.write(raw if raw.endswith("\n") else raw + "\n")
        channels = parse_m3u_content(raw)
        save_m3u_file(channels)
        return jsonify({"imported": len(channels)})
    if payload.get("source_url"):
        source_url = payload["source_url"].strip()
        if not source_url:
            return jsonify({"error": "Source URL is required."}), 400
        try:
            response = requests.get(source_url, headers=HEADERS_DEFAULT, timeout=20, allow_redirects=True)
            response.raise_for_status()
            raw = response.text
            if not raw.startswith("#EXTM3U"):
                return jsonify({"error": "Remote file is not a valid M3U playlist."}), 400
            channels = parse_m3u_content(raw)
            save_m3u_file(channels)
            return jsonify({"imported": len(channels), "source_url": source_url})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502
    return jsonify({"error": "Raw content or source_url required."}), 400

@app.route("/api/epg", methods=["GET", "POST"])
def api_epg_collection():
    events = load_epg()
    if request.method == "GET":
        return jsonify(events)
    data = request.get_json(silent=True) or {}
    event = {
        "id": normalize_id(data.get("id") or f"epg_{len(events)+1}"),
        "channel_id": data.get("channel_id", "").strip(),
        "title": data.get("title", "Untitled Program").strip(),
        "start": data.get("start", "").replace('T', ' '),
        "end": data.get("end", "").replace('T', ' '),
        "description": data.get("description", "").strip()
    }
    if not event["channel_id"] or not event["title"] or not event["start"] or not event["end"]:
        return jsonify({"error": "Missing event details."}), 400
    events.append(event)
    save_epg(events)
    return jsonify(event), 201

@app.route("/api/epg/<event_id>", methods=["PUT", "DELETE"])
def api_epg_item(event_id):
    events = load_epg()
    event = next((e for e in events if e.get("id") == event_id), None)
    if not event:
        return jsonify({"error": "EPG event not found."}), 404
    if request.method == "DELETE":
        events = [e for e in events if e.get("id") != event_id]
        save_epg(events)
        return jsonify({"status": "deleted"})
    data = request.get_json(silent=True) or {}
    event["channel_id"] = data.get("channel_id", event["channel_id"]).strip()
    event["title"] = data.get("title", event["title"]).strip()
    event["start"] = data.get("start", event["start"]).replace('T', ' ')
    event["end"] = data.get("end", event["end"]).replace('T', ' ')
    event["description"] = data.get("description", event.get("description", "")).strip()
    save_epg(events)
    return jsonify(event)

@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    if request.method == "GET":
        return jsonify(load_settings())
    payload = request.get_json(silent=True) or {}
    settings = load_settings()
    settings.update(payload)
    save_settings(settings)
    return jsonify(settings)

@app.route("/playlist.m3u", methods=["GET", "OPTIONS"])
def get_playlist():
    if request.method == "OPTIONS":
        return Response(status=204)
    base_url = _get_base_url()
    channels = parse_m3u_file()
    lines = ["#EXTM3U refresh=1800"]
    for channel in channels:
        if not channel.get("enabled", True):
            continue
        lines.append(
            f'#EXTINF:-1 tvg-id="{channel.get("id")}" '
            f'tvg-name="{channel.get("name")}" '
            f'tvg-logo="{channel.get("logo", "")}" '
            f'tvg-chno="{channel.get("chno", "")}" '
            f'group-title="{channel.get("group", "Chung")}",{channel.get("name")}'
        )
        lines.append(f'{base_url}/stream/play?url={quote(channel.get("url", ""), safe="")}')
    playlist = "\n".join(lines).strip() + "\n"
    return Response(playlist, mimetype="application/x-mpegurl")

@app.route("/stream/play", methods=["GET", "OPTIONS"])
def stream_play():
    if request.method == "OPTIONS":
        return Response(status=204)
    raw_url = request.args.get("url")
    if not raw_url:
        return Response("Missing url parameter", status=400)
    target_url = unquote(raw_url)
    timeshift = request.args.get("timeshift")
    if timeshift:
        target_url = apply_timeshift(target_url, timeshift)
    headers = get_request_headers()
    settings = load_settings()
    try:
        resp = requests.get(target_url, headers=headers, timeout=settings["stream_timeout"], stream=True, allow_redirects=settings["allow_redirects"], verify=False)
        resp.raise_for_status()
        if is_hls_response(resp, target_url):
            playlist = rewrite_hls_playlist(resp.text, _get_base_url(), resp.url, timeshift=timeshift)
            return Response(playlist, mimetype="application/vnd.apple.mpegurl")
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        return Response(stream_with_context(resp.iter_content(chunk_size=65536)), mimetype=content_type)
    except requests.exceptions.RequestException as exc:
        return Response(f"Upstream request failed: {exc}", status=502)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
