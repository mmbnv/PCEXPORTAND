"""Minimal but real PE and ELF parsers (pure python, no dependencies).

These are used by the Analyzer to recover architecture, imported DLLs and
imported symbols from a game's executables.  They deliberately avoid external
tools so analysis works in a bare container; when ``objdump``/``readelf`` are
present the Analyzer augments these results.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

PE_MACHINES = {
    0x014C: ("x86", 32),
    0x8664: ("x86_64", 64),
    0x01C0: ("arm", 32),
    0x01C4: ("arm", 32),
    0xAA64: ("arm64", 64),
    0x0200: ("ia64", 64),
}

ELF_MACHINES = {
    0x03: ("x86", 32),
    0x3E: ("x86_64", 64),
    0x28: ("arm", 32),
    0xB7: ("arm64", 64),
    0xF3: ("riscv", 32),
    0x08: ("mips", 32),
}

# Microsoft PE import-directory DLL names are upper-cased; normalise for matching.
_SYSTEM_PREFIXES = ("api-ms-", "ext-ms-")


@dataclass
class ParsedBinary:
    kind: str = "unknown"  # "pe" | "elf"
    arch: str = "unknown"
    bitness: int | None = None
    machine: int | None = None
    imports: list[str] = field(default_factory=list)      # normalised dll names
    imported_symbols: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    subsystem: int | None = None


def _normalise_dll(name: str) -> str:
    n = name.strip().lower()
    for p in _SYSTEM_PREFIXES:
        if n.startswith(p):
            return n
    if not n.endswith((".dll", ".drv", ".ocx", ".sys", ".so")):
        n += ".dll"
    return n


def parse_pe(data: bytes) -> ParsedBinary:
    out = ParsedBinary(kind="pe")
    if data[:2] != b"MZ":
        raise ValueError("not a PE file")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("bad PE signature")
    coff = e_lfanew + 4
    machine, n_sections, _ts, _psym, _nsym, opt_size, _chars = struct.unpack_from(
        "<HHIIIHH", data, coff
    )
    out.machine = machine
    arch, bits = PE_MACHINES.get(machine, ("unknown", 0))
    out.arch, out.bitness = arch, bits or None

    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    is_pe32_plus = magic == 0x20B
    # Subsystem sits at 0x44 (PE32+) / 0x42 (PE32); data directories start at
    # 0x70 (PE32+) / 0x60 (PE32).
    out.subsystem = struct.unpack_from("<H", data, opt + (0x44 if is_pe32_plus else 0x42))[0]
    dd_off = opt + (0x70 if is_pe32_plus else 0x60)

    sections: list[tuple[str, int, int, int]] = []
    sec_off = opt + opt_size
    for i in range(n_sections):
        base = sec_off + i * 40
        name = data[base : base + 8].rstrip(b"\x00").decode("latin-1", "replace")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((name, vaddr, rawsize, rawptr))
        out.sections.append(name)

    def rva_to_off(rva: int) -> int | None:
        for _name, vaddr, rawsize, rawptr in sections:
            if vaddr <= rva < vaddr + max(rawsize, 1):
                return rawptr + (rva - vaddr)
        return None

    def read_cstr(off: int, limit: int = 256) -> str:
        end = data.find(b"\x00", off, off + limit)
        if end == -1:
            end = off + limit
        return data[off:end].decode("latin-1", "replace")

    # Data directory index 1 == import table, index 0 == export table.
    imp_rva, imp_size = struct.unpack_from("<II", data, dd_off + 1 * 8)
    exp_rva, exp_size = struct.unpack_from("<II", data, dd_off + 0 * 8)

    if imp_rva and imp_size:
        off = rva_to_off(imp_rva)
        while off is not None:
            ilt, _ts, _fc, name_rva, iat = struct.unpack_from("<IIIII", data, off)
            if name_rva == 0 and ilt == 0 and iat == 0:
                break
            dll = _normalise_dll(read_cstr(rva_to_off(name_rva) or off))
            out.imports.append(dll)
            # Walk the import name table for the first few symbols per DLL.
            thunk = ilt or iat
            toff = rva_to_off(thunk)
            count = 0
            while toff is not None and count < 64:
                entry = struct.unpack_from("<Q" if is_pe32_plus else "<I", data, toff)[0]
                if entry == 0:
                    break
                if not (entry >> (63 if is_pe32_plus else 31)):
                    hnoff = rva_to_off(entry & 0x7FFFFFFF)
                    if hnoff is not None:
                        out.imported_symbols.append(read_cstr(hnoff + 2))
                toff += 8 if is_pe32_plus else 4
                count += 1
            off += 20

    if exp_rva and exp_size:
        off = rva_to_off(exp_rva)
        if off is not None:
            _flags, _ts, _maj, _min, name_rva, base, nfunc, nname = struct.unpack_from(
                "<IIHHIIII", data, off
            )
            name_off = rva_to_off(name_rva)
            if name_off is not None:
                out.exports.append(read_cstr(name_off))
            # AddressOfNames is an array of RVAs to symbol strings (offset 32).
            names_rva = struct.unpack_from("<I", data, off + 32)[0]
            noff = rva_to_off(names_rva)
            if noff is not None:
                for i in range(min(nname, 256)):
                    nrva = struct.unpack_from("<I", data, noff + i * 4)[0]
                    so = rva_to_off(nrva)
                    if so is not None:
                        out.exports.append(read_cstr(so))
    out.imports = sorted(set(out.imports))
    out.exports = sorted(set(out.exports))
    return out


def parse_elf(data: bytes) -> ParsedBinary:
    out = ParsedBinary(kind="elf")
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    ei_class = data[4]
    ei_data = data[5]
    endian = "<" if ei_data == 1 else ">"
    is64 = ei_class == 2
    out.bitness = 64 if is64 else 32
    if is64:
        machine = struct.unpack_from(endian + "H", data, 0x12)[0]
    else:
        machine = struct.unpack_from(endian + "H", data, 0x12)[0]
    out.machine = machine
    out.arch = ELF_MACHINES.get(machine, ("unknown", out.bitness))[0]

    if is64:
        e_shoff = struct.unpack_from(endian + "Q", data, 0x28)[0]
        e_shentsize = struct.unpack_from(endian + "H", data, 0x3A)[0]
        e_shnum = struct.unpack_from(endian + "H", data, 0x3C)[0]
    else:
        e_shoff = struct.unpack_from(endian + "I", data, 0x20)[0]
        e_shentsize = struct.unpack_from(endian + "H", data, 0x2E)[0]
        e_shnum = struct.unpack_from(endian + "H", data, 0x30)[0]

    sections: dict[str, tuple[int, int]] = {}
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        if is64:
            name, _type, _flags, _addr, off, size = struct.unpack_from(
                endian + "IIQQQQ", data, base
            )
        else:
            name, _type, _flags, _addr, off, size = struct.unpack_from(
                endian + "IIIIII", data, base
            )
        sections[str(name)] = (off, size)

    # Recover section-name strings from .shstrtab.
    shstr_off = shstr_tab = None
    if is64:
        shstr_index = struct.unpack_from(endian + "H", data, 0x3E)[0]
    else:
        shstr_index = struct.unpack_from(endian + "H", data, 0x32)[0]
    base = e_shoff + shstr_index * e_shentsize
    if is64:
        _n, _t, _f, _a, shstr_off, _s = struct.unpack_from(endian + "IIQQQQ", data, base)
    else:
        _n, _t, _f, _a, shstr_off, _s = struct.unpack_from(endian + "IIIIII", data, base)

    def sec_name(off: int) -> str:
        if shstr_off is None:
            return ""
        end = data.find(b"\x00", shstr_off + off)
        return data[shstr_off + off : end].decode("latin-1", "replace")

    named: dict[str, tuple[int, int]] = {}
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        if is64:
            name, _t, _f, _a, off, size = struct.unpack_from(endian + "IIQQQQ", data, base)
        else:
            name, _t, _f, _a, off, size = struct.unpack_from(endian + "IIIIII", data, base)
        nm = sec_name(name)
        if nm:
            out.sections.append(nm)
            named[nm] = (off, size)

    # .dynstr + .dynamic give needed libraries; .dynsym gives imported symbols.
    if ".dynamic" in named and ".dynstr" in named:
        dyn_off, dyn_size = named[".dynamic"]
        str_off, _ = named[".dynstr"]
        entry_sz = 16 if is64 else 8
        for i in range(dyn_size // entry_sz):
            if is64:
                tag, val = struct.unpack_from(endian + "QQ", data, dyn_off + i * entry_sz)
            else:
                tag, val = struct.unpack_from(endian + "II", data, dyn_off + i * entry_sz)
            if tag == 0:
                break
            if tag == 1:  # DT_NEEDED
                end = data.find(b"\x00", str_off + val)
                out.imports.append(data[str_off + val : end].decode("latin-1", "replace"))
    out.imports = sorted(set(out.imports))
    return out


def parse_file(path: str | Path) -> ParsedBinary:
    """Parse a PE or ELF file, returning an empty result for anything else."""
    p = Path(path)
    data = p.read_bytes()
    if data[:2] == b"MZ":
        return parse_pe(data)
    if data[:4] == b"\x7fELF":
        return parse_elf(data)
    raise ValueError(f"unrecognised binary format: {p}")
