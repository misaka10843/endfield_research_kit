"""Explicit selected-native gate for the pooled LevelScript music profile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.common import check_installed_native_inputs
from scripts.game_data.contracts import CONTRACTS_DIR

CONTRACT = CONTRACTS_DIR / "pooled_levelscript_music_native.json"
SCHEMA = "endfield.pooled-levelscript-music-native.v1"


def load_profile(*, metadata: Path | None, gameassembly: Path | None,
                 contract_path: Path = CONTRACT) -> tuple[dict | None, dict]:
    contract = json.loads(contract_path.read_bytes())
    if contract.get("schema") != SCHEMA or contract.get("status") != "direct-selected-build":
        return None, {"status": "mismatched", "detail": "unsupported pooled LevelScript contract"}
    if metadata is None or gameassembly is None:
        return None, {"status": "missing", "detail": "explicit metadata and GameAssembly paths are required"}
    inputs = contract["nativeInputs"]
    gate = check_installed_native_inputs(inputs["GameAssembly.dll"], inputs["global-metadata.dat"],
                                         metadata=metadata, gameassembly=gameassembly)
    audit = {"status": gate.status, "detail": gate.detail, "nativeInputs": inputs,
             "evidenceLevel": "direct", "runtimeObserved": False}
    if gate.status != "validated":
        return None, audit
    # Hash the selected native code windows as well as the complete input pair.
    from scripts.game_data.il2cpp.native_image import NativeImage
    image = NativeImage(gameassembly, metadata, label="pooledLevelScriptMusic").pe
    for window in contract["nativeReaderWindows"]:
        data = image.bytes_at_va(image.image_base + window["rva"], window["length"])
        actual = hashlib.sha256(data).hexdigest().upper()
        if actual != window["sha256"]:
            return None, {**audit, "status": "mismatched", "detail": "native reader window differs",
                          "methods": window["methods"], "expected": window["sha256"], "actual": actual}
    return contract, audit
