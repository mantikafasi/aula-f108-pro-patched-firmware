# AULA F108Pro: custom screen firmware patch + tools

Reverse-engineering notes and a small firmware patch for the **AULA F108Pro tri-mode keyboard with the 1.14" colour
screen** (USB `0C45:800A`, Sonix SN32F290). It adds:

* a **"now playing" overlay** (smooth-scrolling title + progress bar) on the keyboard's clock page, fed from the PC
* **screen commands** you can script: fill rectangle, draw pixel runs, take over / clip the screen
* a standalone **time-sync** tool that works without the vendor app

Everything else is the stock firmware: keys, lighting, wireless, RTC clock and boot GIF are untouched.

> **Status: experimental.** One keyboard so far. The v2 overlay (title + progress bar, 1 Hz updates) has run on real
> hardware. **v3 (smooth scroll, flash read/config backup) is emulator-tested only; hardware test pending.**
> Flashing replaces the firmware and can leave the keyboard unusable until re-flashed. This project is not affiliated with AULA, Sonix or HFD. See [Safety](#safety).

## How it works (short)

The stock firmware is one bare-metal Cortex-M0 binary (no OS). The main MCU drives an ST7789 TFT over SPI0, reads the
GIF/icons/background from an external NOR flash on SPI1, and keeps time in an I²C RTC. The patch is ~1.8 KB of C
placed in empty flash. The callers of three existing routines are re-pointed at it (the 72 screen-blit calls, the
single USB command dispatcher call, and the clock's once-a-second seconds-digit draw), so no original
code is modified. Details:
[docs/firmware-notes.md](docs/firmware-notes.md), wire protocol: [docs/protocol.md](docs/protocol.md).

## Layout

```
host/             PC-side tools (Python): aula_music.py, aula_screen.py, aula_text.py, aula_timesync.py
firmware_patch/   patch.c + build/test scripts (compile, splice into stock image, unicorn emulator tests)
tools/            extract the stock image from the vendor flasher, build a patched flasher, make a private kit
docs/             hardware map, function catalogue, USB protocol
build_all.py      one-shot build
```

The vendor firmware/flasher is **not** in this repository (it is the vendor's binary). You supply your own copy of
the vendor's *AULA F108PRO Reset* package.

## Build

Requirements: Python 3.10+, `pip install -r requirements-dev.txt`, and a clang with the ARM target + lld
(Android NDK r23+ works; or set `LLVM_BIN`; see `firmware_patch/toolchain.py`).

```bash
python build_all.py "AULA F108PRO Reset.zip"
# -> dist/AULA_F108Pro_patched_flasher.exe   (vendor flasher with the patched image embedded; do not publish it)
```

`build.py` refuses to run unless the image is exactly the analysed build (SI-2688 1.14" tri-mode, HFD80CP100 V1.08,
2025-05-10), and `test_emu.py` runs the patch against the real firmware routines in an emulator.

## Flash

1. Close the AULA driver software. Keyboard on the **USB cable**.
2. Run the patched flasher, click **Start** once, wait ~20 s (the keyboard disconnects and reconnects).
3. `python host/aula_screen.py probe` should print `patched firmware v3`.
4. Flashing resets the keyboard's stored settings (Fn-layer keys, lighting). Re-apply them once (vendor app), then
   back them up so you never have to again:
   ```bash
   python host/aula_backup.py                                   # dump live flash (~12 s)
   python tools/make_config_patch.py firmware_patch/patched.bin private/live_flash.bin private/config_patch.json
   python build_all.py "AULA F108PRO Reset.zip" --config private/config_patch.json   # next flasher keeps your settings
   ```

Rollback: run the *original* vendor flasher.

## Use

```bash
pip install hidapi pillow winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections
python host/aula_music.py          # now-playing on the clock page (--speed N px/s; Ctrl+C to stop; disappears ~5 s later)
python host/aula_screen.py bar 42  # progress bar demo; `release` to undo
python host/aula_text.py "hello"   # takes over the whole screen; `--release` to give it back
python host/aula_timesync.py       # set the keyboard clock (works on stock firmware too); --watch to keep syncing
```

Don't run these while the vendor app is configuring the keyboard; two programs writing to the same HID interface can
garble a transfer.

## Safety

* Only for the F108Pro with the colour screen. Other boards/revisions may use different pins or panel offsets.
* The patch writes only to flash that is empty in the stock image; the build asserts this and the call-site counts.
* Not verified on hardware: very long sessions, other revisions, the Bluetooth/2.4G paths (untouched, but untested after patching).
* You are responsible for your keyboard. No warranty (see LICENSE).

## Legal

Interoperability research on hardware the author owns. No vendor code or firmware is distributed here. MIT licence for
the code in this repository only; vendor binaries remain the property of their owners.
