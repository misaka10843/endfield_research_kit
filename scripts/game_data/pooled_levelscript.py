"""Selected-profile MemoryPack LevelScript reader with pooled strings.

Member names/order are the direct generated-wrapper declaration tier. Union
identities come from the reviewed selected-build dispatch contract. A record
is admitted only at physical EOF; no byte search supplies a field cursor.
"""
from __future__ import annotations

import struct
from typing import Any

from scripts.game_data.string_pool import resolve_string

ROOT_TYPE = "Beyond.Gameplay.LevelScriptData"
SCALARS = {"bool": "B", "byte": "B", "sbyte": "b", "short": "h", "ushort": "H",
           "char": "H", "int": "i", "uint": "I", "long": "q", "ulong": "Q",
           "float": "f", "double": "d"}
VECTORS = {"UnityEngine.Vector2": 2, "UnityEngine.Vector3": 3,
           "UnityEngine.Vector4": 4, "UnityEngine.Quaternion": 4}


def _arguments(value: str) -> list[str]:
    result, depth, start = [], 0, 0
    for index, char in enumerate(value):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(value[start:index].strip())
            start = index + 1
    result.append(value[start:].strip())
    return result


class PooledLevelScriptReader:
    def __init__(self, data: bytes, pool: dict[int, str], profile: dict):
        self.data, self.pool, self.profile = data, pool, profile
        self.cursor = 0
        self.nodes = 0
        self.strings: list[dict[str, Any]] = []

    def take(self, width: int) -> bytes:
        if not 0 <= width <= len(self.data) - self.cursor:
            raise ValueError(f"bounded read: offset={self.cursor}, width={width}, EOF={len(self.data)}")
        result = self.data[self.cursor:self.cursor + width]
        self.cursor += width
        return result

    def scalar(self, kind: str) -> Any:
        return struct.unpack("<" + kind, self.take(struct.calcsize(kind)))[0]

    def read(self, kind: str, path: str = "root", *, element: bool = False, depth: int = 0) -> Any:
        start = self.cursor
        self.nodes += 1
        if depth >= 128 or self.nodes > 100000:
            raise ValueError(f"read bound exceeded at {path}")
        try:
            return self._read(kind, path, element=element, depth=depth)
        except (ValueError, KeyError, struct.error, UnicodeError) as exc:
            raise ValueError(f"{path}: type={kind}, start={start}, cursor={self.cursor}: {exc}") from exc

    def _read(self, kind: str, path: str, *, element: bool, depth: int) -> Any:
        if kind in SCALARS:
            value = self.scalar(SCALARS[kind])
            if kind == "bool":
                if value not in (0, 1):
                    raise ValueError(f"invalid bool {value}")
                return bool(value)
            return value
        if kind == "string":
            index = self.scalar("i")
            value = resolve_string(self.pool, index)
            self.strings.append({"path": path, "offset": self.cursor - 4, "poolIndex": index, "value": value})
            return value
        if kind in self.profile["enumWidths"]:
            return int.from_bytes(self.take(self.profile["enumWidths"][kind]), "little", signed=True)
        if kind.startswith("System.Collections.Generic.List`1<") or kind.endswith("[]"):
            is_array = kind.endswith("[]")
            inner = kind[:-2] if is_array else kind[kind.index("<") + 1:-1]
            count = self.scalar("i")
            if count < -1 or count > min(100000, len(self.data) - self.cursor):
                raise ValueError(f"collection count outside bounds: {count}")
            return None if count == -1 else [self.read(inner, f"{path}[{i}]", element=not is_array, depth=depth + 1) for i in range(count)]
        if kind.startswith("System.Collections.Generic.Dictionary`2<"):
            key_type, value_type = _arguments(kind[kind.index("<") + 1:-1])
            count = self.scalar("i")
            if count < -1 or count > min(100000, (len(self.data) - self.cursor) // 2):
                raise ValueError(f"map count outside bounds: {count}")
            if count > 0 and value_type in SCALARS:
                raise ValueError("unmanaged dictionary pair alignment is not admitted by this profile")
            return None if count == -1 else [{
                "key": self.read(key_type, f"{path}[{i}].key", depth=depth + 1),
                "value": self.read(value_type, f"{path}[{i}].value", depth=depth + 1),
            } for i in range(count)]
        if kind in VECTORS:
            return list(struct.unpack("<" + "f" * VECTORS[kind], self.take(4 * VECTORS[kind])))
        if kind in self.profile["unions"]:
            tag = self.scalar("B")
            if tag == 255:
                return None
            if tag == 250:
                tag = self.scalar("H")
            selected = self.profile["unions"][kind].get(str(tag))
            if selected is None:
                raise ValueError(f"union tag not admitted: {tag}")
            kind = selected["type"]
        wrapper = self.profile["wrappers"].get(kind)
        if wrapper is None:
            raise ValueError("wrapper not admitted")
        if not wrapper["rawStruct"] or element:
            count = self.scalar("B")
            if count == 255:
                return None
            if count != len(wrapper["members"]):
                raise ValueError(f"member count mismatch: {count}/{len(wrapper['members'])}")
        result = {"$type": kind, "$start": self.cursor}
        for member in wrapper["members"]:
            result[member["name"]] = self.read(member["type"], f"{path}.{member['name']}", depth=depth + 1)
        result["$end"] = self.cursor
        return result


def decode_levelscript(data: bytes, pool: dict[int, str], profile: dict) -> dict:
    reader = PooledLevelScriptReader(data, pool, profile)
    root = reader.read(ROOT_TYPE)
    if not isinstance(root, dict) or reader.cursor != len(data):
        raise ValueError(f"LevelScript did not close at EOF: {reader.cursor}/{len(data)}")
    return {"data": root, "bytesConsumed": reader.cursor, "stringReferences": reader.strings,
            "evidenceLevel": "direct", "runtimeObserved": False}
