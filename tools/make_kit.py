"""Bundle a private flash kit for a friend (NOT for public release: it contains modified + original vendor binaries).
usage: python tools/make_kit.py RESET.zip PATCHED_FLASHER.exe OUT.zip
"""
import hashlib, os, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_flasher as mf

ROOT = mf.ROOT
reset_src, patched_exe, out = sys.argv[1:4]

README = """\
AULA F108Pro - music / screen firmware flash kit (EXPERIMENTAL, community-made, not from AULA)
==============================================================================================

What you get after flashing
  * a "now playing" overlay (smooth-scrolling title + progress bar) on the keyboard's CLOCK page, fed by the PC
  * new screen commands (fill rectangle, pixel runs, take over screen) for your own scripts
  * everything else is the stock firmware (same keys, lighting, wireless, clock, GIF boot animation)

READ THIS FIRST
  * Only for the AULA F108Pro tri-mode keyboard WITH the 1.14" colour screen, USB id 0C45:800A.
    Check: Device Manager > Human Interface Devices > (a device) > Details > Hardware Ids
    must contain VID_0C45&PID_800A.
  * Flashing replaces the keyboard's firmware. It was tested on ONE unit only. A failed flash can leave the
    keyboard unusable until you re-flash. ROLLBACK_AULA_F108Pro_stock_flasher.exe (the original vendor tool)
    puts the stock firmware back. Keep it.
  * Flashing resets the keyboard's stored settings (Fn-layer keys, lighting, macros). Re-apply them with the
    AULA driver software afterwards (it re-sends your saved profile when it connects).
  * Use the USB cable (not Bluetooth / 2.4G). Do not unplug or type on the keyboard while flashing.

Flash
  1. Close the AULA driver software (check the tray icon too).
  2. Plug the keyboard in with the USB cable. Laptop: plug in the charger.
  3. Double-click AULA_F108Pro_music_screen_flasher.exe. If SmartScreen complains: More info > Run anyway.
  4. Click Start ONCE. After ~15-25 s the keyboard disconnects, reprograms and reconnects. Wait for it.
  5. Close the flasher. Open the AULA driver software to re-apply your keys/lighting, then close it.

Check it worked and use it
  1. Install Python 3.10+ for Windows.   pip install -r requirements-host.txt
  2. python host/aula_screen.py probe     ->  "patched firmware v4"
  3. Play music in anything that shows in the Windows media overlay (Spotify, browser, ...), then:
        python host/aula_music.py
     Look at the clock page. Stop it with Ctrl+C; the overlay disappears ~5 s later.
  To have it start with Windows, also keep the clock right (the keyboard clock drifts), run once:
        python host/aula_daemon.py --install          (remove with --uninstall; --status shows the log)
  The background service pauses itself while the AULA driver software is open. Do not run aula_music.py by hand
  at the same time as the AULA driver software or the service (two programs talking to the keyboard can garble a transfer).

Roll back
  Run ROLLBACK_AULA_F108Pro_stock_flasher.exe exactly like the flasher above. It restores the stock firmware.

Limits
  Wired only. Title text is ASCII (non-ASCII letters are transliterated). Scroll speed: python host/aula_music.py --speed 60.
  Source and details: see the repository README (docs/firmware-notes.md has the reverse-engineering notes).
"""

REQ = """hidapi
pillow
winrt-runtime
winrt-Windows.Media.Control
winrt-Windows.Foundation
winrt-Windows.Foundation.Collections
"""

files = {
    "AULA_F108Pro_music_screen_flasher.exe": open(patched_exe, "rb").read(),
    "ROLLBACK_AULA_F108Pro_stock_flasher.exe": mf.load_exe(reset_src),
    "README_FLASH.txt": README.encode(),
    "requirements-host.txt": REQ.encode(),
}
for n in sorted(os.listdir(os.path.join(ROOT, "host"))):
    if n.endswith(".py"):
        files["host/" + n] = open(os.path.join(ROOT, "host", n), "rb").read()
files["SHA256SUMS.txt"] = "".join(f"{hashlib.sha256(v).hexdigest()}  {k}\n" for k, v in sorted(files.items())).encode()

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for k, v in files.items():
        z.writestr(k, v)
print("wrote", out, os.path.getsize(out), "bytes;", len(files), "files")
