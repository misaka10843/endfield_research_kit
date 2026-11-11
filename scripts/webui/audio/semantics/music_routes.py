"""Conditional State-to-music leaves over the existing typed HIRC projection.

No native provider facts or runtime observations are manufactured here. A
setter only supplies a hypothetical State assignment, assuming that request
executes and the music engine is active. Zero/default keys and other States
remain explicit unresolved predicates; this is deliberately not a Wwise player.
"""
from __future__ import annotations

from collections import Counter, defaultdict

EXACT = {"typedExactV150", "typedExactV150SelectorSubset"}
SWITCH_TYPED = EXACT | {"typedExactV150SelectorBoundaryUnresolved"}
OWNED = {"directReciprocalChild", "recursiveOwnedDescendant"}


def _states(event: dict) -> tuple[dict[int, int], set[int]]:
    values = defaultdict(set)
    for evidence in event.get("evidence") or []:
        for action in evidence.get("actionEvidence") or []:
            if action.get("operation") == "setState" and action.get("actionControlParserStatus") == "typedExactV150":
                group, value = action.get("groupId"), action.get("stateId")
                if isinstance(group, int) and isinstance(value, int):
                    values[group].add(value)
    conflicts = {key for key, choices in values.items() if len(choices) != 1}
    return {key: next(iter(choices)) for key, choices in values.items() if key not in conflicts}, conflicts


def project_music_routes(events: list[dict]) -> dict[str, dict]:
    """Return separate conditional candidates, never direct Event/media edges."""
    engines = []
    for event in events:
        if not event.get("media"):
            continue
        for evidence in event.get("evidence") or []:
            if evidence.get("bankVersion") != 150:
                continue
            nodes, conflicts = {}, set()
            for node in evidence.get("musicNodeEvidence") or []:
                key = node.get("objectId")
                if key in nodes and nodes[key] != node:
                    conflicts.add(key)
                nodes[key] = node
            for key in conflicts:
                nodes.pop(key, None)
            roots = [row["targetId"] for row in evidence.get("actionEvidence") or []
                     if row.get("operation") == "play" and row.get("isRootEventAction") is True
                     and row.get("targetId") in nodes]
            if not roots:
                continue
            groups = {arg.get("groupId") for node in nodes.values() for arg in node.get("arguments") or []
                      if arg.get("groupType") == 1}
            media = {row["mediaId"]: row for row in event["media"] if isinstance(row.get("mediaId"), int)
                     and any(proof.get("bankId") == evidence.get("bankId")
                             and proof.get("bank") == evidence.get("bank")
                             for proof in row.get("wwiseMediaEvidence") or [])}
            engines.append((event["id"], evidence, nodes, roots, groups, media))
    result = {}
    for event in events:
        states, conflicts = _states(event)
        if conflicts:
            # A conflicting group may be overwritten in an order not modeled
            # here. Do not keep only whichever assignment appeared last.
            result[event["id"]] = {"media": [], "audit": {"status": "conflictingStateAssignments",
                "conflictingGroupIds": sorted(conflicts), "complete": False, "runtimeObserved": False}}
            continue
        if not states:
            continue
        found, gaps = {}, Counter()
        steps = 0
        matching_engines = 0
        for engine_id, evidence, nodes, roots, groups, media in engines:
            if not groups.intersection(states):
                continue
            matching_engines += 1

            def walk(node_id, conditions, path, seen, bindings, matched):
                nonlocal steps
                steps += 1
                if steps > 100000 or len(path) >= 64:
                    gaps["traversalBound"] += 1
                    return
                if node_id in seen:
                    gaps["cycle"] += 1
                    return
                node = nodes.get(node_id)
                if node is None:
                    gaps["nodeUnavailable"] += 1
                    return
                seen = seen | {node_id}
                path = path + [node_id]
                kind = node.get("nodeKind")
                if kind == "musicTrack":
                    if node.get("parserConfidence") != "typedExactV150" or not matched:
                        return
                    for source in node.get("sources") or []:
                        media_id = source.get("mediaId")
                        leaf = media.get(media_id)
                        if source.get("sourceParserStatus") != "typedExactV150AkBankSourceData" or not leaf or not leaf.get("src"):
                            continue
                        key = (engine_id, evidence.get("bank"), media_id, leaf["src"])
                        if key not in found:
                            found[key] = {"id": str(media_id), "mediaId": media_id, "name": f"{media_id}.flac",
                                "src": leaf["src"], "duration": leaf.get("duration"),
                                "relationType": "conditionalStateMusicLeaf", "sourceEventId": engine_id,
                                "bankId": evidence.get("bankId"), "bank": evidence.get("bank"),
                                "conditions": conditions, "objectPath": path, "routeCount": 0}
                        found[key]["routeCount"] += 1
                    return
                if kind == "musicSwitchContainer":
                    # A tree can have unresolved siblings. Each admitted leaf
                    # must still carry its own exact reciprocal ownership proof.
                    if node.get("structureStatus") not in SWITCH_TYPED or node.get("parserConfidence") != "reciprocalParentExact":
                        gaps["unprovedSwitchStructure"] += 1
                        return
                    args = node.get("arguments") or []
                    for leaf in node.get("treeLeaves") or []:
                        if leaf.get("ownershipStatus") not in OWNED:
                            gaps["unprovedLeafOwnership"] += 1
                            continue
                        keys = leaf.get("pathKeys") or []
                        if len(args) != len(keys):
                            gaps["argumentLengthMismatch"] += 1
                            continue
                        next_bindings, predicates = dict(bindings), []
                        compatible, positive = True, matched
                        for arg, value in zip(args, keys):
                            group = arg.get("groupId")
                            group_type = arg.get("groupType")
                            identity = (group_type, group)
                            known = states.get(group) if group_type == 1 else None
                            if known is not None and value != known:
                                # In particular, never assert that key zero is
                                # a runtime fallback for a known nonzero State.
                                compatible = False
                                break
                            if value and identity in next_bindings and next_bindings[identity] != value:
                                compatible = False
                                break
                            if value:
                                next_bindings[identity] = value
                            positive |= known is not None and value == known and value != 0
                            predicates.append({"nodeId": node_id, "groupType": group_type, "groupId": group,
                                               "valueId": value, "status": "matchedRequestState" if known is not None
                                               else "unresolvedDefaultKey" if value == 0 else "unobservedStateOrSwitch"})
                        if compatible:
                            walk(leaf.get("audioNodeId"), conditions + predicates, path, seen, next_bindings, positive)
                    return
                if kind not in {"musicSegment", "musicRandomSequenceContainer"} or node.get("structureStatus") not in EXACT:
                    gaps["unprovedMusicStructure"] += 1
                    return
                for child in node.get("childIds") or []:
                    walk(child, conditions, path, seen, bindings, matched)

            for root in roots:
                walk(root, [], [], set(), {}, False)
        status = ("conditionalStaticRoutes" if matching_engines else
                  "noMatchingMusicStateGroup" if engines else "musicEngineProjectionUnavailable")
        result[event["id"]] = {"media": sorted(found.values(), key=lambda row: (row["sourceEventId"], row["mediaId"])),
            "audit": {"status": status, "stateAssignments": [{"groupId": key, "valueId": value}
                       for key, value in sorted(states.items())], "visitedNodes": steps, "withheld": dict(sorted(gaps.items())),
                       "availableEngineProjectionCount": len(engines), "matchingEngineProjectionCount": matching_engines,
                       "complete": bool(matching_engines) and not gaps, "runtimeObserved": False,
                       "evidenceBoundary": "Assumes this State request executes and the named music engine is playing. No matching projected State group does not prove music or another control route is absent. Other selector predicates, default resolution, playlist selection, mixing and audibility remain unobserved. Only individually proved leaf ownership and same-bank typed media sources are admitted."}}
    return result
