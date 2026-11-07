/* AULA F108Pro (SN32F290, stock "HFD80CP100 V1.08" image) screen-control patch, v3.
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
#define FONT16   ((const u8 *)0x15937)                             /* 16px font: 16 bytes/glyph, 1 byte/row, bit0 = left, ASCII 0x20.. */

#define PAGE      (*(volatile u8 *)0x2000022d)  /* UI page id: 1 = main clock page */
#define BLIT_IDLE (*(volatile u8 *)0x200002a4)  /* 1 = no DMA blit in flight */
#define PKT       ((volatile u8 *)0x20001350)   /* current feature report, b0 first */
#define SPI0_DR   (*(volatile u32 *)0x4001C01C)
#define SPI0_ST   (*(volatile u32 *)0x4001C00C)
#define TFT_CS_LO (*(volatile u32 *)0x40046028) /* GPIO1 bit8 */
#define TFT_CS_HI (*(volatile u32 *)0x40046024)

#define MAGIC 0x5AC0FFEEu
#define TEXT_X 4
#define TEXT_Y 81
#define TEXT_W 232
#define TXT_MAX 95
#define GAP_CELLS 4
#define RAIL 0x2104
#define FILLC 0x07E0

/* ponytail: SRAM isn't known to be zeroed at reset, so a 32-bit magic guards the state. */
static volatile struct {
    u32 magic;
    u8 mode, clip;
    u8 act, ttl, px, shown;
    u8 dtext, dbar, age, speed, len, cal;
    u32 pos_q8, step_q8, tps, last_tick, last_sec_tick, last_pos;
    char txt[96];
} *const S = (void *)0x20002e00;
_Static_assert(sizeof *S <= 0x90, "state must stay below TICKS");
static volatile u32 *const TICKS = (void *)0x20002e90;   /* SysTick count; only differences matter */

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
    if (cmd < 0x90 || cmd > 0x97) return FW_CMD(cmd, val);
    volatile u8 *p = PKT;
    if (cmd == 0x93) { p[8] = 'P'; p[9] = 'A'; p[10] = 'T'; p[11] = 3; return 1; }
    if (cmd == 0x96) {                                   /* flash read, for config backup */
        u32 a = (u32)p[2] << 16 | (u32)p[3] << 8 | p[4];
        if (a + 56 > 0x40000) return 0;
        for (unsigned i = 0; i < 56; i++) p[8 + i] = *(const volatile u8 *)(a + i);
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
        if (adr == 0 && !x && !y && w == 240 && h == 135) S->dtext = S->dbar = 1;  /* clock background redrawn */
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

static void fast_tick(void) {
    if (S->magic != MAGIC || !S->act || !S->ttl || !S->len || PAGE != 1 || S->mode == 1) return;
    if (BLIT_IDLE != 1) return;                           /* never wait here: skip this frame */
    u32 t = *TICKS, dt = t - S->last_tick;
    if (S->len * 8 > TEXT_W) {
        if (dt < (S->tps >> 5) + 1) return;               /* <= ~30 fps */
        S->last_tick = t;
        if (dt > S->tps) dt = S->tps >> 3;                /* e.g. after a long pause */
        S->pos_q8 += dt * S->step_q8;
        u32 wrap = (S->len + GAP_CELLS) * 8 * 256;
        while (S->pos_q8 >= wrap) S->pos_q8 -= wrap;
        u32 ip = S->pos_q8 >> 8;
        if (ip == S->last_pos && !S->dtext) return;
        S->last_pos = ip; S->dtext = 0;
        render(ip);
    } else if (S->dtext) {
        S->dtext = 0;
        render(0);
    }
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
    if (S->cal < 3) {                                    /* first samples: adopt any plausible tick rate */
        if (dt >= 200 && dt <= 20000) { S->tps = dt; S->cal++; }
    } else if (dt >= (S->tps * 3 >> 2) && dt <= (S->tps * 5 >> 2) + 64)   /* then refine; ignore redraw-induced extras */
        S->tps = (S->tps * 3 + dt) >> 2;
    S->step_q8 = udiv((u32)S->speed * 256, S->tps);
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
