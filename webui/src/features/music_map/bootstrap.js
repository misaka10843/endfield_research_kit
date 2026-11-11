// The independent atlas needs only the shared music renderer, not the main app.
window.WEBUI_UI_LOCALE = "zh";
window.WebUI = { escapeHtml: (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;") };
window.WebUI.standaloneMusicAtlas = true;
