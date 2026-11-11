(() => {
  const B = window.WebUI.musicBindings, esc = window.WebUI.escapeHtml;
  const node = (selector) => document.querySelector(selector);
  const storageKey = "endfield.music-binding-draft.v1";
  let data = { schema: B.schema, tracks: [] }, selected = "";
  const list = (value) => [...new Set(value.split(/[,，、\n]/).map((v) => v.trim()).filter(Boolean))];
  function status(message, error = false) { node("#editor-status").textContent = message; node("#editor-status").classList.toggle("is-error", error); }
  function draft() { try { localStorage.setItem(storageKey, JSON.stringify(data)); } catch { status("浏览器无法保存草稿，请及时导出 JSON。", true); } }
  function renderList() {
    const query = node("#track-search").value.toLowerCase();
    node("#track-list").innerHTML = data.tracks.filter((row) => JSON.stringify(row).toLowerCase().includes(query)).map((row) => `<button type="button" data-binding="${esc(row.id)}" aria-pressed="${row.id === selected}">${esc(B.label(row))}<small>${esc((row.aliases || []).join(" / "))} · ${(row.providers || []).length ? "已绑定平台" : "待绑定平台"}</small></button>`).join("");
    node("#track-list").querySelectorAll("[data-binding]").forEach((button) => button.addEventListener("click", () => select(button.dataset.binding)));
  }
  function select(id) {
    selected = id; const row = data.tracks.find((row) => row.id === id); if (!row) { node("#binding-form").reset(); return; }
    for (const [selector, value] of [["#binding-id", row.id], ["#track-title", row.title], ["#album", row.album || ""], ["#track-numbers", (row.trackNumbers || []).join(",")], ["#aliases", (row.aliases || []).join(", ")], ["#media-ids", (row.mediaIds || []).join(", ")], ["#source-names", (row.sourceNames || []).join(", ")], ["#event-names", (row.eventNames || []).join("\n")], ["#netease-song", B.provider(row)?.songId || ""], ["#binding-note", row.note || ""]]) node(selector).value = value;
    providerLink(); renderList();
  }
  function providerLink() {
    const id = B.songId(node("#netease-song").value), link = node("#provider-link"); link.hidden = !id;
    if (id) link.href = B.songUrl(id);
  }
  function save() {
    const previous = data.tracks.find((row) => row.id === selected) || {};
    const external = node("#netease-song").value.trim(), songId = B.songId(external);
    if (external && !songId) throw new Error("请填写有效的网易云歌曲 ID 或完整歌曲链接");
    const row = { ...previous, id: node("#binding-id").value.trim(), title: node("#track-title").value.trim(), album: node("#album").value.trim(), trackNumbers: list(node("#track-numbers").value).map(Number), aliases: list(node("#aliases").value), mediaIds: list(node("#media-ids").value), sourceNames: list(node("#source-names").value), eventNames: list(node("#event-names").value), providers: songId ? [{platform:"netease", songId}] : [], note: node("#binding-note").value.trim() };
    const next = { ...data, tracks: data.tracks.map((item) => item.id === selected ? row : item) };
    if (!next.tracks.some((item) => item === row)) next.tracks.push(row);
    B.validate(next); data = next; selected = row.id; draft(); renderList(); return row;
  }
  function download(value, filename) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], {type:"application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  node("#binding-form").addEventListener("submit", (event) => { event.preventDefault(); try { save(); status("草稿已保存在本机；地图站尚未发布这些修改。"); } catch (error) { status(error.message, true); } });
  node("#new-track").addEventListener("click", () => { const id = `track-${Date.now()}`; data.tracks.push({id,title:"新曲目",trackNumbers:[],aliases:[],mediaIds:[],sourceNames:[],eventNames:[],providers:[]}); select(id); draft(); });
  node("#delete-track").addEventListener("click", () => { data.tracks = data.tracks.filter((row) => row.id !== selected); draft(); select(data.tracks[0]?.id || ""); renderList(); status("条目已从本机草稿移除。"); });
  node("#track-search").addEventListener("input", renderList);
  node("#netease-song").addEventListener("input", providerLink);
  node("#export-all").addEventListener("click", () => { try { save(); download(B.validate(data), "music_bindings.json"); status("完整映射已导出。审核后替换 overrides/music_bindings.json 再发布。"); } catch (error) { status(error.message, true); } });
  node("#export-proposal").addEventListener("click", () => { try { const row = save(); download({schema:B.schema,tracks:[row]}, `music-binding-proposal-${row.id}.json`); status("单条提案已导出；请合并后发布，勿用单条提案覆盖完整目录。"); } catch (error) { status(error.message, true); } });
  node("#import").addEventListener("change", async (event) => {
    try { const file = event.target.files[0]; if (!file) return; if (file.size > 5 * 1024 * 1024) throw new Error("导入文件超过 5 MB"); const incoming = B.validate(JSON.parse(await file.text())); const merged = new Map(data.tracks.map((row) => [row.id,row])); for (const row of incoming.tracks) merged.set(row.id,row); data = B.validate({...data,tracks:[...merged.values()]}); draft(); select(incoming.tracks[0]?.id || data.tracks[0]?.id); status("已合并到本机草稿；没有上传到服务器。"); } catch (error) { status(error.message, true); }
  });
  (async () => {
    try { const response = await fetch("overrides/music_bindings.json", {cache:"no-store"}); if (!response.ok) throw new Error(`读取映射失败 (${response.status})`); data = B.validate(await response.json()); const saved = localStorage.getItem(storageKey); if (saved) { data = B.validate(JSON.parse(saved)); status("已恢复本机草稿；导入 JSON 可合并最新目录。"); } select(data.tracks[0]?.id || ""); renderList(); } catch (error) { status(error.message, true); }
  })();
})();
