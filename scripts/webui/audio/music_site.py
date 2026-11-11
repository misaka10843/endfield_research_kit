"""Music-map workflow and portable static-site publication, without audio decode.

Called by build_audio's public --music-map-only mode. The native contract and
current VFS ledger stay authoritative; a new client never inherits old regions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from scripts.game_data.pooled_levelscript_corpus import DEFAULT_OUTER, DEFAULT_LEDGER
from scripts.repo_paths import REPO_ROOT


def _within(path: Path, root: Path) -> Path:
    result = path.resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError(f"site input escapes its source root: {path}")
    return result


def _relative(value: str) -> PurePosixPath:
    url = urlsplit(value)
    path = PurePosixPath(unquote(url.path))
    if url.scheme or url.netloc or path.is_absolute() or ".." in path.parts or "\\" in str(path):
        raise ValueError(f"unsafe static-site path: {value}")
    return path


def ensure_region_audit(game_root: Path, dumper: Path) -> None:
    from scripts.game_data.memorypack.corpus_gate import _read_outer_and_ledger
    primary = game_root / "Persistent" if (game_root / "Persistent").is_dir() else game_root / "StreamingAssets"
    fallback = game_root / "StreamingAssets" if primary.name == "Persistent" else None
    try:
        summary = json.loads(DEFAULT_OUTER.read_bytes())
        if Path(summary["primaryAssets"]).resolve() != primary.resolve() or (fallback and Path(summary["fallbackAssets"]).resolve() != fallback.resolve()):
            raise ValueError("selected assets roots differ from cached audit")
        _read_outer_and_ledger(DEFAULT_OUTER, DEFAULT_LEDGER, expected_input_set_sha256=summary["inputSetSha256"])
        print("Music map: current JsonData VFS audit reused", flush=True)
        return
    except (OSError, ValueError, KeyError) as exc:
        print(f"Music map: refreshing JsonData VFS audit ({str(exc)[:240]})", flush=True)
    DEFAULT_OUTER.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="music-audit-", dir=DEFAULT_OUTER.parent) as stage_dir:
        stage = Path(stage_dir)
        roots = ["--streaming-assets", str(primary)] + (["--fallback-assets", str(fallback)] if fallback else [])
        index = stage / "index.json"
        subprocess.run([str(dumper), "vfs-index", *roots, "--block-type", "json-data", "--file-regex", r"StringPool0[.]byte$", "--output", str(index)], check=True)
        blocks = [row for row in json.loads(index.read_bytes())["blocks"] if row.get("blockType") == "JsonData"]
        if len(blocks) != 1 or not re.fullmatch(r"[0-9A-Fa-f]{8}", blocks[0].get("hashDirectory", "")):
            raise ValueError("JsonData index does not identify one safe VFS directory")
        outer, ledger, report = stage / "summary.json", stage / "files.jsonl.gz", stage / "report.md"
        subprocess.run([str(dumper), "vfs-audit", *roots, "--block-hash", blocks[0]["hashDirectory"], "--summary-json", str(outer), "--ledger-jsonl-gz", str(ledger), "--report-md", str(report)], check=True)
        summary = json.loads(outer.read_bytes())
        _read_outer_and_ledger(outer, ledger, expected_input_set_sha256=summary["inputSetSha256"])
        # Publish the verified pair only after the complete staged audit passes.
        os.replace(ledger, DEFAULT_LEDGER)
        os.replace(report, DEFAULT_OUTER.with_suffix(".md"))
        os.replace(outer, DEFAULT_OUTER)


def export_site(*, catalog: dict, webui_root: Path, export_root: Path, output: Path,
                media_mode: str = "all") -> dict:
    """Copy the independent UI, needed maps and linked media into a static root."""
    output = output.resolve()
    if output.is_relative_to(REPO_ROOT) and not any(output.is_relative_to(REPO_ROOT / part) for part in ("reports", "tmp", "scratch")):
        raise ValueError("generated site inside the repository must stay under reports/, tmp/ or scratch/")
    if output == output.parent or any(root.resolve().is_relative_to(output) or output.is_relative_to(root.resolve()) for root in (webui_root, export_root)):
        raise ValueError("site output must be separate from export and WebUI source trees")
    data = copy.deepcopy(catalog)
    if media_mode not in ("all", "mapped", "none"):
        raise ValueError("music-site media mode must be all, mapped or none")
    from scripts.webui.audio.semantics.music_bindings import media_track, validate_bindings
    bindings = validate_bindings(data.get("musicBindings") or {"schema": "endfield.music-bindings.v1", "tracks": []})
    language = data["language"]
    if not re.fullmatch(r"[A-Z]{2,8}", language):
        raise ValueError("unsafe catalog language")
    files: dict[str, Path | dict | str] = {}
    rewritten: dict[str, str] = {}
    omitted = set()
    for event in data.get("events", []):
        for item in (event.get("media") or []) + (event.get("conditionalMedia") or []):
            source_url = item.get("src")
            if not source_url:
                continue
            binding = media_track(item, bindings)
            if binding and binding.get("providers"):
                item["platformBinding"] = binding
            if media_mode == "none" or (media_mode == "mapped" and binding and binding.get("providers")):
                omitted.add(source_url)
                item["src"] = None
                item["localPreviewOmitted"] = True
                continue
            parsed = urlsplit(source_url)
            if parsed.scheme or parsed.netloc:
                raise ValueError(f"site media must be a local exported file: {source_url}")
            original = PurePosixPath(unquote(parsed.path))
            if original.parts[:3] != ("/", "export_full", "game"):
                raise ValueError(f"unexpected exported media route: {source_url}")
            relative = _relative(str(PurePosixPath(*original.parts[2:])))
            source = _within(export_root.joinpath(*relative.parts), export_root / "game/Audio")
            if source.suffix.lower() not in (".flac", ".ogg", ".wav", ".mp3", ".m4a") or not source.is_file():
                raise ValueError(f"linked preview is missing or unsupported: {source}")
            target = rewritten.setdefault(source_url, f"media/{hashlib.sha256(source_url.encode()).hexdigest()[:16]}_{source.name}")
            files[target] = source
            item["src"] = target
    scene_ids = {row["sceneId"] for row in data.get("scenes", [])} | {row["sceneId"] for row in data.get("regions", [])}
    index = json.loads((webui_root / "data/map_recovery/index.json").read_bytes())
    scene_ids.update(row["id"] for row in index["maps"] if re.fullmatch(r"map0[12]_lv[0-9]{3}", row.get("id", "")))
    map_rows = []
    for scene_id in sorted(scene_ids):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", scene_id):
            raise ValueError(f"unsafe scene ID: {scene_id}")
        path = webui_root / f"data/map_recovery/maps/{scene_id}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_bytes())
        # Only the map image and geographic labels are needed by this page.
        minimal = {key: payload[key] for key in ("name", "label", "mapConfig", "minimap", "renderBackground") if key in payload}
        for key in ("minimap", "renderBackground"):
            background = minimal.get(key) or {}
            if background.get("src") and background.get("worldBounds"):
                relative = _relative(background["src"])
                source = _within(webui_root / "data/map_recovery" / str(relative), webui_root / "data/map_recovery")
                if not source.is_file():
                    minimal.pop(key, None)
                    continue
                files[f"data/map_recovery/{relative}"] = source
                minimal[key] = {field: background[field] for field in ("src", "worldBounds", "status") if field in background}
        row = next((row for row in index["maps"] if row.get("id") == scene_id), {})
        map_rows.append({"id": scene_id, "name": data.get("mapNames", {}).get(scene_id) or row.get("name", scene_id), "label": row.get("label", scene_id), "regionKey": row.get("regionKey")})
        files[f"data/map_recovery/maps/{scene_id}.json"] = minimal
    files[f"data/lang/{language}/audio/scene_music.json"] = data
    files["data/map_recovery/index.json"] = {"maps": map_rows}
    for relative in ("src/features/map_recovery/music.js", "src/features/map_recovery/music.css", "src/features/music_map/bootstrap.js", "src/features/music_map/index.js", "src/features/music_map/style.css", "src/features/music_map/bindings.js", "src/features/music_map/player.js", "src/features/music_map/editor.js", "src/features/music_map/editor.css", "music-bindings.html"):
        files[relative] = webui_root / relative
    files["overrides/music_bindings.json"] = bindings
    if (webui_root / "favicon.ico").is_file():
        files["favicon.ico"] = webui_root / "favicon.ico"
    if (webui_root / "logo.svg").is_file():
        files["logo.svg"] = webui_root / "logo.svg"
    html = (webui_root / "music.html").read_text(encoding="utf-8").replace('href="music.html"', 'href="index.html"')
    html = html.replace('<a href="./#map-recovery" data-main-app>完整 WebUI ↗</a>', '<span>独立音乐地图</span>')
    # A bundle chooses its own publication language without requiring the app picker.
    html = html.replace('<body>', f'<body><select id="language" hidden><option selected>{language}</option></select>')
    files["index.html"] = html
    files["music.html"] = html
    report = {"schema": "endfield.music-map-site.v1", "maps": len(map_rows), "regions": len(data.get("regions", [])), "mediaMode": media_mode, "omittedLocalMedia": len(omitted), "mediaFiles": len(rewritten), "mediaBytes": sum(files[name].stat().st_size for name in files if name.startswith("media/")), "inputSetSha256": (data.get("regionAudit") or {}).get("inputSetSha256")}
    previous_media = set()
    previous_catalog_path = output / f"data/lang/{language}/audio/scene_music.json"
    if (output / "site_manifest.json").is_file() and previous_catalog_path.is_file():
        previous = json.loads(previous_catalog_path.read_bytes())
        previous_media = {item["src"] for event in previous.get("events", []) for item in (event.get("media") or []) + (event.get("conditionalMedia") or []) if isinstance(item.get("src"), str) and item["src"].startswith("media/")}
    files["site_manifest.json"] = report
    # Incremental, atomic file replacement; no recursive deletion of user paths.
    output.mkdir(parents=True, exist_ok=True)
    for relative, value in files.items():
        destination = _within(output / relative, output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".publishing")
        if temp.exists():
            temp.unlink()
        if isinstance(value, Path):
            try:
                if not relative.startswith("media/"):
                    raise OSError("UI and map sources are copied independently")
                os.link(value, temp)
            except OSError:
                shutil.copyfile(value, temp)
        else:
            temp.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, destination)
    # Remove only old media referenced by this generated site's previous catalog.
    # This is necessary when an existing full site becomes an external-only site.
    for relative in sorted(previous_media - set(files)):
        safe = _relative(relative)
        path = _within(output / str(safe), output / "media")
        if path.is_file():
            path.unlink()
    print("Music map site: " + json.dumps({"output": str(output), **report}), flush=True)
    return report


def build_music_map(args, audio_index: dict) -> dict:
    from scripts.game_data.extraction.verify_export_freshness import Requirements, verify
    from scripts.game_data.pooled_levelscript_native import load_profile
    from scripts.webui.audio.semantics.scene_music import refresh_scene_music
    game_root = args.game_root.resolve()
    if not game_root.is_dir():
        raise ValueError(f"installed Endfield_Data directory not found: {game_root}")
    metadata = args.metadata or game_root / "il2cpp_data/Metadata/global-metadata.dat"
    gameassembly = game_root.parent / "GameAssembly.dll"
    _, native = load_profile(metadata=metadata, gameassembly=gameassembly)
    if native["status"] != "validated":
        raise ValueError(f"music region native profile {native['status']}: {native['detail']}")
    freshness = dict(game_root=game_root, output_root=args.export_root, requirements=Requirements(("table", "json-data")), remedy="music-map refresh is limited to Table and JsonData; re-run without --no-refresh-music-inputs to refresh them")
    if verify(**freshness):
        if args.no_refresh_music_inputs:
            raise ValueError("music-map export inputs are stale; previous publication preserved")
        print("Music map: refreshing only Table and JsonData; no Unity classes or audio decode", flush=True)
        subprocess.run([sys.executable, "-u", "-m", "scripts.game_data.extraction.export_full_from_game", "--game-root", str(game_root), "--output", str(args.export_root.resolve()), "--structured", "table", "json-data", "--skip-animestudio", "--animestudio", str(args.audio_dumper)], cwd=REPO_ROOT, check=True)
        if verify(**freshness):
            raise ValueError("targeted Table/JsonData refresh did not satisfy freshness")
    ensure_region_audit(game_root, args.audio_dumper)
    catalog = refresh_scene_music(audio_index=audio_index, export_root=args.export_root.resolve(), webui_root=args.webui_root.resolve(), language=args.language, metadata=metadata, gameassembly=gameassembly, require_regions=True)
    report = {"schema": "endfield.music-map-build.v1", "counts": catalog["counts"], "coverage": catalog["regionAudit"].get("levelCoverage"), "failureCategories": catalog["regionAudit"].get("failureCategories"), "inputSetSha256": catalog["regionAudit"]["inputSetSha256"]}
    if args.music_site:
        report["site"] = export_site(catalog=catalog, webui_root=args.webui_root.resolve(), export_root=args.export_root.resolve(), output=args.music_site, media_mode=args.music_site_media)
    target = REPO_ROOT / "reports/audio/music_map_build.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Music map: complete; open http://127.0.0.1:8765/music.html", flush=True)
    return report


def refresh_music_bindings(args) -> dict:
    """Republish only reviewed labels/provider links, preserving recovery data."""
    from scripts.webui.audio.semantics.music_bindings import enrich_catalog
    from scripts.webui.audio.semantics.context_utils import json_dump
    path = args.webui_root / f"data/lang/{args.language}/audio/scene_music.json"
    catalog = json.loads(path.read_bytes())
    if catalog.get("schema") != "endfield.webui.scene-music.v1" or not isinstance(catalog.get("events"), list):
        raise ValueError("publish a valid scene music catalog before refreshing bindings")
    enrich_catalog(catalog, export_root=args.export_root.resolve(), webui_root=args.webui_root.resolve())
    json_dump(path, catalog)
    report = {"musicBindingTracks": len(catalog["musicBindings"]["tracks"]), "recoveryData": "preserved"}
    if args.music_site:
        report["site"] = export_site(catalog=catalog, webui_root=args.webui_root.resolve(), export_root=args.export_root.resolve(), output=args.music_site, media_mode=args.music_site_media)
    print("Music map bindings: " + json.dumps(report), flush=True)
    return report
