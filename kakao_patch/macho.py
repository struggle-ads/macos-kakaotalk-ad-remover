"""Bounded Mach-O edits confined to unused load-command padding."""

import plistlib
import struct
from dataclasses import dataclass


class MachOError(ValueError):
    pass


@dataclass(frozen=True)
class Slice:
    offset: int
    size: int
    cpu: int
    filetype: int
    commands: tuple
    command_bytes: int
    first_content: int


def _unpack(fmt, data, offset):
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise MachOError("Truncated Mach-O structure")
    return struct.unpack_from(fmt, data, offset)


def slices(data):
    magic, = _unpack(">I", data, 0)
    ranges = []
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        count, = _unpack(">I", data, 4)
        if not 1 <= count <= 16:
            raise MachOError("Invalid universal architecture count")
        stride = 32 if magic == 0xCAFEBABF else 20
        for index in range(count):
            if stride == 32:
                cpu, _, start, length, _, _ = _unpack(">IIQQII", data, 8 + index * stride)
            else:
                cpu, _, start, length, _ = _unpack(">IIIII", data, 8 + index * stride)
            if start < 8 + count * stride or length < 32 or start + length > len(data):
                raise MachOError("Invalid universal slice bounds")
            ranges.append((start, length, cpu))
        ordered = sorted(ranges)
        if any(a + size > b for (a, size, _), (b, _, _) in zip(ordered, ordered[1:])):
            raise MachOError("Overlapping universal slices")
    else:
        ranges = [(0, len(data), None)]

    result = []
    for start, length, declared_cpu in ranges:
        view = memoryview(data)[start:start + length]
        magic, cpu, _, filetype, count, command_bytes, _, _ = _unpack("<8I", view, 0)
        if magic != 0xFEEDFACF or cpu not in (0x01000007, 0x0100000C):
            raise MachOError("Only 64-bit Intel and Apple Silicon Mach-O files are supported")
        if declared_cpu is not None and cpu != declared_cpu:
            raise MachOError("Universal CPU header mismatch")
        if filetype not in (2, 6, 8) or count > 4096 or 32 + command_bytes > length:
            raise MachOError("Invalid Mach-O load-command table")
        pos = 32
        commands = []
        first_content = length
        for _ in range(count):
            command, size = _unpack("<II", view, pos)
            if size < 8 or size % 8 or pos + size > 32 + command_bytes:
                raise MachOError("Invalid load-command size")
            commands.append((command, pos, size))
            if command == 0x19:
                if size < 72:
                    raise MachOError("Truncated segment")
                fileoff, filesize = _unpack("<QQ", view, pos + 40)
                section_count, = _unpack("<I", view, pos + 64)
                if 72 + 80 * section_count > size or fileoff + filesize > length:
                    raise MachOError("Invalid segment bounds")
                if fileoff and filesize:
                    first_content = min(first_content, fileoff)
                for index in range(section_count):
                    section = pos + 72 + 80 * index
                    section_size, section_offset = _unpack("<QI", view, section + 40)
                    flags, = _unpack("<I", view, section + 64)
                    if flags & 0xFF in (1, 12, 18):  # zero-fill sections have no file bytes
                        continue
                    if section_size:
                        if section_offset + section_size > length:
                            raise MachOError("Invalid section bounds")
                        first_content = min(first_content, section_offset)
            if command in (0x21, 0x2C):
                if size < 20 or _unpack("<I", view, pos + 16)[0]:
                    raise MachOError("Encrypted executable is unsupported")
            pos += size
        if pos != 32 + command_bytes or first_content < pos:
            raise MachOError("Load commands overlap content")
        result.append(Slice(start, length, cpu, filetype, tuple(commands), command_bytes, first_content))
    return result


def dylib_names(data):
    result = []
    for item in slices(data):
        names = []
        for command, pos, size in item.commands:
            if command not in (0xC, 0x80000018, 0x8000001F, 0x80000023):
                continue
            name_offset, = _unpack("<I", data, item.offset + pos + 8)
            if not 24 <= name_offset < size:
                raise MachOError("Invalid dylib name offset")
            raw = data[item.offset + pos + name_offset:item.offset + pos + size]
            if b"\0" not in raw:
                raise MachOError("Unterminated dylib name")
            try:
                names.append(raw.split(b"\0", 1)[0].decode("utf-8"))
            except UnicodeDecodeError as error:
                raise MachOError("Invalid dylib name") from error
        result.append(names)
    return result


def inject_dylib(data, name):
    if not name or "\0" in name or len(name.encode()) > 1024:
        raise MachOError("Invalid library name")
    path = name.encode("utf-8") + b"\0"
    size = (24 + len(path) + 7) & ~7
    command = struct.pack("<6I", 0xC, size, 24, 0, 0x10000, 0x10000) + path
    command = command.ljust(size, b"\0")
    result = bytearray(data)
    for item, names in zip(slices(data), dylib_names(data)):
        if name in names:
            continue
        end = 32 + item.command_bytes
        if end + size > item.first_content:
            raise MachOError("Insufficient load-command padding; no bytes changed")
        if any(data[item.offset + end:item.offset + end + size]):
            raise MachOError("Load-command padding contains data; refusing overwrite")
        struct.pack_into("<II", result, item.offset + 16,
                         len(item.commands) + 1, item.command_bytes + size)
        result[item.offset + end:item.offset + end + size] = command
    return bytes(result)


def entitlements(data):
    found = []
    for item in slices(data):
        current = {}
        for command, pos, size in item.commands:
            if command != 0x1D:
                continue
            if size < 16:
                raise MachOError("Truncated code signature command")
            offset, length = _unpack("<II", data, item.offset + pos + 8)
            if offset + length > item.size:
                raise MachOError("Invalid code signature bounds")
            signature = data[item.offset + offset:item.offset + offset + length]
            magic, total, count = _unpack(">III", signature, 0)
            if magic != 0xFADE0CC0 or total > length or 12 + count * 8 > total:
                raise MachOError("Invalid code signature superblob")
            for index in range(count):
                kind, blob_offset = _unpack(">II", signature, 12 + 8 * index)
                if kind != 5:
                    continue
                blob_magic, blob_length = _unpack(">II", signature, blob_offset)
                if blob_magic != 0xFADE7171 or blob_length < 8 or blob_offset + blob_length > total:
                    raise MachOError("Invalid entitlement blob")
                try:
                    current = plistlib.loads(signature[blob_offset + 8:blob_offset + blob_length])
                except Exception as error:
                    raise MachOError("Cannot decode original entitlements") from error
                if not isinstance(current, dict):
                    raise MachOError("Entitlements must be a dictionary")
        found.append(current)
    if any(value != found[0] for value in found):
        raise MachOError("Architecture entitlements differ")
    return found[0]
