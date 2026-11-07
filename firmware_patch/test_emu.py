"""Unicorn tests for patched.bin. Runs real firmware routines against fake MMIO.
usage: python test_emu.py STOCK.bin PATCHED.bin"""
import struct, sys
from unicorn import *
from unicorn.arm_const import *

STOCK, PATCHED = (open(p, "rb").read() for p in sys.argv[1:3])
SENT = 0x3F800            # return sentinel (empty flash)
PKT, IDLE, STATE = 0x20001350, 0x200002A4, 0x20002E00
import json, os; _SYM = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch.syms.json"))); CMD_HOOK, BLIT_HOOK, SEC_HOOK, UI_HOOK, TICK_HOOK = (_SYM[k] for k in ("cmd_hook", "blit_hook", "sec_hook", "ui_hook", "tick_hook"))

def run(img, entry, args=(), pkt=b"", ram=None, stack_args=(), watch=(), limit=3_000_000):
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    mu.mem_map(0, 0x40000); mu.mem_write(0, img)
    mu.mem_map(0x20000000, 0x10000); mu.mem_map(0x40000000, 0x100000); mu.mem_map(0xE0000000, 0x100000)
    for a, v in (ram or {}).items(): mu.mem_write(a, bytes(v))
    mu.mem_write(PKT, bytes(pkt))
    log = {"spi": [], "dc": 1, "stream": [], "watch": []}
    def on_w(uc, acc, addr, size, val, _):
        if addr == 0x4001C01C:
            log["stream"].append((log["dc"], val & 0xFF))
        elif addr == 0x4004A028 and val & 0x4000: log["dc"] = 0     # DC low  = command
        elif addr == 0x4004A024 and val & 0x4000: log["dc"] = 1     # DC high = data
    mu.hook_add(UC_HOOK_MEM_WRITE, on_w, begin=0x40000000, end=0x4FFFFFFF)
    for w in watch:
        def on_c(uc, addr, size, _, w=w):
            log["watch"].append((hex(addr), [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)],
                                 struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4))[0]))
        mu.hook_add(UC_HOOK_CODE, on_c, begin=w, end=w)
    sp = 0x20002C00
    if stack_args:
        sp -= 16; mu.mem_write(sp, struct.pack("<4I", *(list(stack_args) + [0] * 4)[:4]))
    mu.reg_write(UC_ARM_REG_SP, sp); mu.reg_write(UC_ARM_REG_LR, SENT | 1)
    for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args): mu.reg_write(r, v)
    try:
        mu.emu_start(entry | 1, SENT, count=limit)
        done = True
    except UcError as e:
        done = False; log["err"] = str(e)
    log["r0"] = mu.reg_read(UC_ARM_REG_R0); log["done"] = done
    log["mu"] = mu
    return log

def tft(stream):
    """split the fake-SPI stream into [(cmd, [data...])]"""
    out = []
    for dc, b in stream:
        if dc == 0: out.append((b, []))
        elif out: out[-1][1].append(b)
    return out

ram0 = {0x20000222: [0x28, 0x34], IDLE: [1]}
ok = True
def check(name, cond, info=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, info)

# --- 0x93 probe
L = run(PATCHED, CMD_HOOK, (0x93, 0), pkt=bytes([4, 0x93] + [0] * 62), ram=ram0)
p = bytes(L["mu"].mem_read(PKT, 16))
check("probe ack + signature", L["r0"] == 1 and p[8:12] == b"PAT\x03", p[8:12])

# --- 0x90 fillrect (10,20)-(19,24) colour 0xF800
L = run(PATCHED, CMD_HOOK, (0x90, 0), pkt=bytes([4, 0x90, 10, 20, 19, 24, 0xF8, 0x00] + [0] * 56), ram=ram0)
t = tft(L["stream"]); cmds = [c for c, _ in t]
caset = dict(t)[0x2A]; raset = dict(t)[0x2B]; px = [b for c, d in t if c == 0x2C for b in d]
check("fillrect returns 1", L["r0"] == 1 and L["done"], L.get("err", ""))
check("fillrect CASET/RASET incl. panel offset", caset == [0, 0x28 + 10, 0, 0x28 + 19] and raset == [0, 0x34 + 20, 0, 0x34 + 24], (caset, raset))
check("fillrect pixel count/colour", len(px) == 10 * 5 * 2 and px[:4] == [0xF8, 0, 0xF8, 0] and set(px) == {0xF8, 0}, len(px))

# --- 0x91 pixel run: 3 pixels at (100,50)
pk = bytes([4, 0x91, 100, 50, 3, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC] + [0] * 53)
L = run(PATCHED, CMD_HOOK, (0x91, 0), pkt=pk, ram=ram0); t = tft(L["stream"])
px = [b for c, d in t if c == 0x2C for b in d]; caset = dict(t)[0x2A]
check("pixel run bytes in order", L["r0"] == 1 and px == [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC] and caset == [0, 0x28 + 100, 0, 0x28 + 102], (px, caset))

# --- rejects bad input without touching the TFT
for name, pk in (("x1 off-screen", [4, 0x90, 0, 0, 240, 5, 0, 0]), ("y1 off-screen", [4, 0x90, 0, 0, 5, 135, 0, 0]),
                 ("x0>x1", [4, 0x90, 9, 0, 5, 5, 0, 0]), ("run too long", [4, 0x91, 0, 0, 29]), ("run past edge", [4, 0x91, 230, 0, 20]),
                 ("bad mode", [4, 0x92, 3, 0])):
    L = run(PATCHED, CMD_HOOK, (pk[1], 0), pkt=bytes(pk + [0] * (64 - len(pk))), ram=ram0)
    check("reject " + name, L["r0"] == 0 and not L["stream"])
# blit busy -> refuse after timeout instead of corrupting a DMA transfer
L = run(PATCHED, CMD_HOOK, (0x90, 0), pkt=bytes([4, 0x90, 0, 0, 5, 5, 0, 0] + [0] * 56), ram={**ram0, IDLE: [0]}, limit=3_000_000)
check("busy DMA => nack, no SPI writes", L["done"] and L["r0"] == 0 and not L["stream"])

# --- unknown/stock commands fall through to the ORIGINAL dispatcher, identical effects
bad = []
for cmd in (0x05, 0x10, 0x12, 0x14, 0x16, 0x17, 0x18, 0x19, 0x20, 0x22, 0x26, 0x28, 0x38, 0x72, 0xAB, 0xE0, 0x55, 0x8F, 0x9A):
    pk = bytes([4, cmd] + [0] * 62)
    a = run(STOCK, 0x3FC0, (cmd, 0), pkt=pk, ram=ram0, limit=200000)
    b = run(PATCHED, CMD_HOOK, (cmd, 0), pkt=pk, ram=ram0, limit=200000)
    ra, rb = bytes(a["mu"].mem_read(0x20000000, 0x2800)), bytes(b["mu"].mem_read(0x20000000, 0x2800))  # below the stack
    if a["r0"] != b["r0"] or ra != rb or a["stream"] != b["stream"] or a["done"] != b["done"]:
        bad.append((hex(cmd), a["r0"], b["r0"], a["done"], b["done"], [hex(0x20000000 + i) for i in range(len(ra)) if ra[i] != rb[i]][:4]))
check("19 stock commands behave identically via hook", not bad, bad)

# --- blit hook modes, observing what reaches the ORIGINAL blit (0x7b82)
def blit(mode_ram, args):
    return run(PATCHED, BLIT_HOOK, args[:4], ram={**ram0, **mode_ram}, stack_args=(args[4],), watch=(0x7B82,), limit=50)
magic = list(struct.pack("<I", 0x5AC0FFEE))
r = blit({}, (0, 0, 240, 135, 0x330100))
check("no magic => pass-through", r["watch"] and r["watch"][0][1] == [0, 0, 240, 135] and r["watch"][0][2] == 0x330100, r["watch"][:1])
r = blit({STATE: magic + [1, 0]}, (0, 0, 240, 135, 0x330100))
check("mode 1 => blit dropped", not r["watch"])
r = blit({STATE: magic + [2, 128]}, (0, 0, 240, 135, 0x330100))
check("mode 2 => full frame clipped to 128 rows", r["watch"] and r["watch"][0][1] == [0, 0, 240, 128] and r["watch"][0][2] == 0x330100, r["watch"][:1])
r = blit({STATE: magic + [2, 128]}, (8, 100, 29, 28, 0x2199C))
check("mode 2 => icons untouched", r["watch"] and r["watch"][0][1] == [8, 100, 29, 28] and r["watch"][0][2] == 0x2199C)
# end-to-end: 0x92 command really sets the state that blit_hook reads
L = run(PATCHED, CMD_HOOK, (0x92, 0), pkt=bytes([4, 0x92, 2, 120] + [0] * 60), ram=ram0)
st = bytes(L["mu"].mem_read(STATE, 8))
check("0x92 stores magic/mode/clip", L["r0"] == 1 and st[:4] == bytes(magic) and st[4:6] == bytes([2, 120]), st.hex())

# --- every blit call site goes through the hook; nothing else changed
import re
from capstone import *
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB); n_hook = n_orig = 0
for a in range(0x100, 0x19F2C, 2):
    for i in md.disasm(PATCHED[a:a + 4], a, 1):
        if i.mnemonic == "bl":
            t = int(i.op_str[1:], 0); n_hook += t == BLIT_HOOK; n_orig += t == 0x7B82
check("72 blit sites retargeted, 0 left on original", n_hook >= 72 and n_orig == 0, (n_hook, n_orig))

# ================= v3: music overlay, smooth scroll, flash read, tick/ui hooks =================
PAGE_ADDR, S0, TICKS_ADDR = 0x2000022D, STATE, 0x20002E90
FW = {"fill": 0x7B20, "num": 0x12A9E, "blit": 0x7B82, "win": 0x7A92, "ui": 0xF156, "tick": 0x93C}
RAIL, FILLC = 0x2104, 0x07E0
FONT = PATCHED[0x15937:0x15937 + 95 * 16]


def mk(ram=None):
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    mu.mem_map(0, 0x40000); mu.mem_write(0, PATCHED)
    mu.mem_map(0x20000000, 0x10000); mu.mem_map(0x40000000, 0x100000); mu.mem_map(0xE0000000, 0x100000)
    for a, v in (ram or {}).items(): mu.mem_write(a, bytes(v))
    mu.rec, mu.spi, mu.cs = [], [], []

    def on_w(uc, acc, addr, size, val, _):
        if addr == 0x4001C01C: mu.spi.append(val & 0xFF)
        elif addr in (0x40046028, 0x40046024): mu.cs.append("lo" if addr == 0x40046028 else "hi")
    mu.hook_add(UC_HOOK_MEM_WRITE, on_w, begin=0x40000000, end=0x4FFFFFFF)

    def mkstub(name):
        def h(uc, addr, size, _):
            sp = uc.reg_read(UC_ARM_REG_SP); stk = struct.unpack("<3I", uc.mem_read(sp, 12))
            r = tuple(uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
            mu.rec.append((name, r + stk))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))   # stubbed: return immediately
        return h
    for n, a in FW.items(): mu.hook_add(UC_HOOK_CODE, mkstub(n), begin=a, end=a)
    return mu


def call(mu, entry, args=(), stack=(), limit=3_000_000):
    mu.rec.clear(); mu.spi.clear(); mu.cs.clear()
    sp = 0x20002C00 - 16; mu.mem_write(sp, struct.pack("<4I", *(list(stack) + [0] * 4)[:4]))
    mu.reg_write(UC_ARM_REG_SP, sp); mu.reg_write(UC_ARM_REG_LR, SENT | 1)
    for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args): mu.reg_write(r, v)
    mu.emu_start(entry | 1, SENT, count=limit)
    return mu.reg_read(UC_ARM_REG_R0)


def cmd(mu, *b):
    mu.mem_write(PKT, bytes(list(b) + [0] * (64 - len(b))))
    return call(mu, CMD_HOOK, (b[1], 0))


def tick(mu): call(mu, SEC_HOOK, (165, 45, 7, 2), (0xFFFF, 0, 32)); return [r for r in mu.rec if r[0] != "num"]
def ui(mu): call(mu, UI_HOOK); return mu.rec
def set_ticks(mu, v): mu.mem_write(TICKS_ADDR, struct.pack("<I", v))
def u32(mu, off): return struct.unpack("<I", bytes(mu.mem_read(S0 + off, 4)))[0]
S_FIELDS = dict(magic=0, mode=4, clip=5, act=6, ttl=7, px=8, shown=9, dtext=10, dbar=11, age=12, speed=13, len=14, cal=15)
def f(mu, name): return bytes(mu.mem_read(S0 + S_FIELDS[name], 1))[0]
def pos_q8(mu): return u32(mu, 16)
def tps(mu): return u32(mu, 24)


def reference(text, pos, scroll):
    """independent model: pixel column -> glyph bit; white (0xFF) on black, 2 bytes per pixel, 16 rows of 232 px"""
    n, period, out = len(text), (len(text) + 4) * 8, bytearray()
    for row in range(16):
        for x in range(232):
            col = (pos + x) % period if scroll else pos + x
            c = col >> 3
            g = FONT[(ord(text[c]) - 0x20) * 16 + row] if c < n else 0
            v = 0xFF if (g >> (col & 7)) & 1 else 0
            out += bytes([v, v])
    return bytes(out)


def start(title, speed=40, px=0, ttl=5):
    mu = mk({IDLE: [1], PAGE_ADDR: [1]})
    b = title.encode()
    for off in range(0, len(b), 56):
        assert cmd(mu, 4, 0x95, off, len(b[off:off + 56]), *b[off:off + 56]) == 1
    assert cmd(mu, 4, 0x97, len(b)) == 1 and cmd(mu, 4, 0x94, 1, ttl, px, speed) == 1
    return mu


# --- commands
mu = mk({IDLE: [1]})
check("probe v3", cmd(mu, 4, 0x93) == 1 and bytes(mu.mem_read(PKT + 8, 4)) == b"PAT\x03")
cmd(mu, 4, 0x96, 0x00, 0x3F, 0xC0)
check("0x96 flash read returns the image bytes", bytes(mu.mem_read(PKT + 8, 56)) == PATCHED[0x3FC0:0x3FC0 + 56])
check("0x96 refuses to read past flash", cmd(mu, 4, 0x96, 0x03, 0xFF, 0xD0) == 0 and cmd(mu, 4, 0x96, 0x04, 0, 0) == 0)
check("0x95 chunk bounds", cmd(mu, 4, 0x95, 0, 57) == 0 and cmd(mu, 4, 0x95, 90, 10) == 0 and cmd(mu, 4, 0x97, 96) == 0)
cmd(mu, 4, 0x95, 0, 4, 0x41, 0xE9, 0x00, 0x7F)
check("title chars sanitised to the font range", bytes(mu.mem_read(S0 + 40, 4)) == b"A???", bytes(mu.mem_read(S0 + 40, 4)))

# --- hooks pass through
mu = mk({IDLE: [1], PAGE_ADDR: [1]}); set_ticks(mu, 41)
call(mu, TICK_HOOK); check("tick_hook counts and calls the stock SysTick work", u32(mu, 0x90) == 42 and [r[0] for r in mu.rec] == ["tick"], mu.rec)
check("ui_hook runs the stock renderer first", [r[0] for r in ui(mu)] == ["ui"])
tick(mu); check("sec_hook keeps the stock seconds draw", [r[0] for r in mu.rec] == ["num"] and mu.rec[0][1][:4] == (165, 45, 7, 2), mu.rec)

# --- static short title: one frame, then nothing until dirty
mu = start("Hi"); set_ticks(mu, 1000); r = ui(mu)
frame = reference("Hi", 0, False)
check("static title: window + exact pixels", [x[0] for x in r] == ["ui", "win"] and r[1][1][:4] == (4, 81, 235, 96) and bytes(mu.spi) == frame, (r[1:], len(mu.spi)))
check("static title: CS held low once across the frame", mu.cs == ["lo", "hi"], mu.cs)
set_ticks(mu, 2000); check("static title: not redrawn while nothing changed", [x[0] for x in ui(mu)] == ["ui"])
call(mu, BLIT_HOOK, (0, 0, 240, 135), (0,)); check("clock background redraw marks text+bar dirty", f(mu, "dtext") == 1 and f(mu, "dbar") == 1)
set_ticks(mu, 2001); ui(mu); check("...and the next frame repaints it", bytes(mu.spi) == frame)

# --- marquee: smooth, paced, wraps with a gap
title = "Stavros Markonis - Well Rain Together | Risk of Rain 2"
mu = start(title, speed=40); n = len(title)
set_ticks(mu, 1000); ui(mu); p0 = pos_q8(mu) >> 8
check("marquee first frame matches reference", bytes(mu.spi) == reference(title, p0, True), p0)
set_ticks(mu, 1010); r = ui(mu)
check("frame pacing: <1/30 s later => no frame", [x[0] for x in r] == ["ui"], r)
last, ok_ref, ok_step = p0, True, True
for k in range(1, 60):
    set_ticks(mu, 1000 + 40 * k); ui(mu); p = pos_q8(mu) >> 8
    if mu.spi:
        ok_ref &= bytes(mu.spi) == reference(title, p, True)
        ok_step &= ((p - last) % ((n + 4) * 8)) in (1, 2)       # 40 px/s at 25 fps -> 1-2 px per frame
        last = p
check("60 consecutive frames match the reference and move 1-2 px (smooth)", ok_ref and ok_step, (ok_ref, ok_step))
mu.mem_write(S0 + 16, struct.pack("<I", ((n + 4) * 8 * 256) - 100))      # just before the wrap
set_ticks(mu, 100000); ui(mu); p = pos_q8(mu) >> 8
check("scroll wraps around the title + gap", p < 8 and bytes(mu.spi) == reference(title, p, True), p)
tp = tps(mu); sp = pos_q8(mu)
for k in range(1, 120):                                                  # a 4 s stall must not teleport the text
    pass
set_ticks(mu, 100000 + 4 * tp * 5); before = pos_q8(mu); ui(mu); jump = (pos_q8(mu) - before) % ((n + 4) * 8 * 256)
check("a long stall advances at most 1/8 s worth", jump <= (tp >> 3) * (40 * 256 // tp + 1), jump)

# --- never blocks, never draws when it shouldn't
def quiet(name, setup):
    m = start(title); setup(m); set_ticks(m, 5000); r = ui(m)
    check("no frame: " + name, [x[0] for x in r] == ["ui"] and not m.spi, r)
quiet("DMA blit in flight", lambda m: m.mem_write(IDLE, b"\0"))
quiet("other UI page", lambda m: m.mem_write(PAGE_ADDR, b"\2"))
quiet("host owns screen (mode 1)", lambda m: cmd(m, 4, 0x92, 1, 0))
quiet("ttl expired", lambda m: cmd(m, 4, 0x94, 1, 0, 0, 0))
quiet("hidden", lambda m: cmd(m, 4, 0x94, 0, 0, 0, 0))
m = mk({IDLE: [1], PAGE_ADDR: [1], S0: [1, 2, 3, 4, 1, 0, 1, 5, 99, 0, 1, 0]}); set_ticks(m, 5000)
check("garbage SRAM (no magic) => inert", [x[0] for x in ui(m)] == ["ui"] and not m.spi and tick(m) == [])

# --- 1 Hz part: tick-rate calibration, progress bar, expiry
mu = start("Hi", px=100)
for k in range(5): set_ticks(mu, 777 + 2000 * k); tick(mu)
check("calibrates ticks/second from the seconds hook (here 2000)", 1900 <= tps(mu) <= 2100 and f(mu, "cal") == 3, tps(mu))
mu = start("Hi", px=100); t = tick(mu)
check("bar: rail then fill 0..99 on rows 129..132", len(t) == 2 and t[0][1][:4] == (RAIL, 0, 129, 239) and t[1][1][:4] == (FILLC, 0, 129, 99) and t[1][1][4] == 132, t)
cmd(mu, 4, 0x94, 1, 5, 150, 0); t = tick(mu)
check("bar grows => only the delta", len(t) == 1 and t[0][1][:4] == (FILLC, 100, 129, 149), t)
cmd(mu, 4, 0x94, 1, 5, 120, 0); t = tick(mu)
check("bar shrinks => rail over the removed part", len(t) == 1 and t[0][1][:4] == (RAIL, 120, 129, 149), t)
cmd(mu, 4, 0x94, 1, 5, 120, 0); check("bar unchanged => nothing drawn", tick(mu) == [])
mu.mem_write(PAGE_ADDR, b"\2"); cmd(mu, 4, 0x94, 1, 5, 200, 0); ttl0 = f(mu, "ttl")
check("other page: bar untouched, ttl not consumed", tick(mu) == [] and f(mu, "ttl") == ttl0)
mu.mem_write(PAGE_ADDR, b"\1")
cmd(mu, 4, 0x94, 1, 2, 50, 0); tick(mu); tick(mu); t = tick(mu)
check("ttl expiry erases title band and bar", len(t) == 2 and t[0][1][:5] == (0, 0, 80, 239, 98) and t[1][1][:5] == (0, 0, 129, 239, 132), t)
check("...then stays quiet", f(mu, "act") == 0 and tick(mu) == [])
cmd(mu, 4, 0x94, 1, 9, 10, 0); tick(mu); cmd(mu, 4, 0x94, 0, 0, 0, 0)
check("explicit hide erases", len(tick(mu)) == 2)
cmd(mu, 4, 0x94, 1, 9, 10, 0); mu.mem_write(IDLE, b"\0"); check("DMA busy => 1 Hz part skipped (bounded)", tick(mu) == [])

print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
