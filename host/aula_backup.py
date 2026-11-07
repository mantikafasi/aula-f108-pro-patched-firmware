"""Dump the keyboard's live flash (needs patched firmware v3) so your settings survive the next flash.
  python host/aula_backup.py [OUT.bin] [START END]     default: private/live_flash.bin, 0x8000..0x1A000 (~30 s)
Then:  python tools/make_config_patch.py <the image you flashed> private/live_flash.bin private/config_patch.json
and rebuild with:  python build_all.py RESET.zip --config private/config_patch.json
"""
import os, sys

import aula_screen as s
import aula_timesync as t

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("private", "live_flash.bin")
start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x8000
end = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x1A000
CHUNK = 56

path = t.find()
if not path:
    sys.exit("keyboard not found")
h = t.open_dev(path)
ver = s.probe(h)
if ver < 3:
    sys.exit("needs the patched firmware v3+ (probe says %d)" % ver)
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
buf = bytearray()
try:
    for a in range(start, end, CHUNK):
        req = s.pkt(4, 0x96, a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF)
        for attempt in range(8):
            # An unprocessed request is echoed back unchanged (zero payload); a processed one has data[3]==1.
            # So: read twice, require both processed and identical. (If the middle address byte is 1 the echo is
            # indistinguishable by that flag, so agreement of two slow reads has to do.)
            ok1, r1 = t.xfer(h, req, delay=0.004, strict=True)
            ok2, r2 = t.xfer(h, req, delay=0.004, strict=True)
            if r1[9:9 + CHUNK] == r2[9:9 + CHUNK] and ((ok1 and ok2) or (a >> 8 & 0xFF) == 1):
                break
        else:
            sys.exit("read failed at %#x (never got a processed, consistent reply)" % a)
        buf += r1[9:9 + CHUNK]
        if (a - start) // CHUNK % 200 == 0:
            print("\r%#x / %#x" % (a, end), end="", flush=True)
finally:
    t.k32.CloseHandle(h)
buf = buf[:end - start]
open(out, "wb").write(bytes(buf))
print("\nwrote", out, len(buf), "bytes (flash %#x..%#x)" % (start, end))
