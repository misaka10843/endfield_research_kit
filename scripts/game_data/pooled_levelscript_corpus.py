"""Authenticate a bounded pooled-LevelScript profile against the VFS audit.

Unsupported records remain diagnostics, never partial named music graphs.
An admitted record closes at EOF and matches the selected logical VFS bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from scripts.game_data.game_file_store import read_game_file
from scripts.game_data.memorypack.corpus_gate import _read_outer_and_ledger
from scripts.game_data.pooled_levelscript import decode_levelscript
from scripts.game_data.pooled_levelscript_native import load_profile
from scripts.game_data.string_pool import decode_pool
from scripts.repo_paths import REPO_ROOT

DEFAULT_OUTER = REPO_ROOT / "reports/audio/music_regions_vfs.json"
DEFAULT_LEDGER = REPO_ROOT / "reports/audio/music_regions_vfs_files.jsonl.gz"

POOL_PATH = re.compile(r"^Data/Json/StringPool/StringPool([0-9]+)\.byte$")
SCRIPT_PATH = re.compile(r"^Data/Json/LevelScriptData/([^/]+)/([0-9]+)\.json$")
UI_PATH = re.compile(r"^Data/Json/UILevelMapLoadConfig/[^/]+\.json$")


def collect_pooled_scripts(*, export_root: Path, metadata: Path, gameassembly: Path,
                           expected_input_set_sha256: str, outer_path: Path = DEFAULT_OUTER,
                           ledger_path: Path = DEFAULT_LEDGER) -> tuple[list[dict], dict[int, str], dict]:
    profile, native = load_profile(metadata=metadata, gameassembly=gameassembly)
    audit = {"schema": "endfield.pooled-levelscript-corpus.v1", "status": native["status"],
             "native": native, "evidenceLevel": "direct", "runtimeObserved": False,
             "diagnostics": [], "inputSetSha256": expected_input_set_sha256}
    if profile is None:
        return [], {}, audit
    outer, _, ledger, provenance = _read_outer_and_ledger(
        outer_path, ledger_path, expected_input_set_sha256=expected_input_set_sha256)
    selected_root = metadata.resolve().parents[2]
    for role in ("primaryAssets", "fallbackAssets"):
        if Path(outer[role]).resolve().parent != selected_root:
            raise ValueError(f"pooled LevelScript audit {role} is outside selected game: {outer[role]}")
    selected = [row for row in ledger if row.get("blockName") == "JsonData" and
                (POOL_PATH.fullmatch(str(row.get("virtualPath"))) or SCRIPT_PATH.fullmatch(str(row.get("virtualPath")))
                 or UI_PATH.fullmatch(str(row.get("virtualPath"))))]
    if not selected:
        raise ValueError("selected VFS ledger contains no pooled LevelScript inputs")
    payloads = {}
    sources = {}
    for row in selected:
        virtual = row["virtualPath"]
        if (row.get("status") != "verified" or row.get("boundaryStatus") != "boundary_verified"
                or row.get("inputSetSha256") != expected_input_set_sha256.upper()):
            raise ValueError(f"unverified pooled LevelScript input: {virtual}")
        relative = virtual.removeprefix("Data/")
        if ".." in Path(relative).parts or relative in payloads:
            raise ValueError(f"unsafe or duplicate pooled LevelScript path: {virtual}")
        data = read_game_file(export_root, relative)
        actual = hashlib.md5(data).hexdigest().upper()
        if len(data) != row["length"] or actual != row["recomputedFileDataMd5"].upper():
            raise ValueError(f"pooled LevelScript export/VFS mismatch: {virtual}; "
                             f"length={len(data)}/{row['length']}, MD5={actual}/{row['recomputedFileDataMd5']}")
        payloads[relative] = data
        sources[relative] = {"virtualPath": virtual, "length": len(data), "md5": actual,
                             "sha256": hashlib.sha256(data).hexdigest().upper()}
    blocks = {int(match.group(1)): payloads[virtual.removeprefix("Data/")]
              for row in selected if (match := POOL_PATH.fullmatch(virtual := row["virtualPath"]))}
    pool = decode_pool(blocks, block_size=profile["stringPool"]["blockSize"])
    result, refused, decoded = [], 0, 0
    failures, levels = Counter(), {}
    for relative, data in sorted(payloads.items()):
        match = SCRIPT_PATH.fullmatch("Data/" + relative)
        if match is None:
            continue
        coverage = levels.setdefault(match.group(1), {"records": 0, "decoded": 0, "unsupported": 0, "musicRecords": 0})
        coverage["records"] += 1
        try:
            record = decode_levelscript(data, pool, profile)
            root = record["data"]
            if root["scriptId"] != int(match.group(2)):
                raise ValueError(f"script ID differs from VFS owner: {root['scriptId']}/{match.group(2)}")
            decoded += 1
            coverage["decoded"] += 1
            actions = ((root.get("actionMap") or {}).get("dataMap") or {}).get("actionList") or []
            if any(action and action.get("$type") == "Beyond.Gameplay.Actions.PostMusicEvent" for action in actions):
                coverage["musicRecords"] += 1
                result.append({"levelId": match.group(1), "scriptId": root["scriptId"],
                               "source": sources[relative], "record": record})
        except (ValueError, KeyError, TypeError) as exc:
            refused += 1
            coverage["unsupported"] += 1
            detail = str(exc)
            category = "unknownUnionTag" if "union tag not admitted" in detail else "unknownWrapper" if "wrapper not admitted" in detail else "unsupportedDictionaryAlignment" if "dictionary pair alignment" in detail else "framingOrValueRejected"
            failures[category] += 1
            if len(audit["diagnostics"]) < 32:
                audit["diagnostics"].append({"source": relative, "diagnostic": str(exc)[:1200]})
    audit.update(status="validatedProfileSubset", decodedRecords=decoded, unsupportedRecords=refused,
                 musicRecords=len(result), diagnosticsTruncated=refused > len(audit["diagnostics"]),
                 failureCategories=dict(sorted(failures.items())), levelCoverage=dict(sorted(levels.items())),
                 provenance=provenance, sources=list(sources.values()),
                 evidenceBoundary="Authenticated current VFS bytes and selected native profile; only whole EOF records admitted. Other records remain unsupported. Stored graph paths do not prove runtime activation.")
    return result, pool, audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--gameassembly", type=Path, required=True)
    parser.add_argument("--expected-input-set-sha256", required=True)
    parser.add_argument("--outer-summary", type=Path, default=DEFAULT_OUTER)
    parser.add_argument("--outer-ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records, _, audit = collect_pooled_scripts(export_root=args.export_root, metadata=args.metadata,
        gameassembly=args.gameassembly, expected_input_set_sha256=args.expected_input_set_sha256,
        outer_path=args.outer_summary, ledger_path=args.outer_ledger)
    if audit["status"] != "validatedProfileSubset":
        print(f"pooled LevelScript corpus skipped: {audit['status']}: {audit.get('native', {}).get('detail', '')}", file=sys.stderr)
        return 1
    destination = args.output.resolve()
    if not any(part in ("reports", "tmp", "scratch") for part in destination.parts):
        raise ValueError("corpus output must stay under reports/, tmp/ or scratch/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"audit": audit, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: audit.get(key) for key in ("status", "decodedRecords", "unsupportedRecords", "musicRecords")}))
    return 0 if audit["status"] == "validatedProfileSubset" else 1


if __name__ == "__main__":
    raise SystemExit(main())
