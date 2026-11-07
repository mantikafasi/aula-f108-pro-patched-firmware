"""Print the music-overlay diagnostics of the patched firmware (v4+): `04 98`.
  python host/aula_diag.py [seconds_between_samples]     takes two samples and prints the deltas
Shows whether the SysTick counter advances, how often the UI hook runs, how many title frames were drawn, and why
frames were skipped. Don't run it while aula_music.py is mid-update unless you accept a stray NACK there.
"""
import sys, time

import aula_screen as s
import aula_timesync as t

WHY = ["drawn", "inactive", "hidden", "other_page", "host_mode", "dma_busy", "paced", "same_pos"]


def sample(h):
    out = {}
    for part in (0, 1):
        ok, raw = t.xfer(h, s.pkt(4, 0x98, part), delay=0.002)
        if not ok:
            sys.exit("NACK: firmware older than v4, or the overlay was never used since boot")
        out[part] = [int.from_bytes(raw[9 + 4 * i:13 + 4 * i], "little") for i in range(14)]
    w, c = out[0], out[1]
    return dict(ticks=w[0], tps=w[1], step_q8=w[2], pos_px=w[3] >> 8, last_tick=w[4], ui_calls=w[6], frames=w[7],
                sec_calls=w[8], active=w[9] & 255, ttl=w[9] >> 8 & 255, title_len=w[9] >> 16 & 255, cal=w[9] >> 24,
                page=w[10] & 255, idle=w[10] >> 8 & 255, mode=w[10] >> 16 & 255, speed=w[10] >> 24,
                dirty_text=w[11] & 255, dirty_bar=w[11] >> 8 & 255, px=w[11] >> 16 & 255, shown=w[11] >> 24,
                why={n: c[i] for i, n in enumerate(WHY)})


gap = float(sys.argv[1]) if len(sys.argv) > 1 else 3
h = t.open_dev(t.find())
a = sample(h); time.sleep(gap); b = sample(h)
t.k32.CloseHandle(h)
print("state now:", {k: v for k, v in b.items() if k not in ("why",)})
for k in ("ticks", "ui_calls", "frames", "sec_calls"):
    print(f"  {k:10s} +{b[k] - a[k]:7d} in {gap:.0f}s  ({(b[k] - a[k]) / gap:8.1f}/s)")
print("  skip reasons (+):", {n: b["why"][n] - a["why"][n] for n in WHY})
