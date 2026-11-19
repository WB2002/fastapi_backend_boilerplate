"""Pure-Python MaxMind MMDB v2 test database builder, inspector, and verifier."""

from __future__ import annotations

import ipaddress
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MMDB_MARKER = b"\xab\xcd\xefMaxMind.com"

DEFAULT_GEOIP_CIDRS: tuple[tuple[str, str, str], ...] = (
    ("8.8.8.0/24", "US", "United States"),
    ("81.2.69.0/24", "GB", "United Kingdom"),
    ("103.255.4.0/24", "PK", "Pakistan"),
    ("2001:4860:4860::/48", "US", "United States"),
    ("2a00:1450:4009::/48", "GB", "United Kingdom"),
)

TEST_IPS: dict[str, str] = {
    "US_IPV4": "8.8.8.8",
    "GB_IPV4": "81.2.69.142",
    "PK_IPV4": "103.255.4.1",
    "US_IPV6": "2001:4860:4860::8888",
    "GB_IPV6": "2a00:1450:4009::80e",
    "UNKNOWN_IPV4": "198.51.100.1",
    "UNKNOWN_IPV6": "2001:db8::1",
    "PRIVATE_IPV4": "127.0.0.1",
    "PRIVATE_IPV6": "::1",
}

DEFAULT_EXPECTED_LOOKUPS: dict[str, str | None] = {
    "8.8.8.8": "US",
    "8.8.8.1": "US",
    "81.2.69.142": "GB",
    "81.2.69.1": "GB",
    "103.255.4.1": "PK",
    "2001:4860:4860::8888": "US",
    "2a00:1450:4009::80e": "GB",
    "198.51.100.1": None,
    "192.0.2.1": None,
    "203.0.113.1": None,
    "2001:db8::1": None,
    "127.0.0.1": None,
    "10.0.0.1": None,
    "192.168.1.1": None,
    "::1": None,
}


def _encode_ctrl(type_num: int, size: int) -> bytes:
    if size < 29:
        size_code = size
        extra = b""
    elif size < 285:
        size_code = 29
        extra = bytes([size - 29])
    elif size < 65821:
        size_code = 30
        extra = (size - 285).to_bytes(2, "big")
    else:
        size_code = 31
        extra = (size - 65821).to_bytes(3, "big")

    if type_num >= 8:
        return bytes([size_code, type_num - 7]) + extra
    return bytes([(type_num << 5) | size_code]) + extra


def _encode_uint(val: int, type_num: int) -> bytes:
    raw = b"" if val == 0 else val.to_bytes((val.bit_length() + 7) // 8, "big")
    return _encode_ctrl(type_num, len(raw)) + raw


def _encode_value(val: Any) -> bytes:
    if isinstance(val, bool):
        return _encode_ctrl(14, 1 if val else 0)
    if isinstance(val, int):
        return _encode_uint(val, 6)
    if isinstance(val, str):
        raw = val.encode("utf-8")
        return _encode_ctrl(2, len(raw)) + raw
    if isinstance(val, bytes | bytearray):
        raw = bytes(val)
        return _encode_ctrl(4, len(raw)) + raw
    if isinstance(val, list | tuple):
        parts = [_encode_ctrl(11, len(val))]
        for item in val:
            parts.append(_encode_value(item))
        return b"".join(parts)
    if isinstance(val, Mapping):
        parts = [_encode_ctrl(7, len(val))]
        for key, item in val.items():
            parts.append(_encode_value(str(key)))
            parts.append(_encode_value(item))
        return b"".join(parts)
    msg = f"Unsupported MMDB value type: {type(val)!r}"
    raise TypeError(msg)


def _encode_metadata(node_count: int) -> bytes:
    entries: list[tuple[str, bytes]] = [
        ("binary_format_major_version", _encode_uint(2, 5)),
        ("binary_format_minor_version", _encode_uint(0, 5)),
        ("build_epoch", _encode_uint(1_700_000_000, 9)),
        ("database_type", _encode_value("GeoLite2-Country")),
        ("description", _encode_value({"en": "E2E Test GeoLite2-Country"})),
        ("ip_version", _encode_uint(6, 5)),
        ("languages", _encode_value(["en"])),
        ("node_count", _encode_uint(node_count, 6)),
        ("record_size", _encode_uint(24, 5)),
    ]
    parts = [_encode_ctrl(7, len(entries))]
    for key, encoded_val in entries:
        parts.append(_encode_value(key))
        parts.append(encoded_val)
    return b"".join(parts)


def _cidr_bit_prefixes(cidr: str) -> list[list[int]]:
    net = ipaddress.ip_network(cidr, strict=False)
    if isinstance(net, ipaddress.IPv4Network):
        addr_int = int(net.network_address)
        v4_bits = [(addr_int >> (31 - i)) & 1 for i in range(net.prefixlen)]
        compat_prefix = [0] * 96 + v4_bits
        mapped_prefix = [0] * 80 + [1] * 16 + v4_bits
        return [compat_prefix, mapped_prefix]
    addr_int = int(net.network_address)
    return [[(addr_int >> (127 - i)) & 1 for i in range(net.prefixlen)]]


def _fill_empty_leaves(
    nodes: list[list[int]],
    node_idx: int,
    leaf_val: int,
) -> None:
    stack = [node_idx]
    while stack:
        curr = stack.pop()
        for side in (0, 1):
            nxt = nodes[curr][side]
            if nxt == 0:
                nodes[curr][side] = leaf_val
            elif nxt > 0:
                stack.append(nxt)


def _insert_prefix(
    nodes: list[list[int]],
    bits: Sequence[int],
    data_offset: int,
) -> None:
    if not bits:
        _fill_empty_leaves(nodes, 0, -(data_offset + 1))
        return
    curr = 0
    leaf_val = -(data_offset + 1)
    last_idx = len(bits) - 1
    for idx, bit in enumerate(bits):
        if idx == last_idx:
            existing = nodes[curr][bit]
            if existing > 0:
                _fill_empty_leaves(nodes, existing, leaf_val)
            else:
                nodes[curr][bit] = leaf_val
            return
        nxt = nodes[curr][bit]
        if nxt <= 0:
            child = len(nodes)
            nodes.append([nxt, nxt] if nxt < 0 else [0, 0])
            nodes[curr][bit] = child
            curr = child
        else:
            curr = nxt


def build_test_mmdb(
    records: Sequence[tuple[str, str, str]] = DEFAULT_GEOIP_CIDRS,
) -> bytes:
    """Build a valid MaxMind DB v2 binary file (``ip_version=6``, 24-bit records)."""
    data_chunks: list[bytes] = []
    data_offsets: dict[tuple[str, str], int] = {}
    current_offset = 0

    for _cidr, iso_code, country_name in records:
        key = (iso_code.upper(), country_name)
        if key not in data_offsets:
            payload_dict = {
                "country": {
                    "iso_code": key[0],
                    "names": {"en": key[1]},
                },
                "registered_country": {
                    "iso_code": key[0],
                    "names": {"en": key[1]},
                },
            }
            encoded = _encode_value(payload_dict)
            data_offsets[key] = current_offset
            data_chunks.append(encoded)
            current_offset += len(encoded)

    nodes: list[list[int]] = [[0, 0]]
    for cidr, iso_code, country_name in records:
        data_offset = data_offsets[(iso_code.upper(), country_name)]
        for bit_prefix in _cidr_bit_prefixes(cidr):
            _insert_prefix(nodes, bit_prefix, data_offset)

    node_count = len(nodes)
    tree_parts: list[bytes] = []
    for left, right in nodes:
        for val in (left, right):
            if val == 0:
                resolved = node_count
            elif val > 0:
                resolved = val
            else:
                resolved = node_count + 16 + ((-val) - 1)
            tree_parts.append(resolved.to_bytes(3, "big"))

    search_tree = b"".join(tree_parts)
    separator = b"\x00" * 16
    data_section = b"".join(data_chunks)
    metadata = _encode_metadata(node_count)
    return search_tree + separator + data_section + _MMDB_MARKER + metadata


def _read_size_and_type(buf: bytes, offset: int) -> tuple[int, int, int]:
    ctrl = buf[offset]
    offset += 1
    type_num = ctrl >> 5
    if type_num == 0:
        type_num = buf[offset] + 7
        offset += 1
    size_bits = ctrl & 0x1F
    if size_bits < 29:
        size = size_bits
    elif size_bits == 29:
        size = 29 + buf[offset]
        offset += 1
    elif size_bits == 30:
        size = 285 + int.from_bytes(buf[offset : offset + 2], "big")
        offset += 2
    else:
        size = 65821 + int.from_bytes(buf[offset : offset + 3], "big")
        offset += 3
    return type_num, size, offset


def _decode_pointer(
    buf: bytes,
    size: int,
    offset: int,
    data_base: int,
) -> tuple[int, int]:
    ptr_size = (size >> 3) + 1
    packed = int.from_bytes(buf[offset : offset + ptr_size], "big")
    offset += ptr_size
    if ptr_size == 1:
        ptr = ((size & 0x7) << 8) + packed
    elif ptr_size == 2:
        ptr = ((size & 0x7) << 16) + packed + 2048
    elif ptr_size == 3:
        ptr = ((size & 0x7) << 24) + packed + 526336
    else:
        ptr = packed
    return data_base + ptr, offset


def _decode_mmdb_value(
    buf: bytes,
    offset: int,
    data_base: int,
) -> tuple[Any, int]:
    type_num, size, offset = _read_size_and_type(buf, offset)
    if type_num == 1:
        target, next_offset = _decode_pointer(buf, size, offset, data_base)
        val, _ = _decode_mmdb_value(buf, target, data_base)
        return val, next_offset
    if type_num == 2:
        return buf[offset : offset + size].decode("utf-8"), offset + size
    if type_num == 4:
        return buf[offset : offset + size], offset + size
    if type_num in (5, 6, 9, 10):
        return int.from_bytes(buf[offset : offset + size], "big"), offset + size
    if type_num == 7:
        mapping: dict[str, Any] = {}
        for _ in range(size):
            key, offset = _decode_mmdb_value(buf, offset, data_base)
            val, offset = _decode_mmdb_value(buf, offset, data_base)
            mapping[str(key)] = val
        return mapping, offset
    if type_num == 8:
        raw_int = int.from_bytes(buf[offset : offset + size], "big", signed=True)
        return raw_int, offset + size
    if type_num == 11:
        items: list[Any] = []
        for _ in range(size):
            item, offset = _decode_mmdb_value(buf, offset, data_base)
            items.append(item)
        return items, offset
    if type_num == 14:
        return bool(size), offset
    return buf[offset : offset + size], offset + size


def _read_tree_record(buf: bytes, node_idx: int, bit: int, record_size: int) -> int:
    node_bytes = (record_size * 2) // 8
    offset = node_idx * node_bytes
    if record_size == 24:
        rec_off = offset + (3 if bit else 0)
        return int.from_bytes(buf[rec_off : rec_off + 3], "big")
    if record_size == 28:
        mid = buf[offset + 3]
        if bit == 0:
            return ((mid >> 4) << 24) | int.from_bytes(buf[offset : offset + 3], "big")
        return ((mid & 0x0F) << 24) | int.from_bytes(
            buf[offset + 4 : offset + 7],
            "big",
        )
    rec_off = offset + (4 if bit else 0)
    return int.from_bytes(buf[rec_off : rec_off + 4], "big")


@dataclass(frozen=True, slots=True)
class MmdbInspectionResult:
    """Decoded metadata and lookup helper for an MMDB v2 file."""

    raw: bytes
    metadata: dict[str, Any]
    node_count: int
    record_size: int
    ip_version: int
    search_tree_size: int
    data_base: int

    def lookup_record(self, ip_str: str) -> dict[str, Any] | None:
        """Resolve raw MMDB record dictionary for ``ip_str``."""
        addr = ipaddress.ip_address(ip_str)

        node = 0
        if isinstance(addr, ipaddress.IPv4Address):
            if self.ip_version == 6:
                for _ in range(96):
                    node = _read_tree_record(self.raw, node, 0, self.record_size)
                    if node >= self.node_count:
                        break
            bits = [(int(addr) >> (31 - i)) & 1 for i in range(32)]
        else:
            if self.ip_version != 6:
                return None
            bits = [(int(addr) >> (127 - i)) & 1 for i in range(128)]

        for bit in bits:
            if node >= self.node_count:
                break
            node = _read_tree_record(self.raw, node, bit, self.record_size)

        if node == self.node_count:
            return None
        if node < self.node_count:
            return None
        data_offset = self.search_tree_size + (node - self.node_count)
        decoded, _ = _decode_mmdb_value(self.raw, data_offset, self.data_base)
        return decoded if isinstance(decoded, dict) else None

    def lookup(self, ip_str: str) -> str | None:
        """Return uppercase ISO 3166-1 alpha-2 country code or ``None``."""
        rec = self.lookup_record(ip_str)
        if not rec:
            return None
        country = rec.get("country") or rec.get("registered_country")
        if isinstance(country, dict):
            iso = country.get("iso_code")
            if isinstance(iso, str) and iso:
                return iso.upper()
        return None


def inspect_mmdb(path: Path) -> MmdbInspectionResult:
    """Parse and validate a MaxMind MMDB binary file using pure stdlib."""
    raw = path.read_bytes()
    marker_idx = raw.rfind(_MMDB_MARKER)
    if marker_idx < 0:
        msg = f"Invalid MMDB file (missing MaxMind marker): {path}"
        raise ValueError(msg)

    meta_start = marker_idx + len(_MMDB_MARKER)
    metadata, _ = _decode_mmdb_value(raw, meta_start, data_base=meta_start)
    if not isinstance(metadata, dict):
        msg = f"Invalid MMDB metadata in {path}"
        raise ValueError(msg)

    node_count = int(metadata["node_count"])
    record_size = int(metadata["record_size"])
    ip_version = int(metadata["ip_version"])
    search_tree_size = node_count * ((record_size * 2) // 8)
    data_base = search_tree_size + 16
    return MmdbInspectionResult(
        raw=raw,
        metadata=metadata,
        node_count=node_count,
        record_size=record_size,
        ip_version=ip_version,
        search_tree_size=search_tree_size,
        data_base=data_base,
    )


def get_geoip_mmdb_path(
    dest_dir: Path,
    *,
    filename: str = "test-geolite2-country.mmdb",
    records: Sequence[tuple[str, str, str]] | None = None,
) -> Path:
    """Provide a valid GeoIP ``.mmdb`` file inside ``dest_dir``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / filename

    env_path = os.environ.get("E2E_GEOIP_MMDB_PATH", "").strip()
    if env_path and records is None:
        src = Path(env_path)
        if src.is_file():
            inspect_mmdb(src)
            shutil.copy2(src, target)
            return target

    mmdb_bytes = build_test_mmdb(records or DEFAULT_GEOIP_CIDRS)
    target.write_bytes(mmdb_bytes)
    inspect_mmdb(target)
    return target


def verify_mmdb_lookups(
    path: Path,
    expected: Mapping[str, str | None] | None = None,
) -> None:
    """Verify MMDB lookups via pure-Python parser and optional ``geoip2``."""
    inspection = inspect_mmdb(path)
    targets = expected if expected is not None else DEFAULT_EXPECTED_LOOKUPS
    for ip_str, expected_iso in targets.items():
        actual_iso = inspection.lookup(ip_str)
        if actual_iso != expected_iso:
            msg = (
                f"MMDB pure-Python lookup mismatch for {ip_str}: "
                f"expected {expected_iso!r}, got {actual_iso!r}"
            )
            raise AssertionError(msg)

    try:
        import geoip2.database
        import geoip2.errors
    except ImportError:
        return

    with geoip2.database.Reader(str(path)) as reader:
        for ip_str, expected_iso in targets.items():
            try:
                resp = reader.country(ip_str)
                actual = resp.country.iso_code or resp.registered_country.iso_code
            except geoip2.errors.AddressNotFoundError:
                actual = None
            if actual != expected_iso:
                msg = (
                    f"MMDB geoip2 lookup mismatch for {ip_str}: "
                    f"expected {expected_iso!r}, got {actual!r}"
                )
                raise AssertionError(msg)


__all__ = [
    "DEFAULT_EXPECTED_LOOKUPS",
    "DEFAULT_GEOIP_CIDRS",
    "TEST_IPS",
    "MmdbInspectionResult",
    "build_test_mmdb",
    "get_geoip_mmdb_path",
    "inspect_mmdb",
    "verify_mmdb_lookups",
]
