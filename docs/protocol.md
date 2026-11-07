# USB protocol (wired mode)

Vendor control channel = HID **feature reports**, report ID 0, 64 data bytes (65 with the ID), on the HID collection
whose feature report length is 65 (`MI_03`, usage page `0xFF13` on the tested unit; `config.xml` says `MI_00`, which
is wrong for this unit). Open it with `GENERIC_WRITE` only: Windows reserves read access on keyboard collections.
Send with `HidD_SetFeature`, read the reply with `IOCTL_HID_GET_FEATURE` (`0xB0192`). ~35 ms between packets in the
vendor app; ~2 ms works for the commands here. Reply `data[3] == 1` means ack, `0xFF` nack. Bulk data (GIF upload) uses
4096-byte HID output reports.

## Time sync (vendor)

```
04 18 ...                      begin
04 28 00 00 00 00 00 00 01     arm (byte 8 = 1)
00 slot 5A yy mm dd hh mi ss 00 dow ... AA 55   (yy = year-2000, dow Sunday=0; trailer at bytes 62..63)
04 02                          end
```
The firmware ignores `slot`, stores the time at RAM `0x20000214..` and writes the external RTC (I²C `0xA2`).
The data packet is echoed back verbatim instead of acked with `data[3]==1`.

## GIF upload (vendor)

`04 18`; `04 72 slot ... [8..9]=block count (LE)`; then N x 4096-byte output reports. Image blob = 256-byte header
(byte 0 frame count <= 141; bytes 1..n = frame delay in 2 ms units) + frames of 240x135 RGB565 (64800 B each),
padded to 4 KB blocks. Stored in the external NOR flash at `0x330000` (header) / `0x330100` (frames).

## Commands added by this patch (`04 <cmd> ...`)

| Command | Meaning |
|---|---|
| `04 90 x0 y0 x1 y1 cH cL` | fill rectangle (inclusive coords, RGB565) |
| `04 91 x y n p0H p0L ...` | `n <= 28` pixels along row `y` starting at `x` |
| `04 92 mode clip` | `0` stock; `1` host owns screen (firmware blits dropped); `2` GIF frames clipped to `clip` rows |
| `04 93` | probe; reply bytes 8..11 = `'P','A','T',version` (4 for this firmware) |
| `04 94 show ttl px speed` | music overlay: `show=0` hides; `ttl` seconds without refresh before it erases itself; `px` = bar fill 0..240; `speed` = title scroll in px/s (0 keeps the current value, default 40) |
| `04 95 off n c0..c(n-1)` | write `n <= 56` title chars (ASCII 0x20..0x7E) at buffer offset `off` (buffer 95 chars); not shown until committed |
| `04 97 len` | commit a title of `len <= 95` chars and restart the scroll. Titles up to 29 chars are static; longer ones scroll smoothly |
| `04 98 part` | diagnostics: part 0 = overlay state/timing counters, part 1 = why-no-frame counters (little-endian 32-bit words from reply byte 8) |
| `04 96 a2 a1 a0` | read 56 bytes of internal flash at the 24-bit address into reply bytes 8..63 (used for config backup) |

Coordinates are screen coordinates (0..239, 0..134); the firmware adds the panel offset. Drawing waits for any
in-flight DMA blit and NACKs if it times out. The overlay runs only while UI page `1` (clock) is showing and never waits for the display (a frame is skipped if a DMA blit is in flight).
