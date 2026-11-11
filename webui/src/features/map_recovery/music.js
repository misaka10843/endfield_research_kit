// Audio owns this publication. Map reads its latest scene/event projection.
(() => {
  const W = window.WebUI;
  const esc = W.escapeHtml;
  const SCHEMA = "endfield.webui.scene-music.v1";
  const PAGE_SIZE = 20;
  const cache = new Map();
  const state = { catalog: null, levels: [], events: new Map(), query: "", scope: "current", page: 0, open: false, error: "", active: null, regionId: "", direction: "enter", showRegions: true };
  const zh = () => String(window.WEBUI_UI_LOCALE || document.documentElement.lang || "zh").toLowerCase().startsWith("zh");
  const tr = (en, cn) => zh() ? cn : en;
  const language = () => String(document.querySelector("#language")?.value || "CN").toUpperCase();
  let serial = 0;
  let host = null;
  let refreshMap = null;
  let playOnSelect = false;
  let onSelect = null;
  let isolateRegion = true;

  function pause() { state.active?.pause(); state.active = null; W.musicPlayer?.stopExternal(); }

  async function load(levels, force = false) {
    const request = ++serial;
    pause();
    state.levels = [...levels];
    state.page = 0;
    state.error = "";
    const lang = language();
    const path = `data/lang/${encodeURIComponent(lang)}/audio/scene_music.json`;
    if (force) cache.delete(path);
    try {
      let catalog = cache.get(path);
      if (!catalog) {
        const response = await fetch(path, { cache: "no-store" });
        if (!response.ok) throw new Error(response.status === 404 ? "notPublished" : "loadFailed");
        catalog = await response.json();
        if (catalog.schema !== SCHEMA || !Array.isArray(catalog.scenes) || !Array.isArray(catalog.events)) throw new Error("unsupported");
        cache.set(path, catalog);
      }
      if (request !== serial) return false;
      state.catalog = catalog;
      state.events = new Map(catalog.events.map((event) => [event.id, event]));
      const requested = new URL(location.href).searchParams.get("musicRegion");
      if (requested && regions().some((region) => region.id === requested)) { state.regionId = requested; state.open = true; }
      if (state.regionId && !regions().some((region) => region.id === state.regionId)) state.regionId = "";
    } catch (error) {
      if (request !== serial) return false;
      state.catalog = null;
      state.events.clear();
      state.error = error.message === "notPublished"
        ? tr("Build Audio, then refresh this catalog.", "Audio 尚未发布场景音乐目录；构建完成后可在此刷新。")
        : tr("Scene music is unavailable. Try refreshing the catalog.", "场景音乐读取失败，请刷新目录重试。");
    }
    return request === serial;
  }

  function rows() {
    const levels = new Set(state.levels);
    const scenes = (state.catalog?.scenes || []).filter((scene) => state.scope !== "current" || levels.has(scene.sceneId));
    let result = scenes.flatMap((scene) => (scene.events || []).map((reference) => ({ scene, reference, event: state.events.get(reference.eventId) })).filter((row) => row.event));
    if (state.scope === "unplaced") {
      const owned = new Set((state.catalog?.scenes || []).flatMap((scene) => scene.events.map((event) => event.eventId)));
      result = [...state.events.values()].filter((event) => !owned.has(event.id)).map((event) => ({ scene: null, reference: { roles: [] }, event }));
    }
    if (state.scope === "playable") result = [...state.events.values()].filter((event) => candidateMedia(event).length).map((event) => ({ scene: null, reference: { roles: [] }, event }));
    const region = regions().find((item) => item.id === state.regionId);
    if (region) result = (region.requests || []).filter((request) => request.direction === state.direction)
      .map((request) => ({ region, request, scene: { sceneId: region.sceneId }, reference: { roles: [request.direction === "enter" ? "areaEnterRequest" : "areaLeaveRequest"] }, event: state.events.get(request.eventId) })).filter((row) => row.event);
    const query = state.query.trim().toLowerCase();
    return query ? result.filter((row) => JSON.stringify([row.scene?.sceneId, row.event.id, row.event.name, row.reference.roles, row.event.operations, row.region?.placeLabels, W.musicBindings?.forEvent(row.event, state.catalog), candidateMedia(row.event).map((item, index) => [item.mediaId, W.musicBindings?.mediaLabel(item, row.event, index, state.catalog).title])]).toLowerCase().includes(query)) : result;
  }

  function regions() { return (state.catalog?.regions || []).filter((region) => state.levels.includes(region.sceneId)); }
  function regionTitle(region) {
    const names = [...new Set((region.placeLabels || []).map((label) => label.text).filter(Boolean))];
    const label = names.length && names.length <= 2 ? names.join(" / ") : `${tr("Music zone", "音乐区域")} ${region.slotId}`;
    return `${label}${region.enterStateKeys?.length ? ` · ${region.enterStateKeys.join(" / ")}` : ""}`;
  }

  function overlaySvg(project) {
    const area = (points) => Math.abs(points.reduce((sum, p, i) => { const next = points[(i + 1) % points.length]; return sum + p.x * next.y - next.x * p.y; }, 0)) / 2;
    const shapes = regions().flatMap((region) => (region.shapes || []).filter((item) => Array.isArray(item.polygonXZ) && item.polygonXZ.length >= 3).map((item, index) => {
        const points = item.polygonXZ.map(([x, z]) => project({ x, z }, region.sceneId));
        return { region, index, points, area: area(points) };
      })).filter((row) => row.points.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y)));
    // Sort each individual shape, not a region's total area: SVG paints last
    // on top. A region may contain both a huge box and a tiny box.
    shapes.sort((a, b) => b.area - a.area || a.region.id.localeCompare(b.region.id) || a.index - b.index);
    return `<g class="mr-music-regions">${shapes.map(({region, index, points}) => {
      const title = `${regionTitle(region)} · ${tr("Authored music trigger volume", "配置中的音乐触发体积")}`;
      return `<g class="mr-music-zone" data-music-zone="${esc(region.id)}" data-music-shape="${index}" tabindex="0" role="button" aria-label="${esc(title)}"><title>${esc(title)}</title><polygon points="${points.map((point) => `${point.x.toFixed(3)},${point.y.toFixed(3)}`).join(" ")}"/></g>`;
    }).join("")}</g>`;
  }

  function syncOverlay() {
    host?.querySelectorAll("[data-music-zone]").forEach((node) => {
      node.style.display = state.showRegions && (!isolateRegion || !state.regionId || node.dataset.musicZone === state.regionId) ? "" : "none";
      node.classList.toggle("is-selected", node.dataset.musicZone === state.regionId);
    });
  }

  function markup() {
    return `<details class="mr-music"${state.open ? " open" : ""}><summary>${esc(tr("Scene BGM", "场景 BGM"))} <b>${rows().length}</b></summary><div class="mr-music-content"></div></details>`;
  }

  function audioHref(id) {
    const url = new URL(location.href);
    url.pathname = new URL("./", location.href).pathname;
    url.searchParams.set("audio", id);
    url.searchParams.set("audioKind", "events");
    url.hash = "audio";
    return url.href;
  }

  function mediaHref(src) {
    if (typeof src !== "string" || !src.trim()) return "";
    try {
      const url = new URL(src, location.href);
      return url.origin === location.origin && ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  }

  function candidateMedia(event) {
    const seen = new Set();
    return [...(event.media || []), ...(event.conditionalMedia || [])].filter((item) => {
      const path = mediaHref(item.src);
      const external = W.musicBindings?.provider(item.platformBinding);
      const identity = path || (external ? `netease:${external.songId}` : "");
      if (!identity || seen.has(identity)) return false;
      seen.add(identity); return true;
    });
  }

  function routeText(item) {
    if (item?.relationType !== "conditionalStateMusicLeaf") return tr("Direct Event graph candidate; runtime selection is unobserved.", "事件图中的直接候选；尚未观测运行时选曲。");
    return `${tr("Conditional music route; assumes the request executes and the music engine is playing. Other predicates remain unobserved.", "条件音乐路径：假设此请求执行且主音乐引擎正在播放；其他条件尚未观测。")}\n${JSON.stringify({ engine: item.sourceEventId, mediaId: item.mediaId, conditions: item.conditions, objectPath: item.objectPath }, null, 2)}`;
  }

  const roleText = (role) => ({ areaEnterRequest: tr("Enter music volume", "进入音乐体积"), areaLeaveRequest: tr("Leave music volume", "离开音乐体积"), levelInitEvent: tr("Scene entry", "进入场景"), levelInitEvents: tr("Scene entry", "进入场景"), levelExitEvents: tr("Scene exit", "退出场景"), battleMusicTriggerEvent: tr("Battle music trigger", "战斗音乐触发"), conditionalCueRequest: tr("Conditional Cue", "条件 Cue") }[role] || role);
  const timingText = (field) => ({ delayMs: tr("Request delay", "请求延迟"), fadeInTimeMs: tr("Fade in", "淡入时长"), fadeOutTimeMs: tr("Fade out", "淡出时长"), transitionTimeMs: tr("Transition time", "过渡时长") }[field] || field);

  function card({ scene, reference, event, region, request }) {
    const media = candidateMedia(event);
    const titles = W.musicBindings?.forEvent(event, state.catalog) || [];
    const title = titles.length ? titles.map((row) => W.musicBindings.label(row)).join(" / ") : W.standaloneMusicAtlas && W.musicBindings ? W.musicBindings.requestName(event) : event.name || event.id;
    const mediaLabel = (item, index) => W.musicBindings?.mediaLabel(item, event, index, state.catalog) || { title: item.name || item.id || String(index + 1), detail: "" };
    const conditions = (event.cueRules || []).map((rule) => {
      const nodes = (rule.conditions || []).map((node) => {
        const operands = ["stringValue", "intValue", "floatValue", "boolValue"].filter((key) => node[key] !== undefined && node[key] !== null && node[key] !== "").map((key) => `${key}=${JSON.stringify(node[key])}`);
        return `${node.path || ""}: ${node.exprTypeName || `exprType=${node.exprType}`} ${operands.join(" · ")}`.trim();
      });
      return `<li><code>Cue ${esc(rule.cueId)}</code> · ${esc(rule.levelId || tr("Global definition", "全局定义"))}<pre>${esc(nodes.join("\n") || tr("No condition operands", "无条件操作数"))}</pre></li>`;
    }).join("");
    const timings = (event.timingParameters || []).map((item) => `<li title="${esc(item.path)}"><span>${esc(timingText(item.field))}</span> <code>${esc(String(item.value))}${item.unit ? ` ${esc(item.unit)}` : ""}</code></li>`).join("");
    const controls = (event.controlActions || []).map((action) => {
      const group = action.controlGroupSemantic?.recoveredName || action.controlGroupSemantic?.semanticLabel;
      const value = action.controlValueSemantic?.semanticName;
      return `<li><code>${esc(action.operation)} ${esc(group || action.groupIdHex || action.idExtHex || "?")} → ${esc(value || action.stateIdHex || action.switchIdHex || "?")}</code></li>`;
    }).join("");
    const conditionalHint = media.some((item) => item.relationType === "conditionalStateMusicLeaf")
      ? `<p class="mr-note">${esc(tr("Includes conditional State-to-music candidates. The music engine and remaining selector conditions must also apply; these are not observed playback or region-exclusive songs.", "包含状态决策树中的条件候选：还需主音乐引擎及其他选曲条件满足；不是已观测播放，也不代表区域专属曲目。"))}</p>` : "";
    return `<article class="mr-music-card" data-music-id="${esc(event.id)}">
      <header><span>${esc(state.catalog?.mapNames?.[scene?.sceneId] || scene?.sceneId || (state.scope === "playable" ? tr("Event preview", "事件试听") : tr("Scene unresolved", "场景待定位")))}</span><a href="${esc(audioHref(event.id))}" title="${esc(event.name || event.id)}">${esc(title)}</a></header>
      ${W.musicPlayer?.related(event, state.catalog) || ""}
      ${W.standaloneMusicAtlas ? '<details class="music-request-evidence"><summary>触发条件与静态证据</summary>' : ""}
      <p class="mr-note">${esc(reference.roles.map(roleText).join(" · "))}${event.operations?.length ? ` · ${esc(event.operations.join(" / "))}` : ""}</p>
      <p class="mr-note">${esc(region ? `${regionTitle(region)} · ${tr("Authored trigger extent recovered", "配置触发范围已恢复")}` : tr("Spatial extent unresolved · audible silence gap unresolved", "空间范围待恢复 · 实际过渡空白时长待恢复"))}</p>
      ${request ? `<p class="mr-note">${esc(tr("Script wait before request", "发出请求前的脚本等待"))}: ${Number(request.scriptDelaySeconds).toFixed(3)} s${request.conditions?.length ? ` · ${esc(tr("Conditional branch", "有条件分支"))}` : ""}</p><details><summary>${esc(tr("Trigger path", "触发路径"))}</summary><pre>${esc(JSON.stringify({ path: request.path, conditions: request.conditions, eventOnRelease: request.eventOnRelease }, null, 2))}</pre></details>` : ""}
      ${conditionalHint}
      ${controls ? `<ul>${controls}</ul><p class="mr-note">${esc(tr("Stored control group/value IDs; runtime selection remains unobserved.", "配置中的控制组与取值 ID；尚未观测运行时选曲。"))}</p>` : ""}
      ${W.standaloneMusicAtlas ? "</details>" : ""}
      ${media.length ? `<label>${esc(tr("Possible linked media", "关联媒体候选"))} <b>${media.length}</b><select data-music-media aria-label="${esc(tr("Preview candidate", "试听候选文件"))}">${media.map((item, index) => `<option value="${index}">${esc(item.name || item.id || String(index + 1))}${Number.isFinite(item.duration) ? ` · ${item.duration.toFixed(1)}s` : ""}</option>`).join("")}</select></label><audio controls preload="none"${mediaHref(media[0].src) ? ` src="${esc(mediaHref(media[0].src))}"` : ""} aria-label="${esc(event.id)}"></audio><p class="mr-note">${esc(tr("Preview one possible file; dynamic branch and mixing remain unobserved.", "试听单个候选文件；动态分支与混音选择尚未观测。"))}</p>` : `<p class="mr-note">${esc([...(event.media || []), ...(event.conditionalMedia || [])].some((item) => item.localPreviewOmitted) ? tr("Local audio is omitted from this static site. Use a bound platform recording, or add a mapping.", "此静态包未包含本地音频；可使用已绑定的平台曲目，或在映射工具中补充绑定。") : tr("Control event or no decoded media candidate", "控制事件，或尚无可试听的媒体候选"))}</p>`}
      ${media.length ? `<details class="mr-music-playlist"${region ? " open" : ""}><summary>${esc(tr("Candidate playlist", "候选播放列表"))} · ${media.length}</summary><ol>${media.map((item, index) => { const display = mediaLabel(item, index); return `<li><button type="button" data-music-track="${index}" aria-pressed="${index === 0}"><span>▶ ${esc(display.title)}${display.detail ? `<small>${esc(display.detail)}</small>` : ""}</span><span>${Number.isFinite(item.duration) ? `${Math.floor(item.duration / 60)}:${String(Math.floor(item.duration % 60)).padStart(2, "0")}` : ""}</span></button></li>`; }).join("")}</ol></details>` : ""}
      <details><summary>${esc(tr("Conditions and transition parameters", "条件与过渡参数"))}</summary>${conditions ? `<ul>${conditions}</ul><p class="mr-note">${esc(tr("Stored Cue conditions; not evaluated here.", "保留配置中的 Cue 条件，此处不求值。"))}</p>` : ""}${timings ? `<ul class="mr-music-timings">${timings}</ul>` : `<p class="mr-note">${esc(tr("No stored timing parameters recovered for this event.", "此事件尚未恢复可展示的时间参数。"))}</p>`}<p class="mr-note">${esc(tr("Fade, delay and synchronization values alone do not establish an audible silence duration.", "淡入、淡出、延迟与同步参数仍不足以确定实际听到的空白时长。"))}</p></details>
    </article>`;
  }

  function renderContent() {
    const content = host?.querySelector(".mr-music-content");
    if (!content) return;
    pause();
    const all = rows();
    const count = host.querySelector(".mr-music > summary b");
    if (count) count.textContent = String(all.length);
    state.page = Math.min(state.page, Math.max(0, Math.ceil(all.length / PAGE_SIZE) - 1));
    const page = all.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
    const selectedRegion = regions().find((region) => region.id === state.regionId);
    content.innerHTML = `<div class="mr-music-tools"><input data-music-query type="search" placeholder="${esc(tr("Scene, event or media ID", "搜索场景、事件或 mediaId"))}" value="${esc(state.query)}" aria-label="${esc(tr("Search scene music", "搜索场景音乐"))}"><select data-music-scope aria-label="${esc(tr("Scene scope", "场景范围"))}">${[["current", tr("Current map", "当前地图")], ["all", tr("All scenes", "全部场景")], ["unplaced", tr("Scene unresolved", "场景待定位")], ["playable", tr("Playable events", "可试听事件")]].map(([value, label]) => `<option value="${value}"${state.scope === value ? " selected" : ""}>${esc(label)}</option>`).join("")}</select><button type="button" data-music-refresh>${esc(tr("Refresh", "刷新目录"))}</button><button type="button" data-music-export>${esc(tr("Export JSON", "导出 JSON"))}</button></div>
      ${regions().length ? `<div class="mr-music-tools"><label>${esc(tr("Music trigger zone", "音乐触发区域"))} <select data-music-region aria-label="${esc(tr("Music trigger zone", "音乐触发区域"))}"><option value="">${esc(tr("Whole-scene requests", "整场景请求"))}</option>${regions().map((region) => `<option value="${esc(region.id)}"${region.id === state.regionId ? " selected" : ""}>${esc(regionTitle(region))} · ${esc(region.sceneId)} · ${region.slotId}</option>`).join("")}</select></label><label><input type="checkbox" data-music-show-regions${state.showRegions ? " checked" : ""}>${esc(tr("Show trigger volumes", "显示触发范围"))}</label>${selectedRegion ? `<select data-music-direction aria-label="${esc(tr("Trigger direction", "触发方向"))}">${[["enter", tr("On entering", "进入区域")], ["leave", tr("On leaving", "离开区域")]].map(([value, label]) => `<option value="${value}"${value === state.direction ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>` : ""}</div>` : ""}
      ${selectedRegion ? `<p class="mr-note">${esc(regionTitle(selectedRegion))} · ${selectedRegion.shapes.length} ${esc(tr("authored shapes; click the map overlay to select a zone.", "个配置形状；可点击地图范围选择区域。"))}</p><p class="mr-note">${esc(tr("Map-label anchors are contained in this XZ projection. Vertical limits and stored branch conditions still apply. This is a music trigger range, not observed audible coverage; script wait is not a measured silence gap.", "区域名称来自范围内的地图标注位置；高度限制与配置分支仍然适用。此处显示音乐触发范围，尚不代表实际可听边界；脚本等待也不等于实测静音。"))}</p><details><summary>${esc(tr("Geometry and source", "范围与来源"))}</summary><pre>${esc(JSON.stringify({ source: selectedRegion.source, volumePolicy: selectedRegion.volumePolicy, shapes: selectedRegion.shapes }, null, 2))}</pre></details>` : ""}
      ${state.error ? `<p class="mr-note" role="status">${esc(state.error)}</p>` : ""}
      ${state.catalog?.regionAudit && state.catalog.regionAudit.status !== "validatedProfileSubset" ? `<details><summary>${esc(tr("Region trigger evidence is unavailable for this publication", "本次目录的区域触发证据不可用"))}</summary><pre>${esc(JSON.stringify({ status: state.catalog.regionAudit.status, detail: state.catalog.regionAudit.detail || state.catalog.regionAudit.native?.detail }, null, 2))}</pre></details>` : ""}
      ${["audioLevelAndExplicitCueLevelsOnly", "audioLevelCueAndPooledRegionRequests"].includes(state.catalog?.coverage) ? `<p class="mr-note">${esc(tr("Quick catalog: scene tables, Cue definitions and admitted region scripts. A full Audio build also adds other scene sources.", "快速目录：场景表、Cue 配置及通过校验的区域脚本；完整 Audio 构建还会加入其他场景来源。"))}</p>` : ""}
      <p class="mr-note">${esc(tr("Many music events change State/Switch rather than play a file. Use Playable events to browse linked audio candidates.", "许多音乐事件只切换 State/Switch，并不直接播放文件。切换“可试听事件”可浏览已关联音频。"))}</p>
      ${page.map(card).join("") || (!state.error ? `<p class="mr-note">${esc(tr("No verified scene music request in this selection. Try all scenes or unresolved events.", "当前选择下暂无已验证的场景音乐关联，可切换全部场景或待定位事件。"))}</p>` : "")}
      ${all.length > PAGE_SIZE ? `<div class="mr-music-tools"><button type="button" data-music-page="-1"${state.page ? "" : " disabled"}>←</button><span>${state.page + 1} / ${Math.ceil(all.length / PAGE_SIZE)} · ${all.length}</span><button type="button" data-music-page="1"${(state.page + 1) * PAGE_SIZE < all.length ? "" : " disabled"}>→</button></div>` : ""}`;
    content.querySelector("[data-music-query]").addEventListener("input", (event) => {
      const cursor = event.target.selectionStart;
      state.query = event.target.value; state.page = 0; renderContent();
      const input = content.querySelector("[data-music-query]");
      input.focus(); input.setSelectionRange(cursor, cursor);
    });
    content.querySelector("[data-music-scope]").addEventListener("change", (event) => { state.scope = event.target.value; state.regionId = ""; state.page = 0; renderContent(); });
    content.querySelector("[data-music-region]")?.addEventListener("change", (event) => selectRegion(event.target.value));
    content.querySelector("[data-music-direction]")?.addEventListener("change", (event) => { state.direction = event.target.value; state.page = 0; renderContent(); });
    content.querySelector("[data-music-show-regions]")?.addEventListener("change", (event) => { state.showRegions = event.target.checked; syncOverlay(); });
    content.querySelector("[data-music-refresh]").addEventListener("click", async () => { await load(state.levels, true); if (refreshMap) refreshMap(); else renderContent(); });
    content.querySelector("[data-music-export]").addEventListener("click", () => {
      const payload = {
        schema: "endfield.webui.scene-music-selection.v1", sourceSchema: SCHEMA,
        language: state.catalog?.language, filters: { scope: state.scope, levels: state.levels, query: state.query, regionId: state.regionId, direction: state.direction },
        region: selectedRegion || null,
        rows: all.map(({ scene, reference, event, request }) => ({ sceneId: scene?.sceneId || null, reference, event, request })),
        evidenceBoundary: state.catalog?.evidenceBoundary,
      };
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url; link.download = `scene-music-${state.scope}.json`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    content.querySelectorAll("[data-music-page]").forEach((button) => button.addEventListener("click", () => { state.page += Number(button.dataset.musicPage); renderContent(); }));
    content.querySelectorAll(".mr-music-card").forEach((article) => {
      const event = state.events.get(article.dataset.musicId);
      const media = candidateMedia(event);
      const audio = article.querySelector("audio");
      if (audio) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = tr("Candidate path evidence", "候选路径证据");
        const proof = document.createElement("pre");
        proof.textContent = routeText(media[0]);
        details.append(summary, proof);
        audio.after(details);
      }
      audio?.addEventListener("play", () => { if (state.active !== audio) pause(); state.active = audio; });
      const chooseTrack = (index, playing) => {
        const item = media[index];
        if (!audio || !item) return;
        pause();
        if (item.src) audio.src = mediaHref(item.src); else audio.removeAttribute("src");
        article.querySelector("[data-music-media]").value = String(index);
        article.querySelectorAll("[data-music-track]").forEach((button) => button.setAttribute("aria-pressed", String(Number(button.dataset.musicTrack) === index)));
        const proof = audio.nextElementSibling?.querySelector("pre");
        if (proof) proof.textContent = routeText(item);
        audio.load();
        if (W.musicPlayer) article.dispatchEvent(new CustomEvent("music:track-selected", { detail: { index } }));
        if (playing && !item.src && item.platformBinding) W.musicPlayer?.playExternal(item.platformBinding, article.querySelector("[data-platform-player]"));
        else if (playing) audio.play().catch(() => {});
      };
      article.querySelector("[data-music-media]")?.addEventListener("change", (change) => chooseTrack(Number(change.target.value), !audio.paused));
      article.querySelectorAll("[data-music-track]").forEach((button) => button.addEventListener("click", () => chooseTrack(Number(button.dataset.musicTrack), true)));
      W.musicPlayer?.attach(article, audio, { media, event, selectTrack: chooseTrack, catalog: state.catalog });
      W.musicPlayer?.attachRelated(article, event, state.catalog);
      article.querySelector("a")?.addEventListener("click", pause);
    });
    syncOverlay();
  }

  function selectRegion(id, { play = true } = {}) {
    if (id && !regions().some((region) => region.id === id)) return false;
    state.regionId = id; state.scope = "current"; state.page = 0; state.open = true; state.query = ""; state.direction = "enter";
    const details = host?.querySelector(".mr-music");
    if (details) details.open = true;
    renderContent();
    onSelect?.(regions().find((region) => region.id === id) || null);
    if (play && id && (typeof playOnSelect === "function" ? playOnSelect() : playOnSelect)) {
      const first = rows().map((row) => ({ row, item: candidateMedia(row.event)[0] })).find(({item}) => item);
      if (first?.item.src) host?.querySelector(".mr-music-content audio")?.play().catch(() => {});
      else if (first?.item.platformBinding) W.musicPlayer?.playExternal(first.item.platformBinding, host?.querySelector("[data-platform-player]"));
      else {
        const related = rows().flatMap((row) => W.musicBindings?.forEvent(row.event, state.catalog) || []).find((row) => W.musicBindings?.provider(row));
        if (related) W.musicPlayer?.playExternal(related, host?.querySelector("[data-platform-player]"));
      }
    }
    return true;
  }

  function mount(root, { onRefresh = null, autoPlayRegion = false, onRegionSelect = null, isolateSelectedRegion = true } = {}) {
    host = root;
    refreshMap = onRefresh;
    playOnSelect = autoPlayRegion;
    onSelect = onRegionSelect;
    isolateRegion = isolateSelectedRegion;
    if (state.active && !state.active.isConnected) pause();
    const details = host.querySelector(".mr-music");
    if (!details) return;
    details.addEventListener("toggle", () => { state.open = details.open; if (!details.open) pause(); });
    renderContent();
    host.querySelectorAll("[data-music-zone]").forEach((node) => {
      const choose = (event) => {
        event.stopPropagation();
        selectRegion(node.dataset.musicZone);
      };
      node.addEventListener("click", choose);
      node.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); choose(event); } });
    });
  }
  window.addEventListener("webui:view-changed", (event) => { if (event.detail?.view !== "map-recovery") pause(); });
  window.addEventListener("pagehide", pause);
  W.mapMusic = { load, markup, mount, overlaySvg, selectRegion, pause, getCatalog: () => state.catalog };
})();
