FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 1. Cài đặt các công cụ hệ thống, Node.js & FFmpeg
RUN apt-get update && apt-get install -y -y \
    curl \
    ca-certificates \
    nodejs \
    ffmpeg \
    jq \
    && rm -rf /var/lib/apt/lists/*

# 2. Cài đặt Bore Tunnel
RUN curl -fsSL https://github.com/ekzhang/bore/releases/download/v0.5.0/bore-v0.5.0-x86_64-unknown-linux-musl.tar.gz -o /tmp/bore.tar.gz \
    && tar -xzf /tmp/bore.tar.gz -C /tmp \
    && install -m 755 /tmp/bore /usr/local/bin/bore \
    && rm -f /tmp/bore.tar.gz /tmp/bore

WORKDIR /app

# 3. Tạo file hls_server.js
RUN cat << 'EOF' > /app/hls_server.js
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const MPD_URL = process.env.MPD_URL || "https://s7771.cdn.mytvnet.vn/pkg20/live_dzones/axn.smil/manifest.mpd";
const CLEARKEY_KEY = process.env.CLEARKEY_KEY || "6f1c09c035eab36323d60d1454db3d20";
const USER_AGENT = process.env.USER_AGENT || "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";
const REFERER = process.env.REFERER || "https://mytvnet.vn/";
const HLS_FLAGS = 'delete_segments+independent_segments+omit_endlist+temp_file';

const HLS_DIR = '/tmp/hls';
const PLAYLIST_FILE = path.join(HLS_DIR, 'stream.m3u8');
const SEGMENT_PATTERN = path.join(HLS_DIR, 'segment_%05d.ts');
const FFMPEG_LOG = '/tmp/ffmpeg.log';

const LISTEN_HOST = '0.0.0.0';
const LISTEN_PORT = process.env.PORT || 10000;

let ffmpegProc = null;

function log(...args) {
  console.log(new Date().toISOString(), ...args);
}

function noCacheHeaders() {
  return {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
    'Access-Control-Allow-Origin': '*',
  };
}

function cleanHlsState() {
  fs.mkdirSync(HLS_DIR, { recursive: true });
  const names = fs.readdirSync(HLS_DIR);
  for (const name of names) {
    if (/\.m3u8$/.test(name) || /\.ts$/.test(name) || /\.tmp$/.test(name)) {
      try { fs.rmSync(path.join(HLS_DIR, name), { force: true }); } catch {}
    }
  }
}

function startFfmpeg() {
  if (ffmpegProc) return;
  cleanHlsState();

  const headers = `Referer: ${REFERER}\r\nOrigin: https://mytvnet.vn\r\n`;
  const args = [
    '-hide_banner', '-loglevel', 'warning',
    '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_on_network_error', '1', '-reconnect_delay_max', '2',
    '-user_agent', USER_AGENT,
    '-headers', headers,
    '-cenc_decryption_key', CLEARKEY_KEY,
    '-i', MPD_URL,
    '-map', '0:v:0?', '-map', '0:a:0?',
    '-c:v', 'copy',
    '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2',
    '-af', 'aresample=async=1:first_pts=0',
    '-max_muxing_queue_size', '4096',
    '-f', 'hls', '-hls_time', '2', '-hls_list_size', '6', '-hls_delete_threshold', '2',
    '-hls_flags', HLS_FLAGS, '-hls_segment_type', 'mpegts',
    '-hls_start_number_source', 'epoch',
    '-hls_segment_filename', SEGMENT_PATTERN,
    PLAYLIST_FILE
  ];

  log('[FFmpeg] starting HLS producer');
  ffmpegProc = spawn('ffmpeg', args, { stdio: ['ignore', 'ignore', 'pipe'] });

  ffmpegProc.stderr.on('data', (chunk) => {
    try { fs.appendFileSync(FFMPEG_LOG, chunk.toString()); } catch {}
  });

  ffmpegProc.on('exit', (code, signal) => {
    log(`[FFmpeg] exited code=${code} signal=${signal}`);
    ffmpegProc = null;
    setTimeout(startFfmpeg, 1500);
  });
}

function serveFile(res, absPath, mimeType) {
  fs.stat(absPath, (err, st) => {
    if (err || !st.isFile() || st.size <= 0) {
      res.writeHead(404, { 'Content-Type': 'text/plain', ...noCacheHeaders() });
      res.end('Not found');
      return;
    }
    res.writeHead(200, {
      'Content-Type': mimeType,
      'Content-Length': st.size,
      ...noCacheHeaders(),
    });
    fs.createReadStream(absPath).pipe(res);
  });
}

startFfmpeg();

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/healthz' || url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/plain', ...noCacheHeaders() });
    res.end('OK');
    return;
  }
  if (url.pathname === '/stream.m3u8') {
    serveFile(res, PLAYLIST_FILE, 'application/vnd.apple.mpegurl');
    return;
  }
  if (/^\/segment_\d+\.ts$/.test(url.pathname)) {
    serveFile(res, path.join(HLS_DIR, path.basename(url.pathname)), 'video/mp2t');
    return;
  }
  res.writeHead(404, { 'Content-Type': 'text/plain', ...noCacheHeaders() });
  res.end('Not found');
});

server.listen(LISTEN_PORT, LISTEN_HOST, () => {
  log(`HLS server running on port ${LISTEN_PORT}`);
});
EOF

# 4. Tạo script khởi động vừa chạy Node vừa mở Bore Tunnel
RUN cat << 'EOF' > /app/entrypoint.sh
#!/bin/bash
node /app/hls_server.js &
if [ -n "$BORE_PORT" ]; then
  bore local ${PORT:-10000} --to bore.pub --port $BORE_PORT &
fi
wait -n
EOF

RUN chmod +x /app/entrypoint.sh

EXPOSE 10000
CMD ["/app/entrypoint.sh"]
