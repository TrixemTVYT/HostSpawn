"""Arma PBO packer/reader (uncompressed, stdlib only). Used to build mission and server-addon .pbo files from
source releases (e.g. AsYetUntitled Altis Life v5 ships folders, not .pbo files).

Layout: a header entry list, then the file data, then a 0x00 byte and the SHA-1 of everything before it.
Each entry: name (zero-terminated) + 5 little-endian uint32 (method, original size, reserved, timestamp, data size).
The first entry is the "Vers" product entry that carries properties such as prefix=<addon prefix>."""
import hashlib
import struct
from pathlib import Path

VERS = 0x56657273  # "sreV" as stored


def pack(src: Path, out: Path, prefix: str | None = None) -> int:
    """Pack every file under src into out. Returns the number of files."""
    src = Path(src)
    files = sorted(p for p in src.rglob("*") if p.is_file() and p.name not in ("PboPrefix.txt", "$PBOPREFIX$"))
    head = bytearray()
    head += b"\x00" + struct.pack("<5I", VERS, 0, 0, 0, 0)
    if prefix:
        head += b"prefix\x00" + prefix.encode() + b"\x00"
    head += b"\x00"
    data = []
    for f in files:
        name = str(f.relative_to(src)).replace("/", "\\").encode()
        blob = f.read_bytes()
        head += name + b"\x00" + struct.pack("<5I", 0, 0, 0, int(f.stat().st_mtime), len(blob))
        data.append(blob)
    head += b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0)  # end of header
    body = bytes(head) + b"".join(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body + b"\x00" + hashlib.sha1(body).digest())
    return len(files)


def read(path: Path) -> tuple[dict, dict[str, bytes]]:
    """(properties, {name: data}). Checks the SHA-1 footer."""
    raw = Path(path).read_bytes()
    body, footer = raw[:-21], raw[-21:]
    if footer[0] != 0 or hashlib.sha1(body).digest() != footer[1:]:
        raise ValueError("bad PBO checksum")
    i, props, entries = 0, {}, []

    def cstr():
        nonlocal i
        j = body.index(b"\x00", i)
        s = body[i:j].decode()
        i = j + 1
        return s
    while True:
        name = cstr()
        method, _, _, _, size = struct.unpack_from("<5I", body, i)
        i += 20
        if method == VERS and not name:
            while True:
                k = cstr()
                if not k:
                    break
                props[k] = cstr()
            continue
        if not name:
            break
        entries.append((name, size))
    files = {}
    for name, size in entries:
        files[name] = body[i:i + size]
        i += size
    return props, files


def unpack(path: Path, dest: Path) -> int:
    """Extract a .pbo into dest (refuses names that would escape dest). Writes $PBOPREFIX$ if it has a prefix."""
    props, files = read(path)
    dest = Path(dest).resolve()
    for name, data in files.items():
        target = (dest / name.replace("\\", "/")).resolve()
        if not str(target).startswith(str(dest) + "/"):
            raise ValueError(f"unsafe name in PBO: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if props.get("prefix"):
        (dest / "$PBOPREFIX$").write_text(props["prefix"])
    return len(files)


def main(argv: list[str]) -> int:
    """./manager.sh pbo list <file.pbo> | unpack <file.pbo> <folder> | pack <folder> <file.pbo> [prefix]"""
    if len(argv) >= 2 and argv[0] == "list":
        props, files = read(Path(argv[1]))
        print(f"prefix: {props.get('prefix', '-')}  files: {len(files)}")
        for n, d in files.items():
            print(f"{len(d):>10}  {n}")
        return 0
    if len(argv) >= 3 and argv[0] == "unpack":
        print(f"unpacked {unpack(Path(argv[1]), Path(argv[2]))} files")
        return 0
    if len(argv) >= 3 and argv[0] == "pack":
        src = Path(argv[1])
        prefix = argv[3] if len(argv) > 3 else next(((src / f).read_text().strip() for f in ("$PBOPREFIX$", "PboPrefix.txt")
                                                     if (src / f).exists()), None)
        print(f"packed {pack(src, Path(argv[2]), prefix)} files")
        return 0
    print(main.__doc__)
    return 2
