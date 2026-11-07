# AULA F108Pro firmware notes

Stock image: SI-2688 1.14" tri-mode, HFD80CP100 V1.08 (2025-05-10), Sonix SN32F290 (Cortex-M0). 256 KB image, ~104 KB
of code, linked at 0. Source: RCDATA 4000 inside the vendor "AULA F108PRO Reset" ISP flasher. Everything below is from
static analysis plus emulation (unicorn); addresses are flash addresses in that image (Thumb, base 0).

## What it is
One bare-metal binary: reset handler -> C startup -> polling main loop (`0x2be`) + ISRs. SVC/PendSV vectors are
`b .` (no RTOS). HardFault (`0x9b6`) **requests a system reset**, so any crash looks like a reboot loop. The watchdog
is enabled and fed from the main loop/delay helpers. Stack top `0x20002D00`.

## Hardware map
| Block | Base | Use |
|---|---|---|
| SPI0 | `0x4001C000` | ST7789 TFT. DC = GPIO3 bit14, CS = GPIO1 bit8, reset = GPIO0 bit17, backlight = GPIO0 bit16 |
| SPI1 | `0x40058000` | external NOR flash (cmd `0x03` read, `0x90` id). CS = GPIO0 bit13 |
| UART | `0x40054000` | 16550-style, IRQ8: radio/dongle link (not analysed) |
| I²C  | helpers `0x13be0`/`0x13cb2` | external RTC at `0xA2/0xA3` (PCF8563-style) |
| WDT `0x40010000`, FLASH ctl `0x40062000`, USB `0x4005C000`, GPIO0-3 `0x40044000/46000/48000/4A000` | | |

## Screen functions
| Addr | Function |
|---|---|
| `0x789a` | TFT init (SLPOUT, MADCTL, COLMOD RGB565, INVON, ST7789V porch/power/gamma, DISPON) |
| `0x7822` | rotation 0-3: panel offset in RAM `0x20000222/3`, MADCTL (default 3: landscape, offsets 0x28,0x34) |
| `0x7808` / `0x7800` | TFT command / data byte via `0x134c8` (SPI0 byte send) |
| `0x7a92` | `window(x0,y0,x1,y1)` + RAMWR (adds panel offset) |
| `0x7b20` | `fillrect(colour,x0,y0,x1,y1)` (software; 5th arg on stack) |
| `0x7b82` | `blit(x,y,w,h,flash_addr)`: NOR -> TFT with SPI1+SPI0 hardware assist; IRQ6 sets idle flag `0x200002a4`; silently dropped if not idle |
| `0x12928` | draw char `(x,y,ch,fg,bg,size,transparent)`; fonts 12/16/18/20/24/32 px, ASCII 0x20..0x7E, tables at `0x154c3/0x15937/0x15f27/0x1635f/0x16797/0x17967` |
| `0x12a5a` / `0x12a9e` | draw string / draw number built on it |
| `0x7c38` / `0x7c5c` | display sleep / wake |
| `0xf114` | screen bring-up (init, read GIF header, first frame) |
| `0x1066a` | page/animation state machine; GIF frame address = `0x330100 + n*0xFD20` |
| `0xf156` | UI event renderer: ring buffer `0x200020bf` (100 entries) -> switch -> icons / GIF frames / clock digits |
| `0xffd8` | full page redraw (queues events) |
| `0x3bf4` | time packet handler (`b2 == 0x5A`) -> RTC |
| `0x3c4c` | upload data path: 4 KB output reports -> NOR page program |

UI page id is the byte at `0x2000022d`: `1` = main clock page (also set after a time sync, which redraws it).

### Clock page layout (page 1), from the draw calls in `0xf156`
Background full-screen blit from NOR address 0, then: date `YYYY-MM-DD` at y=7 (18 px, cyan), battery % top right,
big `HH:MM:SS` at y=45 (32 px, white; seconds digits drawn at x=165), status icons 29x28 at y=100 (x=8,40,138,171,204).
Text is drawn with opaque black cells. Free: y=80..98 (title line) and y=129..134 (below the icons).

## USB command path
`0x449a` (main loop, state byte `0x200000b8+1`) -> `0x419a` -> dispatcher `0x3fc0(cmd, u16@b8)`; packet buffer at
`0x20001350` (b0). Handler returns 1 -> reply `data[3]=1`, 0 -> `0xFF`. See `protocol.md`.

## The patch (`firmware_patch/patch.c`, linked at `0x1A000`)
* 72 `bl 0x7b82` -> `blit_hook` (host takeover modes; marks the overlay dirty when the clock background is redrawn)
* `bl 0x3fc0` at `0x41ca` -> `cmd_hook` (new commands 0x90-0x95; everything else falls through unchanged)
* `bl 0x12a9e` at `0xf84e` (seconds digits) -> `sec_hook` (stock digits, then the 1 Hz part: tick-rate calibration, ttl, progress bar)
* `bl 0xf156` at `0x826` (main loop UI renderer) -> `ui_hook` (stock renderer, then the smooth title frame)
* `bl 0x93c` at `0x8cce` (SysTick work) -> `tick_hook` (counts ticks for pacing, then the stock work)
* state at RAM `0x20002E00` (224 bytes), tick counter at `0x20002EE0`, guarded by a 32-bit magic because SRAM isn't known to be zeroed at reset
* title: rendered by the patch itself (232x16 window streamed straight to SPI0 with CS held low, pixel offset per frame),
  <= ~30 fps, never waits for the display; ~108k instructions + 7424 SPI bytes per frame (~6-8 ms, 17-24% CPU at 30 fps,
  SysTick still preempts so key scanning is unaffected). Scroll speed is calibrated against the 1 Hz seconds hook.
* progress bar: 1 Hz, delta-only; self-erases when the host stops refreshing (`ttl`).

Tests (`firmware_patch/test_emu.py`) run the real firmware routines in unicorn with fake SPI/GPIO: byte streams for every
command, input rejection, busy-DMA NACK, blit modes, all overlay states, and that 19 stock commands behave identically
through the hook. `profile_overlay.py` runs the overlay with the real draw code (no stubs) to count work and check for faults.

## Not verified
* Equivalence with firmware already on other units/revisions.
* Long-term behaviour on hardware; the Bluetooth/2.4G paths after patching.
* SPI0 clock rate (affects the per-frame time estimate above); real-hardware frame cost.

## Config storage and backups
The stock image's config pages (`0x9000`, `0x9400`, `0x9800`, `0x9C00`, `0xAC00`, written by dispatcher commands
`0x17`, `0x11`, `0x13`, `0x23`, `0x27`) ship as zeros, so flashing erases the user's stored keys/lighting. `04 96` lets the
host dump live flash; `tools/make_config_patch.py` diffs the dump against the image that was flashed and `build.py --config`
bakes those bytes into the next image, so later flashes keep the settings without the vendor app. The page format itself
is not decoded.
