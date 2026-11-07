"""Splice patch.bin into the stock AULA F108Pro image and retarget the hook BLs.
usage: python build.py STOCK.bin [OUT.bin] [--config CONFIG_PATCH.json]      (run build_patch.py first)
--config bakes your saved keyboard settings (made by tools/make_config_patch.py) into the image, so flashing keeps them.

The stock image is the vendor's (SI-2688 1.14" tri-mode, HFD80CP100 V1.08); it is NOT part of this repo.
Get it with:  python tools/make_flasher.py extract "AULA F108PRO Reset.zip"
"""
import hashlib, json, os, struct, subprocess, sys

import toolchain

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
CONFIG = None
if "--config" in args:
    i = args.index("--config"); CONFIG = args[i + 1]; del args[i:i + 2]
STOCK = args[0]
OUT = args[1] if len(args) > 1 else os.path.join(HERE, "patched.bin")
BLIT, DISPATCH = 0x7B82, 0x3FC0               # blit routine (72 call sites), command dispatcher (1 call site)
SECS_BL, DRAW_NUM = 0xF84E, 0x12A9E           # the one bl that paints the clock's seconds digits
UI_BL, UI_FN = 0x826, 0xF156                  # main loop: UI event renderer
TICK_BL, TICK_FN = 0x8CCE, 0x93C              # SysTick handler: per-tick work
PATCH_AT = 0x1A000                            # empty flash in the stock image
KNOWN_STOCK_SHA = "986b96a324aa05bd"          # prefix; other builds are refused below by the call-site checks

patch = open(os.path.join(HERE, "patch.bin"), "rb").read()
nm = subprocess.check_output([toolchain.tool("llvm-nm"), os.path.join(HERE, "patch.elf")], text=True)
SYM = {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
CMD_HOOK, BLIT_HOOK, SEC_HOOK = SYM["cmd_hook"], SYM["blit_hook"], SYM["sec_hook"]
UI_HOOK, TICK_HOOK = SYM["ui_hook"], SYM["tick_hook"]
assert CMD_HOOK == PATCH_AT and all(a % 2 == 0 for a in (CMD_HOOK, BLIT_HOOK, SEC_HOOK, UI_HOOK, TICK_HOOK))
json.dump(SYM, open(os.path.join(HERE, "patch.syms.json"), "w"))

img = bytearray(open(STOCK, "rb").read())
sha = hashlib.sha256(img).hexdigest()
print("stock sha256", sha[:16], "len", len(img), "" if sha.startswith(KNOWN_STOCK_SHA) else "(different build than analysed!)")
assert len(img) == 0x40000
assert img[PATCH_AT:PATCH_AT + len(patch) + 16] == b"\xff" * (len(patch) + 16), "patch area not empty"


def bl_target(img, a):
    h1, h2 = struct.unpack_from("<HH", img, a)
    if (h1 & 0xF800) != 0xF000 or (h2 & 0xD000) != 0xD000:
        return None
    s = (h1 >> 10) & 1
    i1 = (~((h2 >> 13) ^ s)) & 1
    i2 = (~((h2 >> 11) ^ s)) & 1
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
    if s: off -= 1 << 25
    return a + 4 + off


def bl_encode(a, target):
    off = target - (a + 4)
    assert off % 2 == 0 and -(1 << 24) <= off < (1 << 24)
    s = (off >> 24) & 1; i1 = (off >> 23) & 1; i2 = (off >> 22) & 1
    j1 = (~(i1 ^ s)) & 1; j2 = (~(i2 ^ s)) & 1
    return struct.pack("<HH", 0xF000 | s << 10 | (off >> 12) & 0x3FF, 0xD000 | j1 << 13 | j2 << 11 | (off >> 1) & 0x7FF)


for a, t in ((0x41CA, 0x3FC0), (0x1000, 0x1A174), (0x19000, 0x1A000)):      # encoder round-trip
    assert bl_target(bytearray(bl_encode(a, t)), 0) == 4 + (t - a - 4), "bl codec"

sites = {BLIT: [], DISPATCH: []}
for a in range(0x100, 0x19F2C, 2):                # code extent of the stock image
    t = bl_target(img, a)
    if t in sites: sites[t].append(a)
print("blit call sites:", len(sites[BLIT]), " dispatcher call sites:", len(sites[DISPATCH]))
assert len(sites[BLIT]) == 72 and len(sites[DISPATCH]) == 1, "image is not the analysed stock build; refusing"
assert bl_target(img, SECS_BL) == DRAW_NUM, "seconds-digit call site not where analysed; refusing"
assert bl_target(img, UI_BL) == UI_FN and bl_target(img, TICK_BL) == TICK_FN, "main-loop/SysTick call sites not where analysed; refusing"

img[PATCH_AT:PATCH_AT + len(patch)] = patch
for a in sites[BLIT]:     img[a:a + 4] = bl_encode(a, BLIT_HOOK)
for a in sites[DISPATCH]: img[a:a + 4] = bl_encode(a, CMD_HOOK)
for site, hook in ((SECS_BL, SEC_HOOK), (UI_BL, UI_HOOK), (TICK_BL, TICK_HOOK)):
    img[site:site + 4] = bl_encode(site, hook)
    sites[hook] = [site]

cfg = []
if CONFIG:
    for run in json.load(open(CONFIG))["runs"]:
        a, d = run["addr"], bytes.fromhex(run["hex"])
        assert 0x8000 <= a and a + len(d) <= PATCH_AT, "config bytes outside 0x8000..0x1A000: refusing"
        assert not any(a < x + 4 and x < a + len(d) for v in sites.values() for x in v), "config overlaps a hook site: refusing"
        img[a:a + len(d)] = d; cfg.append((a, a + len(d)))
    print("baked config:", sum(hi - lo for lo, hi in cfg), "bytes in", len(cfg), "run(s)")

stock = open(STOCK, "rb").read()
changed = [i for i in range(len(img)) if img[i] != stock[i]]
print("bytes changed:", len(changed), " (patch", len(patch), "+ 76 BLs x<=4)")
assert all(PATCH_AT <= i < PATCH_AT + len(patch) or any(a <= i < a + 4 for v in sites.values() for a in v)
           or any(lo <= i < hi for lo, hi in cfg) for i in changed)
open(OUT, "wb").write(img)
print("wrote", OUT, hashlib.sha256(img).hexdigest()[:16])
