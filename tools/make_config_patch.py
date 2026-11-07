"""Turn a live-flash dump into a "config patch": the bytes your settings changed relative to the image you flashed.
usage: python tools/make_config_patch.py FLASHED_IMAGE.bin LIVE_FLASH.bin OUT.json [DUMP_START=0x8000]
FLASHED_IMAGE is the exact patched image that was flashed (build_all keeps copies in private/flashed/).
The patch is applied to a new image with: firmware_patch/build.py STOCK --config OUT.json
"""
import json, sys

flashed = open(sys.argv[1], "rb").read()
live = open(sys.argv[2], "rb").read()
out = sys.argv[3]
base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x8000
assert len(flashed) == 0x40000, "FLASHED_IMAGE must be a 256 KB image"

runs, i = [], 0
while i < len(live):
    if live[i] != flashed[base + i]:
        j = i
        while j < len(live) and (live[j] != flashed[base + j] or (j + 1 < len(live) and live[j + 1] != flashed[base + j + 1])):
            j += 1
        runs.append((base + i, live[i:j])); i = j
    else:
        i += 1
pages = sorted({a >> 10 for a, d in runs for a in range(a, a + len(d), 1)})
print(f"{sum(len(d) for _, d in runs)} changed bytes in {len(runs)} run(s), 1 KB pages: {[hex(p << 10) for p in pages]}")
if not runs:
    sys.exit("nothing differs: the keyboard holds default settings (or the dump/image mismatch)")
if base + len(live) > 0x1A000:
    print("note: dump reaches into the patch area; build.py refuses config bytes there")
json.dump({"runs": [{"addr": a, "hex": d.hex()} for a, d in runs]}, open(out, "w"), indent=1)
print("wrote", out)
