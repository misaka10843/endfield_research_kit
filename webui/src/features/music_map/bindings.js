(() => {
  const W = window.WebUI;
  const schema = "endfield.music-bindings.v1";
  function validate(data) {
    if (!data || data.schema !== schema || !Array.isArray(data.tracks) || data.tracks.length > 10000) throw new Error("映射格式或条目数量无效");
    const ids = new Set(), media = new Map(), sources = new Map();
    for (const row of data.tracks) {
      if (!row || !/^[\w-]{1,100}$/.test(row.id || "") || ids.has(row.id) || typeof row.title !== "string" || !row.title.trim() || row.title.length > 200) throw new Error("曲目 ID 必须唯一，名称不能为空");
      ids.add(row.id);
      for (const key of ["aliases", "eventNames", "sourceNames", "mediaIds"]) if (!Array.isArray(row[key] || []) || (row[key] || []).some((value) => typeof value !== "string" || !value || value.length > 300)) throw new Error(`无效的 ${key}`);
      if ((row.mediaIds || []).some((value) => !/^\d+$/.test(value))) throw new Error("媒体 ID 必须为数字");
      if (!Array.isArray(row.trackNumbers || []) || (row.trackNumbers || []).some((value) => !Number.isInteger(value) || value < 1 || value > 999)) throw new Error("专辑序号无效");
      if (!Array.isArray(row.providers || []) || (row.providers || []).some((p) => p?.platform !== "netease" || !/^[1-9]\d{0,19}$/.test(p.songId || ""))) throw new Error("平台绑定需要有效的网易云歌曲 ID");
      for (const [key, owners] of [["mediaIds", media], ["sourceNames", sources]]) for (const value of row[key] || []) { if (owners.has(value) && owners.get(value) !== row.id) throw new Error(`${key} 重复绑定：${value}`); owners.set(value, row.id); }
    }
    return data;
  }
  function tracks(catalog) { return catalog?.musicBindings?.tracks || []; }
  function forEvent(event, catalog) { return tracks(catalog).filter((row) => (row.eventNames || []).includes(event.name) || (row.eventNames || []).includes(event.id)); }
  function forMedia(item, catalog) {
    const id = String(item.mediaId || item.id || "");
    const filename = item.originalName || item.sourceFileName || item.name || "";
    const stem = filename.split(/[\\/]/).pop().replace(/\.[^.]*$/, "");
    const matches = tracks(catalog).filter((row) => (row.mediaIds || []).includes(id) || (row.sourceNames || []).includes(stem));
    return matches.length === 1 ? matches[0] : null;
  }
  function numbers(row) { return (row.trackNumbers || []).map((n) => String(n).padStart(2, "0")).join("–"); }
  function label(row) { return `${numbers(row)} ${row.title}`.trim(); }
  function requestName(event) { return (event.name || event.id || "音乐").replace(/^au_music_map\d+_lv\d+_(?:exploring_|explore_)?/, "").replace(/^au_music_/, ""); }
  function mediaLabel(item, event, index, catalog) {
    const row = item.platformBinding || forMedia(item, catalog);
    const raw = item.originalName || item.sourceFileName || item.name || String(item.mediaId || "");
    return { title: row ? label(row) : /^\d+(?:\.\w+)?$/.test(raw) ? `${requestName(event)} · 候选 ${String(index + 1).padStart(2, "0")}` : raw,
      detail: row ? `${row.album || "人工绑定"} · ${raw}` : `${raw} · 曲名待绑定`, binding: row };
  }
  function songId(value) {
    const text = String(value || "").trim();
    if (/^[1-9]\d{0,19}$/.test(text)) return text;
    try {
      const url = new URL(text);
      if (!["music.163.com", "y.music.163.com", "m.music.163.com"].includes(url.hostname) || url.protocol !== "https:") return null;
      const query = new URLSearchParams(url.search || url.hash.replace(/^#\/[^?]*\?/, "?"));
      const id = query.get("id"); return /^[1-9]\d{0,19}$/.test(id || "") ? id : null;
    } catch { return null; }
  }
  function provider(row) { return (row?.providers || []).find((p) => p.platform === "netease" && /^[1-9]\d{0,19}$/.test(p.songId || "")) || null; }
  function playerUrl(id) { if (!/^[1-9]\d{0,19}$/.test(String(id))) throw new Error("无效歌曲 ID"); return `https://music.163.com/outchain/player?type=2&id=${id}&auto=1&height=66`; }
  function songUrl(id) { return `https://music.163.com/#/song?id=${encodeURIComponent(id)}`; }
  W.musicBindings = { schema, validate, forEvent, forMedia, label, mediaLabel, requestName, songId, provider, playerUrl, songUrl };
})();
