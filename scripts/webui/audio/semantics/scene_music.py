"""Project scene BGM requests and authenticated authored trigger regions.

AudioLevel/AudioMapData scene keys, explicit AudioCue level handlers and
admitted LevelScript owners establish scene membership. Names identify music candidates, never
scene ownership. Wwise media are possible leaves, not a reconstructed mix.
Stored fade/delay/sync parameters do not establish an audible silence gap.
No spatial geometry is manufactured from a scene-level request or emitter.
"""
from __future__ import annotations

import re
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.webui.audio.semantics.identifiers import audio_hash_generator_compute

SCHEMA = "endfield.webui.scene-music.v1"
TIMING_FIELD = re.compile(r"fade|delay|transition|sync|preEntry|postExit", re.I)
TIMING_COUNTER = re.compile(r"count|schema|status", re.I)


def _hash(value: Any) -> int | None:
    return (value & 0xffffffff) if isinstance(value, int) and not isinstance(value, bool) else None


def _music(event: dict) -> bool:
    return event.get("category") == "music" or str(event.get("id") or "").lower().startswith("au_music_")


def _timings(event: dict) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    pending = [("evidence", event.get("evidence") or [])]
    visited = 0
    while pending and len(rows) < 96 and visited < 20000:
        path, value = pending.pop()
        visited += 1
        if isinstance(value, dict):
            for key, child in reversed(list(value.items())):
                child_path = f"{path}.{key}"
                if TIMING_FIELD.search(key) and not TIMING_COUNTER.search(key) and isinstance(child, (int, float, str, bool)) and len(str(child)) <= 200:
                    rows.append({"path": child_path, "field": key, "value": child,
                                 "unit": "ms" if key.lower().endswith("ms") else None})
                elif isinstance(child, (dict, list)):
                    pending.append((child_path, child))
        elif isinstance(value, list):
            pending.extend((f"{path}[{index}]", child) for index, child in reversed(list(enumerate(value))))
    return rows, bool(pending)


def build_scene_music_catalog(scene_catalog: dict, events: list[dict], cue_semantics: dict, language: str,
                             *, regions: list[dict] | None = None, region_audit: dict | None = None) -> dict:
    by_hash: dict[int, list[dict]] = defaultdict(list)
    for event in events:
        value = _hash(event.get("hash"))
        if value is not None:
            by_hash[value].append(event)
    scenes: dict[str, dict] = {}
    selected: dict[str, dict] = {}
    unresolved: list[dict] = []

    def scene(scene_id: str) -> dict:
        return scenes.setdefault(scene_id, {"sceneId": scene_id, "events": [],
            "spatialCoverage": {"status": "unresolved", "geometry": None,
                                "reason": "sceneRequestDoesNotEstablishSpatialCoverage"}})

    def attach(scene_id: str, value: int, role: str, source: Any, *, music_trigger: bool = False) -> None:
        matches = by_hash.get(value) or []
        if len(matches) != 1:
            unresolved.append({"sceneId": scene_id, "eventHash": value, "role": role,
                               "reason": "eventHashMissing" if not matches else "ambiguousEventHash"})
            return
        event = matches[0]
        if not (_music(event) or music_trigger):
            return
        event_id = str(event["id"])
        selected[event_id] = event
        bucket = scene(scene_id)["events"]
        existing = next((row for row in bucket if row["eventId"] == event_id), None)
        if existing is None:
            existing = {"eventId": event_id, "eventHash": value, "roles": [], "sources": []}
            bucket.append(existing)
        if role not in existing["roles"]:
            existing["roles"].append(role)
        if source and source not in existing["sources"]:
            existing["sources"].append(source)

    source_scenes = (scene_catalog.get("scenes") or []) if scene_catalog.get("status") in {
        "validatedPublishedObjectIndex", "validatedPartialPublishedObjectIndex", "exactAudioLevelTable",
    } else []
    for source_scene in source_scenes:
        scene_id = str(source_scene.get("sceneId") or "")
        if not scene_id:
            continue
        level = source_scene.get("audioLevel") or {}
        scene(scene_id)["customMusicModeBaseState"] = level.get("customMusicModeBaseState")
        for event in level.get("events") or []:
            value = _hash(event.get("eventHash"))
            if value is not None:
                attach(scene_id, value, str(event.get("role") or "levelEvent"), "game/Table/AudioLevel.json",
                       music_trigger=event.get("role") == "battleMusicTriggerEvent")
        for definition in source_scene.get("definitions") or []:
            for event in definition.get("events") or []:
                value = _hash(event.get("eventHash"))
                if value is not None:
                    attach(scene_id, value, str(event.get("role") or "sceneGlobalEvent"), definition.get("source"))

    rules_by_event: dict[str, list[dict]] = defaultdict(list)
    for definition in (cue_semantics.get("cueDefinitions") or {}).values():
        for request in definition.get("behaviorEvents") or []:
            event_id = str(request.get("eventId") or "")
            value = audio_hash_generator_compute(event_id)
            matches = by_hash.get(value) or []
            if len(matches) != 1 or not _music(matches[0]):
                continue
            event = matches[0]
            event_id = str(event["id"])
            selected[event_id] = event
            level_id = str(request.get("levelId") or "")
            if request.get("handlerScope") == "level" and level_id:
                attach(level_id, value, "conditionalCueRequest", request.get("source"))
            conditions = [node for node in definition.get("expressionAst") or []
                if node.get("expressionSide") == "condition"
                and node.get("handlerScope") == request.get("handlerScope")
                and node.get("levelId") == request.get("levelId")
                and node.get("handlerIndex") == request.get("handlerIndex")]
            rule = {"cueId": definition.get("cueId"), "handlerScope": request.get("handlerScope"),
                    "levelId": level_id or None, "handlerIndex": request.get("handlerIndex"),
                    "source": request.get("source"), "evaluationStatus": "notEvaluated",
                    "conditions": [{key: node.get(key) for key in (
                        "path", "exprType", "exprTypeName", "stringValue", "intValue", "floatValue", "boolValue", "childPaths")}
                        for node in conditions[:64]], "conditionsTruncated": len(conditions) > 64}
            if rule not in rules_by_event[event_id]:
                rules_by_event[event_id].append(rule)

    for region in regions or []:
        for request in region["requests"]:
            attach(region["sceneId"], request["eventHash"],
                   "areaEnterRequest" if request["direction"] == "enter" else "areaLeaveRequest",
                   region["source"], music_trigger=True)

    # Keep music without scene ownership searchable, without guessing a level
    # from names such as "lv001", a region name, or a nearby emitter.
    for event in events:
        if _music(event):
            selected[str(event["id"])] = event
    from scripts.webui.audio.semantics.music_routes import project_music_routes
    routes = project_music_routes(list(selected.values()))
    projected_events = []
    for event_id, event in sorted(selected.items()):
        timings, truncated = _timings(event)
        projected_events.append({
            "id": event_id, "name": event.get("name") or event_id, "hash": event.get("hash"),
            "nameEvidence": event.get("nameEvidence"),
            "category": event.get("category"), "foundInWwise": event.get("foundInWwise"),
            "operations": event.get("wwiseActionOperations") or [],
            "controlActions": [dict(action) for evidence in event.get("evidence") or []
                               for action in evidence.get("actionEvidence") or []
                               if action.get("operation") in {"setState", "setSwitch", "setGameParameter", "resetGameParameter"}],
            "selectorBranches": event.get("selectorBranches") or [],
            "playbackRole": event.get("playbackRole"),
            "media": [{**{key: media.get(key) for key in ("id", "mediaId", "src", "name", "duration", "category", "relationType")
                       if media.get(key) is not None}, "name": media.get("name") or str(media.get("mediaId") or media.get("id"))}
                      for media in event.get("media") or [] if media.get("src")],
            "conditionalMedia": (routes.get(event_id) or {}).get("media") or [],
            "musicRouteAudit": (routes.get(event_id) or {}).get("audit"),
            "cueRules": rules_by_event.get(event_id) or [],
            "timingParameters": timings, "timingParametersTruncated": truncated,
            "audibleGap": {"status": "unresolved", "durationMs": None,
                           "reason": "fadeDelayAndSyncParametersDoNotProveAudibleSilence"},
        })
    scene_rows = [row for _, row in sorted(scenes.items()) if row["events"]]
    owned = {event["eventId"] for row in scene_rows for event in row["events"]}
    return {"schema": SCHEMA, "language": language, "sceneSourceStatus": scene_catalog.get("status"),
        "coverage": "sceneBackgroundsAndExplicitCueLevels",
        "sources": {"sceneSources": scene_catalog.get("sources") or [], "audioLevel": (scene_catalog.get("audioLevel") or {}).get("sources") or [],
                    "cueSource": cue_semantics.get("source")},
        "counts": {"scenes": len(scene_rows), "musicEvents": len(projected_events),
                   "sceneLinkedEvents": len(owned), "sceneUnresolvedEvents": len(selected) - len(owned),
                   "authoredMusicRegions": len(regions or [])},
        "scenes": scene_rows, "events": projected_events,
        "regions": regions or [], "regionAudit": region_audit,
        "unresolvedSceneEventReferences": unresolved,
        "evidenceBoundary": "Stored scene/event requests, conditional Cue definitions and authenticated nominal trigger geometry; media are possible Wwise leaves. Runtime state, audible spatial coverage, selected mix and audible silence are not established."}


def refresh_scene_music(*, audio_index: dict, export_root: Path, webui_root: Path, language: str,
                        metadata: Path | None = None, gameassembly: Path | None = None,
                        require_regions: bool = False) -> dict:
    """Focused table/Cue and pooled-region projection using decoded Wwise media.

    A prior full scene catalog is not reused: it may describe another export.
    This mode covers AudioLevel, explicit Cue level handlers and admitted pooled regions;
    the normal semantic publication also includes authenticated AudioMapData.
    """
    from scripts.webui.audio.semantics import event_projection, native_evidence, scene_backgrounds, table_contexts
    from scripts.webui.audio.semantics.context_utils import json_dump

    print("Scene music: collecting AudioLevel and AudioCue tables (no Unity scan or decode)", flush=True)
    scenes = scene_backgrounds.collect_audio_level_scene_catalog(export_root, audio_index)
    context = native_evidence.validate_native_audio_evidence(metadata, gameassembly)
    cues = table_contexts.collect_audio_cue_semantics(export_root, native_context=context)
    print("Scene music: projecting existing Wwise Event/media links", flush=True)
    events, _, _ = event_projection.build_event_rows(audio_index, {})
    from scripts.webui.audio.semantics.scene_music_regions import collect_regions
    print("Scene music: authenticating pooled LevelScript region requests", flush=True)
    events, regions, region_audit = collect_regions(export_root=export_root, metadata=metadata,
        gameassembly=gameassembly, language=language, events=events)
    catalog = build_scene_music_catalog(scenes, events, cues, language.upper(), regions=regions, region_audit=region_audit)
    if require_regions and region_audit.get("status") != "validatedProfileSubset":
        raise ValueError(f"music region publication refused: {region_audit.get('status')}: {region_audit.get('detail') or region_audit.get('native', {}).get('detail')}")
    catalog["coverage"] = "audioLevelCueAndPooledRegionRequests"
    from scripts.webui.audio.semantics.music_bindings import enrich_catalog
    enrich_catalog(catalog, export_root=export_root, webui_root=webui_root)
    json_dump(webui_root / "data" / "lang" / language.upper() / "audio" / "scene_music.json", catalog)
    print("Scene music: " + json.dumps(catalog["counts"]), flush=True)
    return catalog
