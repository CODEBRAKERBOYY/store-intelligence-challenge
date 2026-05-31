from __future__ import annotations

import struct
from pathlib import Path


def parse_mp4_metadata(path: Path) -> dict:
    with path.open("rb") as handle:
        duration = None
        width = None
        height = None
        timescale = None
        for start, end, box_type in _find_path(handle, ["moov", "mvhd"]):
            handle.seek(start)
            data = handle.read(min(120, end - start))
            version = data[0]
            if version == 1:
                timescale = _u32(data, 20)
                raw_duration = _u64(data, 24)
            else:
                timescale = _u32(data, 12)
                raw_duration = _u32(data, 16)
            duration = raw_duration / timescale if timescale else 0
            break

        for trak_start, trak_end, _ in _find_path(handle, ["moov", "trak"]):
            handler = None
            for h_start, h_end, _ in _find_path(handle, ["mdia", "hdlr"], trak_start, trak_end):
                handle.seek(h_start)
                data = handle.read(min(40, h_end - h_start))
                if len(data) >= 12:
                    handler = data[8:12].decode("latin1")
            if handler != "vide":
                continue
            for tkhd_start, tkhd_end, _ in _find_path(handle, ["tkhd"], trak_start, trak_end):
                handle.seek(tkhd_start)
                data = handle.read(min(100, tkhd_end - tkhd_start))
                version = data[0]
                if version == 1:
                    width = _u32(data, 84) / 65536
                    height = _u32(data, 88) / 65536
                else:
                    width = _u32(data, 76) / 65536
                    height = _u32(data, 80) / 65536
                break

    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "duration_s": round(duration or 0, 3),
        "timescale": timescale,
        "width": int(width or 0),
        "height": int(height or 0),
    }


def _boxes(handle, start: int = 0, end: int | None = None):
    if end is None:
        handle.seek(0, 2)
        end = handle.tell()
    handle.seek(start)
    while handle.tell() + 8 <= end:
        pos = handle.tell()
        header = handle.read(8)
        if len(header) < 8:
            break
        size = _u32(header, 0)
        box_type = header[4:8].decode("latin1")
        header_size = 8
        if size == 1:
            size = _u64(handle.read(8), 0)
            header_size = 16
        elif size == 0:
            size = end - pos
        if size < header_size:
            break
        yield pos + header_size, pos + size, box_type
        handle.seek(pos + size)


def _find_path(handle, path: list[str], start: int = 0, end: int | None = None):
    ranges = [(start, end)]
    for part in path:
        found = []
        for range_start, range_end in ranges:
            for box_start, box_end, box_type in _boxes(handle, range_start, range_end):
                if box_type == part:
                    found.append((box_start, box_end, box_type))
        ranges = [(box_start, box_end) for box_start, box_end, _ in found]
        result = found
    return result


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack(">I", data[offset : offset + 4])[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack(">Q", data[offset : offset + 8])[0]
