(() => {
  const W = window.WebUI, esc = W.escapeHtml;
  let external = null;
  const clock = (value) => Number.isFinite(value) && value >= 0 ? `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}` : "0:00";
  function stopExternal() { if (external) { external.replaceChildren(); external = null; } }
  function playExternal(row, host) {
    const provider = W.musicBindings.provider(row); if (!provider || !host) return;
    W.mapMusic.pause(); stopExternal();
    const frame = document.createElement("iframe");
    frame.src = W.musicBindings.playerUrl(provider.songId); frame.title = `${row.title} · 网易云音乐`;
    frame.width = "100%"; frame.height = "86"; frame.allow = "autoplay"; frame.referrerPolicy = "strict-origin-when-cross-origin";
    host.replaceChildren(frame); external = host;
  }
  function attach(article, audio, { media, event, selectTrack, catalog }) {
    if (!audio || !media.length) return;
    const controls = document.createElement("div"); controls.className = "atlas-player";
    controls.innerHTML = `<div class="player-heading"><span class="player-art" aria-hidden="true">♫</span><div><strong data-now-title></strong><small data-now-detail></small></div></div><div class="player-transport"><button type="button" data-prev aria-label="上一首候选">⏮</button><button type="button" data-toggle aria-label="播放" class="player-toggle">▶</button><button type="button" data-next aria-label="下一首候选">⏭</button><button type="button" data-repeat aria-pressed="false" title="单曲循环">↻</button><label class="player-volume">音量 <input data-volume type="range" min="0" max="1" step="0.05" value="0.8" aria-label="音量"></label></div><div class="player-timeline"><span data-current>0:00</span><input data-seek type="range" min="0" max="1000" value="0" aria-label="播放进度"><span data-duration>0:00</span></div><p class="player-message" data-player-message role="status"></p>`;
    audio.before(controls); audio.controls = false; audio.volume = .8;
    const node = (selector) => controls.querySelector(selector);
    let index = 0;
    const update = () => {
      const remote = !media[index]?.src;
      node("[data-current]").textContent = clock(audio.currentTime);
      node("[data-duration]").textContent = clock(audio.duration || media[index]?.duration);
      node("[data-seek]").value = Number.isFinite(audio.duration) && audio.duration > 0 ? String(Math.round(audio.currentTime / audio.duration * 1000)) : "0";
      node("[data-toggle]").textContent = remote ? "↗" : audio.paused ? "▶" : "Ⅱ";
      node("[data-toggle]").setAttribute("aria-label", remote ? "网易云播放" : audio.paused ? "播放" : "暂停");
      node("[data-seek]").disabled = remote || !Number.isFinite(audio.duration) || audio.duration <= 0;
      node("[data-volume]").disabled = remote;
      node("[data-repeat]").disabled = remote;
    };
    const selection = (next) => {
      index = next;
      const value = W.musicBindings.mediaLabel(media[index], event, index, catalog);
      node("[data-now-title]").textContent = value.title; node("[data-now-detail]").textContent = value.detail;
      node("[data-player-message]").textContent = media[index].src ? "" : "使用平台外链播放器；若外链受限，可在平台打开。"; update();
    };
    node("[data-toggle]").addEventListener("click", () => {
      if (!media[index].src && media[index].platformBinding) return playExternal(media[index].platformBinding, article.querySelector("[data-platform-player]"));
      if (audio.paused) audio.play().catch(() => { node("[data-player-message]").textContent = "播放未能开始，可重试或选择其他候选。"; }); else audio.pause();
    });
    node("[data-prev]").addEventListener("click", () => selectTrack((index - 1 + media.length) % media.length, true));
    node("[data-next]").addEventListener("click", () => selectTrack((index + 1) % media.length, true));
    node("[data-repeat]").addEventListener("click", () => { audio.loop = !audio.loop; node("[data-repeat]").setAttribute("aria-pressed", String(audio.loop)); });
    node("[data-seek]").addEventListener("input", (change) => { if (Number.isFinite(audio.duration)) audio.currentTime = Number(change.target.value) / 1000 * audio.duration; update(); });
    node("[data-volume]").addEventListener("input", (change) => { audio.volume = Number(change.target.value); });
    for (const type of ["timeupdate", "durationchange", "play", "pause", "ended"]) audio.addEventListener(type, update);
    audio.addEventListener("error", () => { node("[data-player-message]").textContent = "本地音频不可用。若已绑定平台，可使用下方平台播放器。"; });
    article.addEventListener("music:track-selected", (change) => selection(change.detail.index));
    selection(0);
  }
  function related(event, catalog) {
    const rows = [...new Map([...W.musicBindings.forEvent(event, catalog), ...[...(event.media || []), ...(event.conditionalMedia || [])].map((item) => item.platformBinding || W.musicBindings.forMedia(item, catalog)).filter(Boolean)].map((row) => [row.id, row])).values()];
    if (!rows.length) return "";
    return `<section class="music-recordings"><h4>已整理的专辑曲目</h4>${rows.map((row) => { const provider = W.musicBindings.provider(row); return `<div class="music-recording"><div><strong>${esc(W.musicBindings.label(row))}</strong><small>${esc(row.album || "人工整理")} · ${esc((row.aliases || []).join(" / "))}</small></div>${provider ? `<button type="button" data-platform-track="${esc(row.id)}">网易云播放 ↗</button><a href="${esc(W.musicBindings.songUrl(provider.songId))}" target="_blank" rel="noopener noreferrer">在平台打开</a>` : `<span class="music-binding-status">待绑定平台</span>`}</div>`; }).join("")}<p class="mr-note">事件关联的专辑记录；本地候选的具体曲目可在独立映射工具中补充。</p><div data-platform-player></div></section>`;
  }
  function attachRelated(article, event, catalog) {
    article.querySelectorAll("[data-platform-track]").forEach((button) => button.addEventListener("click", () => {
      const row = catalog.musicBindings.tracks.find((row) => row.id === button.dataset.platformTrack);
      if (row) playExternal(row, article.querySelector("[data-platform-player]"));
    }));
  }
  W.musicPlayer = { attach, related, attachRelated, stopExternal, playExternal, clock };
})();
