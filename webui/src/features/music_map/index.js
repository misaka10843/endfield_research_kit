(() => {
  const music = window.WebUI.mapMusic;
  const esc = window.WebUI.escapeHtml;
  const root = document.querySelector("main");
  const panel = document.querySelector("#music-panel");
  const map = document.querySelector("#map");
  const picker = document.querySelector("#scene");
  const error = document.querySelector("#error");
  const width = 1200, height = 900, pad = 35;
  let payload = null, sceneId = "", selected = null, sceneSerial = 0;
  let bounds, project, view = [0, 0, width, height];
  let availableScenes = [];
  let allScenes = [], world = "map02";
  const payloadCache = new Map();
  const worldOf = (id) => /^map01_lv\d{3}$/.test(id) ? "map01" : /^map02_lv\d{3}$/.test(id) ? "map02" : "other";
  function options(preferred = sceneId) {
    const showSpecial = document.querySelector("#show-special").checked;
    if (!showSpecial && world === "other") world = "map02";
    availableScenes = allScenes.filter((scene) => worldOf(scene.id) === world && (showSpecial || world !== "other"));
    picker.innerHTML = availableScenes.map((scene) => `<option value="${esc(scene.id)}">${esc(scene.label)}${scene.hasRegions ? " · 有音乐范围" : ""}</option>`).join("");
    document.querySelectorAll("[data-world]").forEach((button) => { button.hidden = button.dataset.world === "other" && !showSpecial; button.setAttribute("aria-pressed", String(button.dataset.world === world)); });
    return availableScenes.find((scene) => scene.id === preferred)?.id || availableScenes[0]?.id;
  }

  async function json(path) {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) { const failure = new Error(`读取失败 (${response.status})：${path}`); failure.status = response.status; throw failure; }
    return response.json();
  }
  function updateView() { map.querySelector("svg")?.setAttribute("viewBox", view.join(" ")); }
  function fit() { view = [0, 0, width, height]; updateView(); }
  function zoom(factor) {
    const w = Math.max(30, Math.min(width * 5, view[2] * factor)); const ratio = w / view[2];
    view = [view[0] + (view[2] - w) / 2, view[1] + view[3] * (1 - ratio) / 2, w, view[3] * ratio]; updateView();
  }
  function regions() { return (music.getCatalog()?.regions || []).filter((region) => region.sceneId === sceneId); }
  function name(region) { return [...new Set((region.placeLabels || []).map((label) => label.text))].join(" / ") || `区域 ${region.slotId}`; }
  function focus() {
    const points = (selected ? [selected] : regions()).flatMap((region) => region.shapes.flatMap((shape) => shape.polygonXZ || [])).map(([x, z]) => project({ x, z }));
    if (!points.length) return fit();
    const minX = Math.min(...points.map((p) => p.x)), maxX = Math.max(...points.map((p) => p.x));
    const minY = Math.min(...points.map((p) => p.y)), maxY = Math.max(...points.map((p) => p.y));
    const w = Math.max(180, maxX - minX + 120), h = Math.max(140, maxY - minY + 120);
    view = [(minX + maxX - w) / 2, (minY + maxY - h) / 2, w, h]; updateView();
  }
  function choose(region) {
    selected = region;
    const url = new URL(location.href);
    if (region) url.searchParams.set("musicRegion", region.id); else url.searchParams.delete("musicRegion");
    url.searchParams.set("map", sceneId);
    history.replaceState(null, "", url);
  }
  function bindMap() {
    const svg = map.querySelector("svg");
    let drag = null, moved = false;
    const point = (event) => { const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); };
    svg.addEventListener("wheel", (event) => {
      event.preventDefault(); const at = point(event); const factor = event.deltaY > 0 ? 1.15 : 1 / 1.15;
      const w = Math.max(30, Math.min(width * 5, view[2] * factor)); const ratio = w / view[2];
      view = [at.x - (at.x - view[0]) * ratio, at.y - (at.y - view[1]) * ratio, w, view[3] * ratio]; updateView();
    }, { passive: false });
    svg.addEventListener("pointerdown", (event) => { if (event.button !== 0) return; drag = { x: event.clientX, y: event.clientY, view: [...view] }; moved = false; });
    svg.addEventListener("pointermove", (event) => {
      if (!drag) return;
      if (!moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) return;
      moved = true;
      const matrix = svg.getScreenCTM();
      const scaleX = Math.hypot(matrix.a, matrix.b), scaleY = Math.hypot(matrix.c, matrix.d);
      view = [drag.view[0] - (event.clientX - drag.x) / scaleX, drag.view[1] - (event.clientY - drag.y) / scaleY, drag.view[2], drag.view[3]]; updateView();
      if (!svg.hasPointerCapture(event.pointerId)) svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener("pointerup", () => { drag = null; });
    svg.addEventListener("pointercancel", () => { drag = null; moved = false; });
    svg.addEventListener("click", (event) => { if (moved) { event.stopImmediatePropagation(); event.preventDefault(); moved = false; } }, true);
  }
  function render() {
    const background = payload?.minimap?.src && payload.minimap.worldBounds ? payload.minimap : payload?.renderBackground?.src && payload.renderBackground.worldBounds ? payload.renderBackground : null;
    const shapePoints = regions().flatMap((region) => region.shapes.flatMap((shape) => shape.polygonXZ || []));
    bounds = background?.worldBounds || (shapePoints.length ? { minX: Math.min(...shapePoints.map((p) => p[0])) - 30, maxX: Math.max(...shapePoints.map((p) => p[0])) + 30, minZ: Math.min(...shapePoints.map((p) => p[1])) - 30, maxZ: Math.max(...shapePoints.map((p) => p[1])) + 30 } : { minX: 0, maxX: 1, minZ: 0, maxZ: 1 });
    const rangeX = bounds.maxX - bounds.minX || 1, rangeZ = bounds.maxZ - bounds.minZ || 1;
    const scale = Math.min((width - pad * 2) / rangeX, (height - pad * 2) / rangeZ);
    const x = (width - rangeX * scale) / 2, y = (height - rangeZ * scale) / 2;
    const rawProject = (p) => ({ x: x + (p.x - bounds.minX) * scale, y: y + (bounds.maxZ - p.z) * scale });
    project = (p) => {
      if (!payload?.mapConfig?.needInverseXZ) return rawProject(p);
      const cx = (bounds.minX + bounds.maxX) / 2, cz = (bounds.minZ + bounds.maxZ) / 2;
      return rawProject({ x: cx + p.z - cz, z: cz - p.x + cx });
    };
    const labels = [...new Map(regions().flatMap((region) => region.placeLabels || []).map((label) => [label.id, label])).values()];
    map.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${view.join(" ")}" role="group" aria-label="${esc(sceneId)} 音乐触发范围">${background ? `<image href="data/map_recovery/${esc(background.src)}" x="${x}" y="${y}" width="${rangeX * scale}" height="${rangeZ * scale}" preserveAspectRatio="none"/>` : `<text x="40" y="50" fill="#e8e8d5">此场景暂无地图底图，仅显示配置范围</text>`}${music.overlaySvg(project)}${labels.map((label) => { const p = project(label.position); return `<text class="music-place" x="${p.x}" y="${p.y}" text-anchor="middle">${esc(label.text)}</text>`; }).join("")}</svg>`;
    panel.innerHTML = music.markup();
    music.mount(root, { onRefresh: () => { updateCoverage(); selected = regions().find((region) => region.id === selected?.id) || null; render(); }, autoPlayRegion: () => document.querySelector("#auto-play").checked, onRegionSelect: choose, isolateSelectedRegion: false });
    // Opening a deep link is selection only; audio starts on an explicit click.
    panel.querySelector(".mr-music").open = true;
    bindMap();
  }
  function updateCoverage() {
    const catalog = music.getCatalog();
    const audit = catalog.regionAudit || {};
    const visible = (catalog.regions || []).filter((region) => document.querySelector("#show-special").checked || worldOf(region.sceneId) !== "other");
    document.querySelector("#coverage").textContent = `${visible.length} 个音乐区域 · ${new Set(visible.map((region) => region.sceneId)).size} 张大地图${audit.unsupportedRecords ? " · 持续整理中" : ""}`;
  }
  async function changeScene(id) {
    const serial = ++sceneSerial;
    music.pause(); error.hidden = true; picker.disabled = true;
    try {
      let next = payloadCache.get(id);
      if (!next) {
        try { next = await json(`data/map_recovery/maps/${encodeURIComponent(id)}.json`); }
        catch (failure) { if (failure.status !== 404) throw failure; next = {}; }
        payloadCache.set(id, next);
      }
      if (serial !== sceneSerial) return;
      if (!await music.load([id]) || serial !== sceneSerial) return;
      payload = next; sceneId = id; selected = regions().find((region) => region.id === new URL(location.href).searchParams.get("musicRegion")) || null;
      picker.value = id; document.querySelector("#scene-name").textContent = `${availableScenes.find((scene) => scene.id === id)?.label || id} · ${regions().length} 个范围`;
      view = [0, 0, width, height]; render(); choose(selected); if (selected) focus();
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { if (serial === sceneSerial) picker.disabled = false; }
  }
  async function start() {
    try {
      await music.load([]);
      if (!music.getCatalog()) throw new Error("音乐目录未发布，请先执行音乐地图构建命令。");
      updateCoverage();
      const manifest = await json("data/map_recovery/index.json");
      const maps = manifest.maps || manifest.levels || [];
      const regionScenes = new Set((music.getCatalog().regions || []).map((region) => region.sceneId));
      const ids = new Set([...regionScenes, ...maps.filter((row) => worldOf(row.id || row.levelId) !== "other").map((row) => row.id || row.levelId)]);
      allScenes = [...ids].sort().map((id) => {
        const row = maps.find((item) => (item.id || item.levelId) === id);
        const regionNames = [...new Set((music.getCatalog().regions || []).filter((region) => region.sceneId === id).map(name))].slice(0, 3).join("、");
        return { id, hasRegions: regionScenes.has(id), label: music.getCatalog().mapNames?.[id] || (row?.name && !row.name.includes("�") ? row.name : regionNames || row?.label || id) };
      });
      // Scene-only requests remain browsable even where no trigger volume is recovered.
      for (const scene of music.getCatalog().scenes || []) if (!ids.has(scene.sceneId) && maps.some((row) => (row.id || row.levelId) === scene.sceneId)) allScenes.push({ id: scene.sceneId, label: music.getCatalog().mapNames?.[scene.sceneId] || scene.sceneId });
      const requested = new URL(location.href).searchParams.get("map");
      world = requested && worldOf(requested) !== "other" ? worldOf(requested) : "map02";
      const initial = options(requested);
      if (!initial) throw new Error("尚未恢复可展示的场景，请检查构建报告。");
      await changeScene(initial);
    } catch (failure) { error.textContent = failure.message; error.hidden = false; document.querySelector("#coverage").textContent = "目录不可用"; }
  }
  picker.addEventListener("change", () => changeScene(picker.value));
  document.querySelectorAll("[data-world]").forEach((button) => button.addEventListener("click", () => { world = button.dataset.world; const next = options(); if (next) changeScene(next); }));
  document.querySelector("#show-special").addEventListener("change", () => { updateCoverage(); const next = options(); if (next !== sceneId && next) changeScene(next); });
  document.querySelector("#fit").addEventListener("click", fit);
  document.querySelector("#focus").addEventListener("click", focus);
  document.querySelector("#zoom-in").addEventListener("click", () => zoom(1 / 1.5));
  document.querySelector("#zoom-out").addEventListener("click", () => zoom(1.5));
  window.addEventListener("popstate", () => changeScene(new URL(location.href).searchParams.get("map") || sceneId));
  start();
})();
