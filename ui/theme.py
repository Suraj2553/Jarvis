"""JARVIS visual constants — the complete color/typography/layout system.

Every pixel that appears on screen traces back to one of these constants.
No magic numbers in rendering code — named constants only.
"""

# ── Arc Reactor Palette ──────────────────────────────────────────────── #
ARC_BLUE         = "#00D4FF"    # primary, active
ARC_BLUE_DIM     = "#003D4F"    # idle glow
ARC_BLUE_GLOW    = "#00D4FF22"  # bloom color

REPULSOR_PURPLE  = "#8B5CF6"    # AI thinking
REPULSOR_DIM     = "#2D1F52"

SUIT_GREEN       = "#10B981"    # speaking, success
SUIT_GREEN_DIM   = "#042F22"

WARNING_AMBER    = "#F59E0B"    # caution
CRITICAL_RED     = "#EF4444"    # error, urgent

VOID_BLACK       = "#02020A"    # base background
DEEP_SPACE       = "#05050F"    # secondary background
GHOST_WHITE      = "#E8F4FD"    # primary text
MUTED_STEEL      = "#4A6080"    # secondary text

# Per-state accent colours
COLOR_IDLE       = ARC_BLUE
COLOR_LISTENING  = ARC_BLUE
COLOR_THINKING   = REPULSOR_PURPLE
COLOR_SPEAKING   = SUIT_GREEN
COLOR_WARNING    = WARNING_AMBER
COLOR_ERROR      = CRITICAL_RED

IDLE_ALPHA       = 64

# Status dot colours
DOT_IDLE         = "#333344"
DOT_LISTENING    = ARC_BLUE
DOT_THINKING     = REPULSOR_PURPLE
DOT_SPEAKING     = SUIT_GREEN

# ── Text card ───────────────────────────────────────────────────────── #
TEXT_QUERY       = "#556677"
TEXT_RESPONSE    = GHOST_WHITE
CARD_BG          = "rgba(4, 8, 22, 210)"
CARD_BORDER      = "rgba(0, 212, 255, 50)"

# ── Typography ──────────────────────────────────────────────────────── #
FONT_MONO        = "Share Tech Mono"  # downloaded on first run if missing
FONT_SECONDARY   = "Rajdhani"
FONT_FALLBACK    = "Courier New"
FONT_MONO_LEGACY = "Consolas"         # fallback for existing code

FONT_QUERY_PX    = 11
FONT_RESPONSE_PX = 13

# ── Layout ──────────────────────────────────────────────────────────── #
HUD_SIZE         = 380    # new HUD is 380×380
HUD_MARGIN       = 20
HUD_BOTTOM_OFFSET = 400
HUD_EDGE_OFFSET  = 24     # pixels from screen edge

# ── Corner data nodes ────────────────────────────────────────────────── #
NODE_UPDATE_MS   = 2000   # refresh interval for CPU/RAM/battery readouts

# ── Conversation panel ───────────────────────────────────────────────── #
PANEL_WIDTH      = 340
PANEL_MAX_HEIGHT = 180
PANEL_BG         = "rgba(2, 2, 10, 0.92)"

# ── Animation ────────────────────────────────────────────────────────── #
SCAN_LINE_INTERVAL_MS = 8000   # scan line sweeps every 8 seconds
RING_SPAWN_INTERVAL_MS = 350   # speaking rings spawn every 350ms
