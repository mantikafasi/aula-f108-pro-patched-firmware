/* AULA F108Pro (SN32F290, stock "HFD80CP100 V1.08" image) screen-control patch, v5.
 * Linked at 0x1A000 (empty flash). Reached only via re-targeted BLs; the original code is untouched.
 *
 * New feature-report commands (b0=0x04, b1=cmd); all ack with data[3]==1 on success:
 *   04 90 x0 y0 x1 y1 cH cL     fill rectangle (inclusive), colour RGB565
 *   04 91 x y n  p0H p0L ...    n<=28 pixels, one row run starting at (x,y)
 *   04 92 mode clip             0 normal | 1 host owns screen (all firmware blits dropped)
 *                               | 2 GIF frames clipped to clipH rows
 *   04 93                       probe: reply data[8..11] = 'P','A','T',version
 *   04 94 show ttl px speed     music overlay (clock page only): show=0 hides; ttl = seconds it lives without a
 *                               refresh; px = progress bar fill 0..240; speed = title scroll px/s (0 = keep)
 *   04 95 off n c0..            write n<=56 title chars at offset off (buffer 95 chars), not shown yet
 *   04 97 len                   commit title of len chars (restarts the scroll)
 *   04 96 a2 a1 a0              read 56 bytes of internal flash at 24-bit address into data[8..63] (config backup)
 *   04 98 part                  diagnostics: part 0 = overlay state/timing, part 1 = why-no-frame counters
 *   04 99 show ttl p5 pw s5[4] sw[4]  usage rings (clock page): replaces the left status icons (x=8..133, y=100..127)
 *                               with two rings: p5/pw = percent 0..100, s5/sw = up to 4 chars (NUL padded) of reset text.
 *                               show=0 (or ttl expiry) brings the stock icons back via a page redraw
 *
 * Overlay: title line y=81..96 (firmware's 16px font, rendered here pixel-smooth), bar y=129..132.
 * Runs only while UI page 1 (clock) shows; never waits for the display (skips a frame instead);
 * redraws after page redraws; erases itself if the host stops refreshing.
 */
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;

#define FW_FILL  ((void (*)(unsigned, int, int, int, int))0x7b21) /* fillrect(colour,x0,y0,x1,y1) */
#define FW_WIN   ((void (*)(int, int, int, int))0x7a93)           /* set TFT window + RAMWR       */
#define FW_DATA  ((void (*)(unsigned))0x7801)                      /* send one data byte to TFT    */
#define FW_BLIT  ((void (*)(int, int, int, int, unsigned))0x7b83) /* flash->TFT blit (x,y,w,h,adr)*/
#define FW_CMD   ((int (*)(int, int))0x3fc1)                       /* original command dispatcher  */
#define FW_NUM   ((void (*)(int, int, int, int, unsigned, unsigned, unsigned))0x12a9f) /* draw number */
#define FW_UI    ((void (*)(void))0xf157)                          /* UI event renderer (main loop) */
#define FW_TICK  ((void (*)(void))0x93d)                           /* SysTick work function         */
#define FW_REDRAW ((void (*)(void))0xffd9)                         /* full page redraw (queues UI events) */
#define FONT12   ((const u8 *)0x154c3)                             /* 12px font: 12 bytes/glyph, bit0 = left */
#define FONT16   ((const u8 *)0x15937)                             /* 16px font: 16 bytes/glyph, 1 byte/row, bit0 = left, ASCII 0x20.. */

#define PAGE      (*(volatile u8 *)0x2000022d)  /* UI page id: 1 = main clock page */
#define BLIT_IDLE (*(volatile u8 *)0x200002a4)  /* 1 = no DMA blit in flight */
#define PKT       ((volatile u8 *)0x20001350)   /* current feature report, b0 first */
#define SPI0_DR   (*(volatile u32 *)0x4001C01C)
#define SPI0_ST   (*(volatile u32 *)0x4001C00C)
#define TFT_CS_LO (*(volatile u32 *)0x40046028) /* GPIO1 bit8 */
#define TFT_CS_HI (*(volatile u32 *)0x40046024)

/* The flasher resets the chip without clearing SRAM, so a state block from the PREVIOUS firmware can survive and look
 * valid. Bump LAYOUT whenever struct S changes: a stale block then fails the magic check and is re-initialised. */
#define LAYOUT 5
#define MAGIC (0x5AC0FF00u | LAYOUT)
#define TEXT_X 4
#define TEXT_Y 81
#define TEXT_W 232
#define TXT_MAX 95
#define GAP_CELLS 4
#define RAIL 0x2104
#define FILLC 0x07E0

/* ponytail: SRAM isn't known to be zeroed at reset, so a 32-bit magic guards the state. */
#include "ring_table.h"

static volatile struct {
    u32 magic;
    u8 mode, clip;
    u8 act, ttl, px, shown;
    u8 dtext, dbar, age, speed, len, cal;
    u32 pos_q8, step_q8, tps, last_tick, last_sec_tick, last_pos;
    u32 ui_calls, frames, sec_calls, bad;
    u32 cnt[8];                                           /* why a frame was (not) drawn, see WHY_* */
    char txt[96];
    u8 uact, uttl, up5, upw, udirty, urestore;            /* usage rings */
    char us5[5], usw[5];
} *const S = (void *)0x20002e00;
_Static_assert(sizeof *S <= 0xE0, "state must stay below TICKS");
static volatile u32 *const TICKS = (void *)0x20002ee0;   /* SysTick count; only differences matter */
enum { WHY_DRAWN, WHY_INACTIVE, WHY_HIDDEN, WHY_PAGE, WHY_MODE, WHY_BUSY, WHY_PACE, WHY_SAMEPOS };

static u32 udiv(u32 n, u32 d) {                           /* no libgcc here */
    u32 q = 0, r = 0;
    for (int i = 31; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) { r -= d; q |= 1u << i; }
    }
    return q;
}

static void init(void) {                                  /* first use after reset */
    if (S->magic == MAGIC) return;
    volatile u8 *b = (volatile u8 *)S;
    for (unsigned i = 0; i < sizeof *S; i++) b[i] = 0;
    S->magic = MAGIC; S->speed = 40; S->tps = 1000; S->step_q8 = udiv(40u * 256, 1000);
}

static int wait_idle(void) {
    for (u32 i = 0; i < 400000; i++)
        if (BLIT_IDLE == 1) return 1;
    return 0;
}

int cmd_hook(int cmd, int val) {
    if (cmd < 0x90 || cmd > 0x99) return FW_CMD(cmd, val);
    volatile u8 *p = PKT;
    if (cmd == 0x93) { p[8] = 'P'; p[9] = 'A'; p[10] = 'T'; p[11] = 5; return 1; }
    if (cmd == 0x98) {                                   /* diagnostics */
        if (S->magic != MAGIC) return 0;
        u32 w[14]; unsigned n = 0;
        if (p[2] == 0) {
            w[n++] = *TICKS; w[n++] = S->tps; w[n++] = S->step_q8; w[n++] = S->pos_q8; w[n++] = S->last_tick;
            w[n++] = S->last_sec_tick; w[n++] = S->ui_calls; w[n++] = S->frames; w[n++] = S->sec_calls;
            w[n++] = (u32)S->act | S->ttl << 8 | S->len << 16 | (u32)S->cal << 24;
            w[n++] = (u32)PAGE | (u32)BLIT_IDLE << 8 | (u32)S->mode << 16 | (u32)S->speed << 24;
            w[n++] = (u32)S->dtext | (u32)S->dbar << 8 | (u32)S->px << 16 | (u32)S->shown << 24;
            w[n++] = S->bad; w[n++] = S->last_pos;
        } else {
            for (; n < 8; n++) w[n] = S->cnt[n];
        }
        for (unsigned i = 0; i < n; i++)
            for (unsigned b = 0; b < 4; b++) p[8 + 4 * i + b] = w[i] >> (8 * b);
        return 1;
    }
    if (cmd == 0x96) {                                   /* flash read, for config backup */
        u32 a = (u32)p[2] << 16 | (u32)p[3] << 8 | p[4];
        if (a + 56 > 0x40000) return 0;
        for (unsigned i = 0; i < 56; i++) p[8 + i] = *(const volatile u8 *)(a + i);
        return 1;
    }
    if (cmd == 0x99) {                                   /* usage rings */
        if (p[2] == 0) { init(); if (S->uact) { S->uact = 0; S->urestore = 1; } return 1; }
        if (p[3] == 0 || p[4] > 100 || p[5] > 100) return 0;
        init();
        unsigned ch = !S->uact || S->up5 != p[4] || S->upw != p[5];
        S->up5 = p[4]; S->upw = p[5];
        for (unsigned i = 0; i < 4; i++) {
            unsigned a = p[6 + i], b = p[10 + i];
            if (a && (a < 0x20 || a > 0x7e)) a = '?';
            if (b && (b < 0x20 || b > 0x7e)) b = '?';
            if (S->us5[i] != a || S->usw[i] != b) ch = 1;
            S->us5[i] = a; S->usw[i] = b;
        }
        S->us5[4] = S->usw[4] = 0;
        S->uttl = p[3]; S->uact = 1;
        if (ch) S->udirty = 1;
        return 1;
    }
    if (cmd == 0x92) {
        if (p[2] > 2) return 0;
        init(); S->clip = p[3]; S->mode = p[2];
        return 1;
    }
    if (cmd == 0x94) {                                   /* music keep-alive / progress / hide */
        init();
        S->px = p[4] > 240 ? 240 : p[4];
        S->ttl = p[2] ? p[3] : 0;                         /* hide = let the next tick erase it */
        if (!S->act) S->dtext = S->dbar = 1;               /* appearing from hidden: paint everything */
        S->act = 1;
        if (p[5] && p[5] != S->speed) {
            S->speed = p[5]; S->step_q8 = udiv((u32)S->speed * 256, S->tps);
        }
        return 1;
    }
    if (cmd == 0x95) {                                   /* title chunk */
        unsigned off = p[2], n = p[3];
        if (n > 56 || off + n > TXT_MAX) return 0;
        init();
        for (unsigned i = 0; i < n; i++) {
            unsigned c = p[4 + i];
            S->txt[off + i] = (c < 0x20 || c > 0x7e) ? '?' : c;   /* font covers 0x20..0x7e */
        }
        return 1;
    }
    if (cmd == 0x97) {                                   /* commit title */
        if (p[2] > TXT_MAX) return 0;
        init();
        S->len = p[2]; S->txt[S->len] = 0;
        S->pos_q8 = 0; S->last_pos = 0xFFFFFFFFu; S->dtext = 1;
        return 1;
    }
    unsigned x0 = p[2], y0 = p[3];                       /* 0x90 / 0x91 */
    if (cmd == 0x90) {
        unsigned x1 = p[4], y1 = p[5];
        if (x1 > 239 || y1 > 134 || x0 > x1 || y0 > y1 || !wait_idle()) return 0;
        FW_FILL((p[6] << 8) | p[7], x0, y0, x1, y1);
        return 1;
    }
    unsigned n = p[4];                                   /* cmd == 0x91 */
    if (n == 0 || n > 28 || x0 + n > 240 || y0 > 134 || !wait_idle()) return 0;
    FW_WIN(x0, y0, x0 + n - 1, y0);
    for (unsigned i = 0; i < n; i++) { FW_DATA(p[5 + 2 * i]); FW_DATA(p[6 + 2 * i]); }
    return 1;
}

void blit_hook(int x, int y, int w, int h, unsigned adr) {
    if (S->magic == MAGIC) {
        if (S->mode == 1) return;
        if (S->mode == 2 && w == 240 && h == 135 && S->clip && S->clip < 135) h = S->clip;
        if (adr == 0 && !x && !y && w == 240 && h == 135) S->dtext = S->dbar = S->udirty = 1;  /* clock background redrawn */
        if (S->uact && y == 100 && h == 28 && (x == 8 || x == 40)) { S->udirty = 1; return; }   /* the rings own these icons */
    }
    FW_BLIT(x, y, w, h, adr);
}

/* ---- SysTick: count ticks, then the stock work ---- */
void tick_hook(void) {
    (*TICKS)++;
    FW_TICK();
}

/* ---- smooth title: stream the 232x16 text window straight to the panel, pixel offset `pos` ---- */
static inline void spi(unsigned v) {
    SPI0_DR = v;
    while (SPI0_ST & 0x10) {}                             /* busy */
    (void)SPI0_DR;
}

static void render(u32 pos) {
    u32 cells = S->len + GAP_CELLS, len = S->len;
    int scroll = len * 8 > TEXT_W;
    FW_WIN(TEXT_X, TEXT_Y, TEXT_X + TEXT_W - 1, TEXT_Y + 15);
    TFT_CS_LO = 0x100;
    for (unsigned row = 0; row < 16; row++) {
        u32 p = pos;                                      /* virtual pixel under x=0 */
        unsigned x = 0;
        while (x < TEXT_W) {
            u32 c = p >> 3;
            if (scroll && c >= cells) c -= cells;
            unsigned g = 0;
            if (c < len) g = FONT16[((u8)S->txt[c] - 0x20) * 16 + row];
            unsigned bit = p & 7;
            for (; bit < 8 && x < TEXT_W; bit++, x++) {
                unsigned v = (g >> bit) & 1 ? 0xFF : 0x00;   /* white on black: both bytes equal */
                spi(v); spi(v);
                p++;
            }
            if (scroll && p >= cells * 8) p -= cells * 8;
        }
    }
    TFT_CS_HI = 0x100;
}

static unsigned ucol(unsigned p) { return p > 85 ? 0xFA89 : p > 60 ? 0xE501 : 0x3DCA; }

static void render_block(unsigned x0, unsigned pct, const char *lab, const volatile char *sub) {
    unsigned col = ucol(pct), ones = pct > 99 ? 99 : pct, tens = 0;
    for (unsigned k = 10; k <= 90; k += 10) tens += ones >= k;     /* no libgcc divide here (and clang must not make one) */
    ones -= tens * 10;
    char d[2] = { '0' + tens, '0' + ones };
    FW_WIN(x0, 100, x0 + 59, 127);
    TFT_CS_LO = 0x100;
    for (unsigned y = 0; y < 28; y++)
        for (unsigned x = 0; x < 60; x++) {
            unsigned c = 0;
            if (x < 28) {
                unsigned m = RING[y * 28 + x];
                c = m < 100 ? (m < pct ? col : 0x2945) : 0;
                if (x >= 8 && x < 20 && y >= 8 && y < 20) {
                    unsigned cx = x >= 14;
                    if ((FONT12[((u8)d[cx] - 0x20) * 12 + y - 8] >> (x - 8 - 6 * cx)) & 1) c = 0xFFFF;
                }
            } else if (x >= 31 && ((y >= 1 && y < 13) || (y >= 15 && y < 27))) {
                unsigned t = x - 31, ci = (t >= 6) + (t >= 12) + (t >= 18) + (t >= 24), row = y < 13 ? y - 1 : y - 15;
                t -= ci * 6;
                unsigned ch = ci < 4 ? (y < 13 ? (u8)lab[ci] : (u8)sub[ci]) : 0;
                if (ch && ((FONT12[(ch - 0x20) * 12 + row] >> t) & 1)) c = y < 13 ? col : 0x8C51;
            }
            spi(c >> 8); spi(c & 0xFF);
        }
    TFT_CS_HI = 0x100;
}

static void fast_tick(void) {
    S->ui_calls++;
    if (S->magic == MAGIC) {
        if (S->urestore) { S->urestore = 0; if (PAGE == 1 && S->mode != 1) FW_REDRAW(); }
        if (S->uact && S->udirty && PAGE == 1 && S->mode != 1 && BLIT_IDLE == 1) {
            S->udirty = 0;
            FW_FILL(0, 68, 100, 73, 127);                    /* gap between the two rings */
            static const char L5[4] = "5h", LW[4] = "wk";    /* zero padded: the glyph loop reads 4 chars */
            render_block(8, S->up5, L5, S->us5);
            render_block(74, S->upw, LW, S->usw);
            return;
        }
    }
    if (S->magic != MAGIC || !S->act || !S->ttl || !S->len) { S->cnt[WHY_INACTIVE]++; return; }
    if (PAGE != 1) { S->cnt[WHY_PAGE]++; return; }
    if (S->mode == 1) { S->cnt[WHY_MODE]++; return; }
    if (BLIT_IDLE != 1) { S->cnt[WHY_BUSY]++; return; }   /* never wait here: skip this frame */
    u32 t = *TICKS, dt = t - S->last_tick;
    if (S->dtext) {                                       /* a repaint was requested: never starve it behind pacing */
        S->dtext = 0; S->last_tick = t; S->last_pos = S->pos_q8 >> 8;
        S->cnt[WHY_DRAWN]++; S->frames++;
        render(S->last_pos);
        return;
    }
    if (S->len * 8 <= TEXT_W) { S->cnt[WHY_SAMEPOS]++; return; }   /* static title: nothing to animate */
    if (dt < (S->tps >> 5) + 1) { S->cnt[WHY_PACE]++; return; }    /* <= ~30 fps */
    S->last_tick = t;
    if (dt > S->tps) dt = S->tps >> 3;                    /* e.g. after a long pause */
    S->pos_q8 += dt * S->step_q8;
    u32 wrap = (S->len + GAP_CELLS) * 8 * 256;
    while (S->pos_q8 >= wrap) S->pos_q8 -= wrap;
    u32 ip = S->pos_q8 >> 8;
    if (ip == S->last_pos) { S->cnt[WHY_SAMEPOS]++; return; }
    S->last_pos = ip;
    S->cnt[WHY_DRAWN]++; S->frames++;
    render(ip);
}

void ui_hook(void) {                                      /* main loop: stock UI renderer, then the overlay */
    FW_UI();
    fast_tick();
}

/* ---- once per second on the clock page: calibrate, expire, progress bar ---- */
static void overlay_sec(void) {
    if (S->magic != MAGIC) return;
    u32 t = *TICKS, dt = t - S->last_sec_tick;
    S->last_sec_tick = t;
    S->sec_calls++;
    if (S->cal < 3) {                                    /* first samples: adopt any plausible tick rate */
        if (dt >= 200 && dt <= 20000) { S->tps = dt; S->cal++; }
    } else if (dt >= (S->tps * 3 >> 2) && dt <= (S->tps * 5 >> 2) + 64) {   /* then refine; ignore redraw-induced extras */
        S->tps = (S->tps * 3 + dt) >> 2; S->bad = 0;
    } else if (dt >= 200 && dt <= 20000 && ++S->bad >= 3) {                  /* three misses in a row: we were wrong */
        S->tps = dt; S->bad = 0;
    }
    if (S->tps < 200 || S->tps > 20000) S->tps = 1000;
    S->step_q8 = udiv((u32)S->speed * 256, S->tps);
    if (S->uact && S->uttl && --S->uttl == 0) { S->uact = 0; S->urestore = 1; }   /* host went away: stock icons back */
    if (!S->act || PAGE != 1 || S->mode == 1) return;
    if (!wait_idle()) return;
    if (S->ttl == 0) {                                   /* host went away or asked to hide: erase */
        FW_FILL(0, 0, 80, 239, 98); FW_FILL(0, 0, 129, 239, 132);
        S->act = 0; S->shown = 0; S->dtext = S->dbar = 1;
        return;
    }
    S->ttl--;
    if (++S->age >= 30) { S->age = 0; S->dtext = S->dbar = 1; }   /* in case something else painted over us */
    if (S->dbar) { FW_FILL(RAIL, 0, 129, 239, 132); S->shown = 0; S->dbar = 0; }
    unsigned px = S->px, sh = S->shown;
    if (px > sh)      FW_FILL(FILLC, sh, 129, px - 1, 132);
    else if (px < sh) FW_FILL(RAIL, px, 129, sh - 1, 132);
    S->shown = px;
}

/* retargeted from the single `bl draw_num` that paints the clock's seconds digits */
void sec_hook(int x, int y, int v, int n, unsigned fg, unsigned bg, unsigned size) {
    FW_NUM(x, y, v, n, fg, bg, size);
    overlay_sec();
}
