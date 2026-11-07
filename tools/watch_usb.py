"""Log every time the keyboard's USB collections appear/disappear (a reset shows up as a gap).
usage: python watch_usb.py [seconds]"""
import sys, time, hid
dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60
t0 = time.time(); last = None; events = 0
while time.time() - t0 < dur:
    n = len(hid.enumerate(0x0C45, 0x800A))
    if n != last:
        print(f"{time.time()-t0:7.1f}s  collections={n}", flush=True); events += last is not None; last = n
    time.sleep(0.2)
print(f"done: {events} change(s) in {dur:.0f}s")
