"""Work with the vendor 'AULA F108PRO Reset' flasher (a Sonix/HFD ISP tool with the firmware embedded as RCDATA 4000).

  python tools/make_flasher.py extract RESET.zip|RESET.exe          -> firmware_patch/stock_res4000.bin
  python tools/make_flasher.py build   RESET.zip|RESET.exe OUT.exe  -> flasher whose embedded image is firmware_patch/patched.bin

The vendor exe is only ever read; the output is a modified copy. Do not publish either exe (vendor binaries).
"""
import hashlib, os, sys, zipfile

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, "firmware_patch", "stock_res4000.bin")
PATCHED = os.path.join(ROOT, "firmware_patch", "patched.bin")
SIZE = 0x40000


def load_exe(path):
    if path.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.lower().endswith(".exe")]
        if len(names) != 1:
            raise SystemExit("expected exactly one .exe in the zip, found %s" % names)
        return z.read(names[0])
    return open(path, "rb").read()


def res4000(raw):
    """-> (file offset, size) of RCDATA 4000, the firmware image the flasher programs."""
    pe = pefile.PE(data=raw); pe.parse_data_directories()
    for t in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if pefile.RESOURCE_TYPE.get(t.struct.Id) == "RT_RCDATA":
            for e in t.directory.entries:
                if e.struct.Id == 4000:
                    d = e.directory.entries[0].data.struct
                    return pe.get_offset_from_rva(d.OffsetToData), d.Size
    raise SystemExit("this exe has no RCDATA 4000 firmware resource")


def main():
    cmd, src = sys.argv[1], sys.argv[2]
    raw = bytearray(load_exe(src))
    off, size = res4000(raw)
    if size != SIZE:
        raise SystemExit("unexpected firmware size %#x" % size)
    stock = bytes(raw[off:off + size])
    print("vendor image sha256", hashlib.sha256(stock).hexdigest()[:16])
    if cmd == "extract":
        open(STOCK, "wb").write(stock); print("wrote", STOCK); return
    if cmd != "build":
        raise SystemExit(__doc__)
    patched = open(PATCHED, "rb").read()
    assert len(patched) == SIZE, "run firmware_patch/build.py first"
    orig = bytes(raw); raw[off:off + size] = patched
    diff = [i for i in range(len(raw)) if raw[i] != orig[i]]
    assert all(off <= i < off + size for i in diff)
    open(sys.argv[3], "wb").write(raw)
    off2, _ = res4000(bytes(raw))
    assert bytes(raw[off2:off2 + size]) == patched
    print("changed bytes:", len(diff), "| embedded image == patched.bin | sha256", hashlib.sha256(raw).hexdigest()[:16])
    keep = os.path.join(ROOT, "private", "flashed"); os.makedirs(keep, exist_ok=True)
    kept = os.path.join(keep, "patched_%s.bin" % hashlib.sha256(patched).hexdigest()[:8]); open(kept, "wb").write(patched)
    print("kept a copy of the image for config backups:", os.path.relpath(kept, ROOT))
    print("wrote", sys.argv[3])


if __name__ == "__main__":
    main()
