"""Static level-owned music requests joined to authored trigger geometry."""
from __future__ import annotations

import json
import hashlib
import math
from collections import defaultdict
from pathlib import Path

from scripts.webui.audio.semantics.identifiers import audio_hash_generator_compute


def constant(param):
    if (isinstance(param, dict) and param.get("idRef") == -1 and param.get("paramSource") == 0
            and param.get("path") in (None, "")):
        return param.get("constValue")
    return None


def box_polygon(shape):
    if shape.get("shapeType") != 1:
        return None
    position, rotation, size = (shape.get(key) for key in ("position", "rotation", "size"))
    if not all(isinstance(value, list) and len(value) == 3 and
               all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)
               for value in (position, rotation, size)):
        return None
    if min(size) <= 0:
        return None
    if abs(rotation[0]) > 1e-5 or abs(rotation[2]) > 1e-5:
        # Unity Euler applies Z, X, Y. The selected consumer uses unit-scale
        # TRS; project all eight corners, never flatten the tilt into yaw.
        ax, ay, az = map(math.radians, rotation)
        points = []
        for x in (-size[0] / 2, size[0] / 2):
            for y in (-size[1] / 2, size[1] / 2):
                for z in (-size[2] / 2, size[2] / 2):
                    rz_x, rz_y = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
                    rx_z = rz_y * math.sin(ax) + z * math.cos(ax)
                    points.append((position[0] + rz_x * math.cos(ay) + rx_z * math.sin(ay),
                                   position[2] - rz_x * math.sin(ay) + rx_z * math.cos(ay)))
        ordered = sorted(set(points))
        def cross(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        def half(values):
            out = []
            for point in values:
                while len(out) >= 2 and cross(out[-2], out[-1], point) <= 0:
                    out.pop()
                out.append(point)
            return out
        return [list(point) for point in half(ordered)[:-1] + half(reversed(ordered))[:-1]]
    angle = math.radians(rotation[1])
    return [[position[0] + x * math.cos(angle) + z * math.sin(angle),
             position[2] - x * math.sin(angle) + z * math.cos(angle)]
            for x, z in ((-size[0] / 2, -size[2] / 2), (size[0] / 2, -size[2] / 2),
                         (size[0] / 2, size[2] / 2), (-size[0] / 2, size[2] / 2))]


def shape_polygon(shape):
    if shape.get("shapeType") == 1:
        return box_polygon(shape)
    center, radius = shape.get("position"), shape.get("radius")
    if (shape.get("shapeType") != 2 or not isinstance(center, list) or len(center) != 3
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in center)
            or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0):
        return None
    # Nominal sphere silhouette sampled at 96 vertices; no player-radius padding.
    return [[center[0] + radius * math.cos(i * math.tau / 96), center[2] + radius * math.sin(i * math.tau / 96)] for i in range(96)]


def contains_xz(shape, position):
    polygon = shape_polygon(shape)
    if not polygon:
        return False
    if shape.get("shapeType") == 2:
        return math.hypot(position["x"] - shape["position"][0], position["z"] - shape["position"][2]) <= shape["radius"]
    signs = [(b[0] - a[0]) * (position["z"] - a[1]) - (b[1] - a[1]) * (position["x"] - a[0]) for a, b in zip(polygon, polygon[1:] + polygon[:1])]
    return all(value >= -1e-7 for value in signs) or all(value <= 1e-7 for value in signs)


def _requests(header, actions, getters):
    output = []
    pending = [(header.get("_nextID"), [], [], 0.0, frozenset())]
    steps = 0
    while pending and steps < 512:
        node_id, path, conditions, delay, seen = pending.pop()
        steps += 1
        if node_id in (-1, 0, None) or node_id in seen or len(path) >= 64:
            continue
        node = actions.get(node_id)
        if node is None:
            continue
        kind = node["$type"].rsplit(".", 1)[-1]
        path = path + [{"nodeId": node_id, "type": kind, "sourceOffset": node["$start"]}]
        seen = seen | {node_id}
        if kind == "IfElseAction":
            param = node.get("_condition")
            value = constant(param)
            for result, next_id in ((True, node.get("_onTrueID")), (False, node.get("_onFalseID"))):
                if isinstance(value, bool) and result != value:
                    continue
                condition = {"nodeId": node_id, "branch": result, "param": param,
                             "getter": getters.get((param or {}).get("idRef")), "evaluation": "notEvaluated"}
                pending.append((next_id, path, conditions + [condition], delay, seen))
            continue
        if kind == "WaitForSeconds":
            seconds = constant(node.get("_seconds"))
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
                continue
            delay += seconds
            path[-1]["seconds"] = seconds
        elif kind == "SetBool":
            path[-1].update(key=node.get("_key"), value=node.get("_value"))
        elif kind == "PostMusicEvent":
            name = constant(node.get("_musicEvent"))
            if isinstance(name, str) and name.startswith("au_music_"):
                output.append({"eventName": name, "eventHash": audio_hash_generator_compute(name),
                               "nodeId": node_id, "path": path, "conditions": conditions,
                               "scriptDelaySeconds": delay, "preAction": node.get("_musicEventType"),
                               "eventOnRelease": node.get("_musicEventOnRelease"), "evaluation": "notObserved"})
        else:
            continue  # Unknown control flow never becomes a music edge.
        pending.append((node.get("_nextID"), path, conditions, delay, seen))
    return output


def project_regions(records, events, place_labels):
    by_hash = defaultdict(list)
    for event in events:
        by_hash[event.get("hash")].append(event)
    aliases, regions, withheld = {}, [], []
    for source in records:
        root = source["record"]["data"]
        graph = ((root.get("actionMap") or {}).get("dataMap") or {})
        actions_list = [node for node in graph.get("actionList") or [] if node]
        getters_list = [node for node in graph.get("getterList") or [] if node]
        actions = {node["_ID"]: node for node in actions_list}
        getters = {node["_ID"]: node for node in getters_list}
        if len(actions) != len(actions_list) or len(getters) != len(getters_list):
            withheld.append({"source": source["source"], "reason": "duplicateNodeId"})
            continue
        volume_rows = root.get("triggerVolumes") or []
        volumes = {row["key"]: row["value"] for row in volume_rows}
        if len(volumes) != len(volume_rows):
            withheld.append({"source": source["source"], "reason": "duplicateTriggerSlot"})
            continue
        buckets = {}
        for header in graph.get("headerList") or []:
            if not header:
                continue
            kind = header["$type"].rsplit(".", 1)[-1]
            if kind not in ("OnLeaderEnterTriggerVolume", "OnLeaderLeaveTriggerVolume"):
                continue
            slot = constant(header.get("_triggerSlotIdFilter"))
            volume = volumes.get(slot)
            if (not isinstance(slot, int) or not volume or volume.get("slotId") != slot
                    or header.get("_filterMode") is not True or header.get("_targetScript") is not None
                    or header.get("_triggerTarget") != 0 or constant(header.get("_validate")) is not True):
                continue
            requests = _requests(header, actions, getters)
            for request in requests:
                matches = by_hash.get(request["eventHash"]) or []
                if len(matches) != 1 or not matches[0].get("foundInWwise"):
                    withheld.append({"source": source["source"], "eventName": request["eventName"], "reason": "eventMissingOrAmbiguous"})
                    continue
                event = matches[0]
                request.update(eventId=event["id"], direction="enter" if kind == "OnLeaderEnterTriggerVolume" else "leave",
                               headerId=header["_ID"], headerSourceOffset=header["$start"])
                aliases[event["id"]] = request["eventName"]
                buckets.setdefault(slot, []).append(request)
        for slot, requests in buckets.items():
            volume = volumes[slot]
            shapes = [{"shape": shape, "polygonXZ": shape_polygon(shape),
                       "projection": "sampledSphereSilhouette96" if shape.get("shapeType") == 2 else "boxCornerXZConvexHull" if shape.get("shapeType") == 1 else "unsupported"}
                      for shape in volume.get("shapeList") or []]
            labels = [label for label in place_labels.get(source["levelId"], [])
                      if any(contains_xz(item["shape"], label["position"]) for item in shapes)]
            state_keys = sorted({node["key"]["path"] for request in requests if request["direction"] == "enter"
                                 for node in request["path"] if node["type"] == "SetBool"
                                 and constant(node.get("value")) is True and isinstance(node.get("key"), dict)
                                 and isinstance(node["key"].get("path"), str) and node["key"].get("paramSource") == 100})
            regions.append({"id": f"{source['levelId']}:{source['scriptId']}:{slot}", "sceneId": source["levelId"],
                "scriptId": source["scriptId"], "slotId": slot, "source": source["source"],
                "requests": requests, "shapes": shapes, "placeLabels": labels,
                "enterStateKeys": state_keys,
                "labelRelation": "mapLabelAnchorContainedInTriggerXZProjection",
                "volumePolicy": {key: value for key, value in volume.items() if key not in ("shapeList", "$type", "$start", "$end")},
                "scriptActivation": {key: root.get(key) for key in ("activeShapeList", "startShapeList", "startType", "endType", "parentLevelScriptId", "levelScriptType")},
                "evidenceLevel": "direct", "runtimeObserved": False,
                "evidenceBoundary": "Level-owned stored entering/leaving header filter and bounded action path to a music request. XZ map-label containment names the displayed vicinity, not a formal place boundary. This is an authored trigger volume, not an observed audible attenuation range; script lifecycle and competing requests remain unobserved."})
    augmented = [{**event, "name": aliases[event["id"]], "category": "music",
                  "nameEvidence": "pooledLevelScriptConstantFNV1JoinToWwiseEvent"} if event["id"] in aliases else event
                 for event in events]
    return augmented, regions, withheld


def collect_regions(*, export_root, metadata, gameassembly, language, events):
    from scripts.game_data.pooled_levelscript_corpus import DEFAULT_OUTER, collect_pooled_scripts
    if metadata is None or gameassembly is None:
        return events, [], {"status": "missing", "detail": "explicit native inputs are required"}
    try:
        outer = json.loads(DEFAULT_OUTER.read_bytes())
        records, _, audit = collect_pooled_scripts(export_root=export_root, metadata=metadata,
            gameassembly=gameassembly, expected_input_set_sha256=outer["inputSetSha256"])
        if audit["status"] != "validatedProfileSubset":
            return events, [], audit
        table = json.loads((export_root / "game/Table/TextTable.json").read_bytes())
        localized = json.loads((export_root / f"game/Table/I18nTextTable_{language.upper()}.json").read_bytes())
        labels = {}
        source_fingerprints = {source["virtualPath"]: source for source in audit.get("sources", [])}
        for level in {record["levelId"] for record in records}:
            path = export_root / f"game/Json/UILevelMapLoadConfig/{level}.json"
            if not path.is_file():
                continue
            raw = path.read_bytes()
            fingerprint = source_fingerprints.get(f"Data/Json/UILevelMapLoadConfig/{level}.json")
            if not fingerprint or hashlib.sha256(raw).hexdigest().upper() != fingerprint["sha256"]:
                raise ValueError(f"map-label configuration differs from admitted VFS bytes: {level}")
            config = json.loads(raw)
            labels[level] = [{"id": key, "textId": row.get("textId"),
                              "text": localized.get(str((table.get(row.get("textId")) or {}).get("id"))) or row.get("textId"),
                              "position": row["position"], "source": f"game/Json/UILevelMapLoadConfig/{level}.json"}
                             for key, row in (config.get("staticElements") or {}).items()
                             if row.get("type") == 2 and isinstance(row.get("position"), dict)
                             and all(isinstance(row["position"].get(axis), (int, float)) for axis in ("x", "z"))]
        augmented, regions, withheld = project_regions(records, events, labels)
        audit.update(regions=len(regions), withheldMusicReferences=withheld)
        return augmented, regions, audit
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return events, [], {"status": "unavailable", "detail": str(exc)[:1600]}
