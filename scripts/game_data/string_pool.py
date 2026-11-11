"""Bounded USPC v1 string blocks and the indexed BeyondReadString profile.

This reads the stored representation only. Callers authenticating a selected
client must admit its native profile and source files before naming references.
"""
from __future__ import annotations

import struct
from collections.abc import Mapping


def decompress_lz4(source: bytes, expected: int) -> bytes:
    """Standard LZ4 block, with exact input/output bounds (not inverted LZ4)."""
    if not 0 <= expected <= 64 * 1024 * 1024:
        raise ValueError(f"USPC decoded length outside limit: {expected}")
    output = bytearray()
    cursor = 0

    def extended(value: int) -> int:
        nonlocal cursor
        if value == 15:
            while True:
                if cursor >= len(source):
                    raise ValueError("USPC LZ4 truncated extended length")
                byte = source[cursor]
                cursor += 1
                value += byte
                if byte != 255:
                    break
        return value

    while cursor < len(source):
        token = source[cursor]
        cursor += 1
        length = extended(token >> 4)
        if length > len(source) - cursor or length > expected - len(output):
            raise ValueError("USPC LZ4 literal outside bounds")
        output.extend(source[cursor:cursor + length])
        cursor += length
        if cursor == len(source):
            break
        if cursor + 2 > len(source):
            raise ValueError("USPC LZ4 truncated match offset")
        offset = int.from_bytes(source[cursor:cursor + 2], "little")
        cursor += 2
        if not 0 < offset <= len(output):
            raise ValueError(f"USPC LZ4 invalid match offset: {offset}")
        length = extended(token & 15) + 4
        if length > expected - len(output):
            raise ValueError("USPC LZ4 match outside output")
        for _ in range(length):
            output.append(output[-offset])
    if cursor != len(source) or len(output) != expected:
        raise ValueError(f"USPC LZ4 length mismatch: {len(output)}/{expected}")
    return bytes(output)


def decode_block(data: bytes, *, first: bool = False) -> list[str]:
    start = 4 if first else 0  # File-zero prefix is retained as an opaque word.
    if len(data) < start + 24:
        raise ValueError("USPC truncated header")
    magic, version, flags, count, raw_size, stored_size = struct.unpack_from("<6I", data, start)
    if (magic != int.from_bytes(b"USPC", "little") or version != 1
            or flags not in (0, 1) or not 0 < count <= 10000
            or stored_size != len(data) - start - 24):
        raise ValueError("USPC unsupported header or inconsistent stored length")
    payload = data[start + 24:]
    raw = decompress_lz4(payload, raw_size) if flags else payload
    if len(raw) != raw_size or len(raw) < 4 + count * 4:
        raise ValueError("USPC raw block length mismatch")
    if struct.unpack_from("<I", raw)[0] != count:
        raise ValueError("USPC inner/outer count mismatch")
    base = cursor = 4 + count * 4
    result = []
    for ordinal in range(count):
        relative = struct.unpack_from("<I", raw, 4 + ordinal * 4)[0]
        if base + relative != cursor or cursor + 4 > len(raw):
            raise ValueError(f"USPC offset directory mismatch at string {ordinal}")
        length = struct.unpack_from("<i", raw, cursor)[0]
        cursor += 4
        if not 0 <= length <= len(raw) - cursor:
            raise ValueError(f"USPC string {ordinal} outside block")
        result.append(raw[cursor:cursor + length].decode("utf-8", errors="strict"))
        cursor += length
    if cursor != len(raw):
        raise ValueError(f"USPC trailing bytes: {len(raw) - cursor}")
    return result


def decode_pool(blocks: Mapping[int, bytes], *, block_size: int) -> dict[int, str]:
    if block_size != 10000 or not blocks or sorted(blocks) != list(range(max(blocks) + 1)):
        raise ValueError("USPC unsupported block size or noncontiguous block set")
    result = {}
    for index, data in sorted(blocks.items()):
        strings = decode_block(data, first=index == 0)
        if index != max(blocks) and len(strings) != block_size:
            raise ValueError(f"USPC short nonterminal block {index}")
        result.update((index * block_size + local, value) for local, value in enumerate(strings))
    return result


def resolve_string(pool: Mapping[int, str], index: int) -> str | None:
    if index == -2:
        return None
    if index == -1:
        return ""
    if index not in pool:
        raise ValueError(f"BeyondReadString index absent from selected pool: {index}")
    return pool[index]
