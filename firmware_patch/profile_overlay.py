"""Run the overlay with the REAL firmware routines (no stubs) in the emulator: instructions per frame, peak stack, faults.
usage: python profile_overlay.py PATCHED.bin"""
import json, os, struct, sys
from unicorn import *
from unicorn.arm_const import *
img = open(sys.argv[1], "rb").read()
SYM = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch.syms.json")))
PKT, IDLE, PAGE, S0, TICKS, SENT = 0x20001350, 0x200002A4, 0x2000022D, 0x20002E00, 0x20002E90, 0x3F800

mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
mu.mem_map(0, 0x40000); mu.mem_write(0, img); mu.mem_map(0x20000000, 0x10000)
mu.mem_map(0x40000000, 0x100000); mu.mem_map(0xE0000000, 0x100000)
mu.mem_write(IDLE, b"\1"); mu.mem_write(PAGE, b"\1"); mu.mem_write(0x20000222, bytes([0x28, 0x34]))
spi_bytes = [0]
mu.hook_add(UC_HOOK_MEM_WRITE, lambda uc, acc, a, s, v, _: spi_bytes.__setitem__(0, spi_bytes[0] + 1), begin=0x4001C01C, end=0x4001C01F)

def run(entry, args=()):
    st = {"n": 0, "minsp": 0x20002C00}
    def code(uc, a, s, _):
        st["n"] += 1; st["minsp"] = min(st["minsp"], uc.reg_read(UC_ARM_REG_SP))
    h = mu.hook_add(UC_HOOK_CODE, code); spi_bytes[0] = 0
    mu.reg_write(UC_ARM_REG_SP, 0x20002C00 - 16); mu.reg_write(UC_ARM_REG_LR, SENT | 1)
    for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args): mu.reg_write(r, v)
    err = None
    try: mu.emu_start(entry | 1, SENT, count=30_000_000)
    except UcError as e: err = str(e)
    mu.hook_del(h); return st["n"], 0x20002C00 - st["minsp"], err

def cmd(*b):
    mu.mem_write(PKT, bytes(list(b) + [0] * (64 - len(b)))); return run(SYM["cmd_hook"], (b[1], 0))

title = b"Stavros Markonis - Well Rain Together | Risk of Rain 2"
cmd(4, 0x95, 0, len(title), *title); cmd(4, 0x97, len(title)); cmd(4, 0x94, 1, 5, 100, 40)
print("faults while setting up:", [e for e in (cmd(4, 0x93)[2],) if e] or "none")
worst = 0
for k in range(0, 40):
    mu.mem_write(TICKS, struct.pack("<I", 1000 + 40 * k))
    n, depth, err = run(SYM["ui_hook"])
    if spi_bytes[0]:
        worst = max(worst, n)
        if k < 3: print(f"frame {k}: instructions={n} spi_bytes={spi_bytes[0]} stack={depth}B fault={err}")
    assert not err, err
print(f"worst frame: {worst} instructions = ~{worst*1.4/48000:.1f} ms of CPU @48 MHz (1.4 cyc/insn), plus the SPI wait for 7424 bytes")
for name, mhz in (("12 MHz", 12), ("24 MHz", 24)):
    print(f"  SPI at {name}: +{7424*8/mhz/1000:.1f} ms  -> ~{worst*1.4/48000 + 7424*8/mhz/1000:.1f} ms per frame, {30*(worst*1.4/48000 + 7424*8/mhz/1000)/10:.0f}% CPU at 30 fps")
n, depth, err = run(SYM["sec_hook"], (165, 45, 7, 2)); print("1 Hz part:", n, "instructions, fault:", err)
