"""Dump the keyboard's live flash (needs patched firmware v3) so your settings survive the next flash.
  python host/aula_backup.py [OUT.bin] [START END]     default: private/live_flash.bin, 0x8000..0x1A000 (~12 s)
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
ok, raw = t.xfer(h, s.pkt(4, 0x93), delay=0.002)
if raw[9:12] != b"PAT" or raw[12] < 3:
    sys.exit("needs the v3 patched firmware (probe: %r)" % raw[9:13])
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
buf = bytearray()
try:
    for a in range(start, end, CHUNK):
        for attempt in range(4):
            ok, raw = t.xfer(h, s.pkt(4, 0x96, a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF), delay=0.001)
            if ok: break
        else:
            sys.exit("read failed at %#x" % a)
        buf += raw[9:9 + CHUNK]
        if (a - start) // CHUNK % 200 == 0:
            print("\r%#x / %#x" % (a, end), end="", flush=True)
finally:
    t.k32.CloseHandle(h)
buf = buf[:end - start]
open(out, "wb").write(bytes(buf))
print("\nwrote", out, len(buf), "bytes (flash %#x..%#x)" % (start, end))
