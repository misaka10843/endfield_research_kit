"""Reviewed community/manual music names and provider mappings, separate from evidence."""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = "endfield.music-bindings.v1"


def validate_bindings(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("tracks"), list):
        raise ValueError("unsupported music binding document")
    if len(data["tracks"]) > 10000:
        raise ValueError("music binding count exceeds limit")
    seen = set()
    for row in data["tracks"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", row["id"]) or row["id"] in seen:
            raise ValueError("music binding IDs must be unique safe identifiers")
        seen.add(row["id"])
        if not isinstance(row.get("title"), str) or not 0 < len(row["title"]) <= 200:
            raise ValueError(f"binding {row['id']}: title is required")
        for key in ("aliases", "sourceNames", "eventNames", "mediaIds"):
            if not isinstance(row.get(key, []), list) or any(not isinstance(value, str) or not value or len(value) > 300 for value in row.get(key, [])):
                raise ValueError(f"binding {row['id']}: invalid {key}")
        if any(not re.fullmatch(r"[0-9]+", value) for value in row.get("mediaIds", [])):
            raise ValueError(f"binding {row['id']}: mediaIds must be decimal strings")
        if not isinstance(row.get("trackNumbers", []), list) or any(type(value) is not int or not 1 <= value <= 999 for value in row.get("trackNumbers", [])):
            raise ValueError(f"binding {row['id']}: invalid album track numbers")
        if not isinstance(row.get("providers", []), list):
            raise ValueError(f"binding {row['id']}: providers must be a list")
        for provider in row.get("providers", []):
            if not isinstance(provider, dict) or provider.get("platform") != "netease" or not isinstance(provider.get("songId"), str) or not re.fullmatch(r"[1-9][0-9]{0,19}", provider["songId"]):
                raise ValueError(f"binding {row['id']}: invalid supported provider/song ID")
    # Ambiguous per-file bindings cannot safely replace a local file in a bundle.
    for key in ("mediaIds", "sourceNames"):
        owners = {}
        for row in data["tracks"]:
            for value in row.get(key, []):
                if value in owners and owners[value] != row["id"]:
                    raise ValueError(f"conflicting music {key} binding: {value}")
                owners[value] = row["id"]
    return data


def enrich_catalog(catalog: dict, *, export_root: Path, webui_root: Path) -> dict:
    path = webui_root / "overrides/music_bindings.json"
    bindings = validate_bindings(json.loads(path.read_bytes())) if path.is_file() else {"schema": SCHEMA, "tracks": []}
    catalog["musicBindings"] = bindings
    catalog["musicBindingBoundary"] = "User/community catalog labels and provider links; event association does not identify an individual Wwise media leaf. Only explicit media-ID or stored source-name bindings may replace a local preview."
    desc_path = export_root / "game/Table/LevelDescTable.json"
    text_path = export_root / f"game/Table/I18nTextTable_{catalog['language']}.json"
    if desc_path.is_file() and text_path.is_file():
        descriptions, localized = json.loads(desc_path.read_bytes()), json.loads(text_path.read_bytes())
        catalog["mapNames"] = {key: localized.get(str((row.get("showName") or {}).get("id"))) or (row.get("showName") or {}).get("text") or key for key, row in descriptions.items()}
    return catalog


def media_track(item: dict, bindings: dict) -> dict | None:
    media_id = str(item.get("mediaId") or item.get("id") or "")
    source = item.get("originalName") or item.get("sourceFileName") or item.get("name") or ""
    stem = Path(source).stem
    matches = [row for row in bindings.get("tracks", []) if media_id in row.get("mediaIds", []) or stem in row.get("sourceNames", [])]
    return matches[0] if len(matches) == 1 else None
