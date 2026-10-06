"""Show which torch is installed, without importing it, and which DLLs it cannot load."""

import argparse
import ctypes
import importlib.util
import os
import pathlib
import struct
import sys
import sysconfig


def identity() -> pathlib.Path | None:
    spec = importlib.util.find_spec("torch")
    if spec is None or not spec.origin:
        print("torch is not installed")
        return None
    init = pathlib.Path(spec.origin)
    root = init.parent
    text = init.read_text(encoding="utf-8", errors="replace").splitlines()
    raises = [i for i, line in enumerate(text, 1) if line.strip() == "raise err"]
    version = ""
    for name in ("version.py", "torch_version.py"):
        path = root / name
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("__version__") or line.startswith("version"):
                    version = line.strip()
                    break
    print(f"torch file: {init}")
    print(f"torch version line: {version or 'unknown'}")
    print(f"raise err lines: {raises}")
    lib = root / "lib"
    if lib.exists():
        cpu = lib / "torch_cpu.dll"
        print(f"torch_cpu.dll bytes: {cpu.stat().st_size if cpu.exists() else 'missing'}")
        print("dlls:", ", ".join(sorted(p.name for p in lib.glob('*.dll'))))
    return root


def pe_imports(data: bytes) -> list[str]:
    if data[:2] != b"MZ":
        return []
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        return []
    coff = e_lfanew + 4
    nsec, = struct.unpack_from("<H", data, coff + 2)
    opt_size, = struct.unpack_from("<H", data, coff + 16)
    opt = coff + 20
    magic, = struct.unpack_from("<H", data, opt)
    dd_off = opt + (112 if magic == 0x20B else 96)
    import_rva, = struct.unpack_from("<I", data, dd_off + 8)
    if import_rva == 0:
        return []
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        off = sec_off + i * 40
        vsz, va, rsz, raw = struct.unpack_from("<IIII", data, off + 8)
        sections.append((va, raw, max(vsz, rsz)))

    def rva_to_off(rva: int) -> int | None:
        for va, raw, span in sections:
            if va <= rva < va + span:
                return raw + (rva - va)
        return None

    off = rva_to_off(import_rva)
    if off is None:
        return []
    names = []
    while off + 20 <= len(data):
        _ilt, _tds, _fwd, name_rva, _iat = struct.unpack_from("<IIIII", data, off)
        if name_rva == 0:
            break
        no = rva_to_off(name_rva)
        if no is None:
            break
        names.append(data[no:data.find(b"\x00", no)].decode("ascii", "replace"))
        off += 20
    return names


def find_dll(name: str, lib: pathlib.Path, extra: list[pathlib.Path]) -> str | None:
    lowered = name.lower()
    for folder in [lib, *extra]:
        if not folder.exists():
            continue
        for entry in folder.iterdir():
            if entry.name.lower() == lowered:
                return str(entry)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", action="store_true")
    args = parser.parse_args()
    root = identity()
    if args.identity or root is None:
        return 0
    lib = root / "lib"
    if not lib.exists():
        print("no torch/lib directory")
        return 1
    prefix = pathlib.Path(sys.prefix)
    windir = pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows"))
    extra = [
        windir / "System32",
        prefix,
        prefix / "Library" / "bin",
        prefix / "bin",
        pathlib.Path(sys.executable).parent,
    ]
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if part:
            extra.append(pathlib.Path(part))
    print("missing imports:")
    missing = False
    for dll in sorted(lib.glob("*.dll")):
        try:
            imports = pe_imports(dll.read_bytes())
        except OSError as exc:
            print(f"  cannot read {dll.name}: {exc}")
            continue
        for dep in imports:
            low = dep.lower()
            if low.startswith("api-ms-win-") or low.startswith("ext-ms-"):
                continue
            if find_dll(dep, lib, extra) is None:
                missing = True
                print(f"  {dll.name} needs {dep}")
    if not missing:
        print("  none")
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AddDllDirectory.argtypes = [ctypes.c_wchar_p]
        kernel32.AddDllDirectory.restype = ctypes.c_void_p
        kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.LoadLibraryExW.restype = ctypes.c_void_p
        kernel32.AddDllDirectory(str(lib))
        print("load test:")
        for dll in sorted(lib.glob("*.dll")):
            kernel32.SetLastError(0)
            handle = kernel32.LoadLibraryExW(str(dll), None, 0x00001100)
            if not handle:
                print(f"  FAIL {dll.name} WinError {ctypes.get_last_error()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
