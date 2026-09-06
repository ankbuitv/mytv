/**
 * CHRTV APK HOST — tải APK mới nhất tại https://ankb.qzz.io/apk
 *   GET /apk          -> 302 tới file .apk mới nhất (worker tốn ~0 băng thông)
 *   GET /apk?proxy=1  -> tải xuyên qua worker (ẩn GitHub hoàn toàn)
 *   GET /apk/info     -> JSON phiên bản/dung lượng (app tự kiểm tra cập nhật)
 *   GET /apk/sha256   -> checksum SHA-256
 * Cấu hình vars: APK_REPOS = "ankbuitv/chrtv-apk,ankbuitv/ott"
 */
const MEMORY = new Map(); // repo -> { at, data } — cache 5 phút

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=60",
    },
  });
}

async function ghApi(path, env) {
  const headers = { "User-Agent": "chrtv-apk-host", Accept: "application/vnd.github+json" };
  if (env && env.GH_TOKEN) headers.Authorization = "Bearer " + env.GH_TOKEN;
  const r = await fetch("https://api.github.com" + path, { headers, signal: AbortSignal.timeout(8000) });
  if (!r.ok) throw new Error("GitHub " + r.status);
  return r.json();
}

async function latestRelease(repo, env) {
  const c = MEMORY.get(repo);
  if (c && Date.now() - c.at < 5 * 60 * 1000) return c.data;
  const data = await ghApi("/repos/" + repo + "/releases/latest", env);
  MEMORY.set(repo, { at: Date.now(), data });
  return data;
}

function pickApk(assets) {
  const list = (assets || []).filter((a) => /\.apk$/i.test(a.name || ""));
  if (!list.length) return null;
  return list.sort((a, b) => (b.size || 0) - (a.size || 0))[0];
}

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") return json({ error: "Method not allowed" }, 405);
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "");
    const repos = String((env && env.APK_REPOS) || "ankbuitv/chrtv-apk,ankbuitv/ott")
      .split(",").map((s) => s.trim()).filter(Boolean);

    if (path !== "/apk" && path !== "/apk/info" && path !== "/apk/sha256") {
      return json({ error: "Not found", hint: "Đường dẫn: /apk · /apk/info · /apk/sha256" }, 404);
    }

    let rel = null, usedRepo = "";
    for (const repo of repos) {
      try {
        const data = await latestRelease(repo, env);
        if (pickApk(data.assets)) { rel = data; usedRepo = repo; break; }
      } catch (e) { /* thử repo kế tiếp */ }
    }
    if (!rel) return json({ error: "Chưa tìm thấy APK nào được phát hành", repos }, 503);

    const apk = pickApk(rel.assets);
    const sha = (rel.assets || []).find((a) => /\.sha256$/i.test(a.name || ""));
    const version = (String(apk.name).match(/(\d+\.\d+\.\d+)/) || [])[1] || "";

    if (path === "/apk") {
      if (url.searchParams.get("proxy") === "1") {
        const upstream = await fetch(apk.browser_download_url);
        const headers = new Headers(upstream.headers);
        headers.set("Content-Disposition", 'attachment; filename="' + apk.name + '"');
        headers.set("Cache-Control", "public, max-age=300");
        return new Response(upstream.body, { status: upstream.status, headers });
      }
      return Response.redirect(apk.browser_download_url, 302);
    }

    if (path === "/apk/info") {
      return json({
        repo: usedRepo, tag: rel.tag_name, published_at: rel.published_at, version,
        apk: { name: apk.name, size: apk.size, url: "/apk", sha256_url: sha ? "/apk/sha256" : null },
        release: rel.html_url,
      });
    }

    if (!sha) return json({ error: "Bản build không kèm file .sha256" }, 404);
    const t = await (await fetch(sha.browser_download_url)).text();
    return new Response(t.trim() + "\n", {
      headers: { "Content-Type": "text/plain; charset=utf-8", "Access-Control-Allow-Origin": "*" },
    });
  },
};
