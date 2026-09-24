"""Visual layer on top of the native Streamlit theme (.streamlit/config.toml).

Native theming already handles light/dark colors, fonts and chart palettes.
This module only adds what the theme cannot express: design tokens for the
custom HTML components (hero, alert cards, bed map, chips) and a few layout
refinements (sticky assistant dock, tighter paddings).
"""

from __future__ import annotations

import streamlit as st

from core.config import BRAND, STATUS

# Tokens per mode. Custom components read them as CSS variables (--hiq-*).
TOKENS = {
    "light": {
        "surface": "#ffffff", "surface-2": "#f7f8fb", "border": "#dcdfea", "ink": BRAND["navy"],
        "muted": "#5b6189", "accent": BRAND["green"], "lime": BRAND["lime"], "hero": BRAND["navy"],
        "hero-ink": "#ffffff", "chip": "#eef0f6",
        "critical-bg": "#fdecec", "serious-bg": "#fdf0e6", "warning-bg": "#fbf5dc", "info-bg": "#eceefa", "good-bg": "#e9f4e6",
        "bed-free": "#d8ecc2", "bed-busy": "#3b47a8", "bed-long": "#c0781a",
    },
    "dark": {
        "surface": "#151b3a", "surface-2": "#11162f", "border": "#262d55", "ink": "#e6e8f5",
        "muted": "#9aa1c9", "accent": BRAND["lime"], "lime": BRAND["lime"], "hero": "#1b2250",
        "hero-ink": "#ffffff", "chip": "#1f2750",
        "critical-bg": "#3a1518", "serious-bg": "#3a2412", "warning-bg": "#342c0c", "info-bg": "#1d2350", "good-bg": "#16301a",
        "bed-free": "#2b4a1a", "bed-busy": "#6f7bd8", "bed-long": "#c28226",
    },
}


def theme_type() -> str:
    """``light`` or ``dark`` as reported by the browser session."""
    kind = getattr(getattr(st.context, "theme", None), "type", None)
    return kind if kind in TOKENS else "light"


def inject() -> None:
    """Inject CSS variables and component styles (call once per run)."""
    tokens = TOKENS[theme_type()]
    variables = ";".join(f"--hiq-{k}:{v}" for k, v in tokens.items())
    status = ";".join(f"--hiq-{k}:{v}" for k, v in STATUS.items())
    st.html(f"<style>:root{{{variables};{status}}}{CSS}</style>")


CSS = """
/* Layout ------------------------------------------------------------------ */
[data-testid="stMainBlockContainer"]{padding-top:1.4rem;padding-bottom:3rem;max-width:1680px}
header[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebarUserContent"]{padding-top:.5rem}

/* Assistant dock: follows the scroll so it is always at hand ---------------- */
/* The whole column sticks inside the page-long row; flex-start keeps its own
   height equal to the dock so it has room to travel. */
[data-testid="stColumn"]:has(.st-key-chat_dock){position:sticky;top:3.4rem;align-self:flex-start;z-index:5}

/* Metric cards: subtle lift + brand accent on hover ------------------------ */
[data-testid="stMetric"]{background:var(--hiq-surface);transition:border-color .15s,transform .15s}
[data-testid="stMetric"]:hover{border-color:var(--hiq-lime);transform:translateY(-1px)}
[data-testid="stMetricLabel"] p{font-size:.78rem;font-weight:500;color:var(--hiq-muted)}

/* Hero banner -------------------------------------------------------------- */
.hiq-hero{background:var(--hiq-hero);color:var(--hiq-hero-ink);border-radius:14px;padding:18px 22px;
  display:flex;flex-wrap:wrap;gap:16px;align-items:center;justify-content:space-between;
  border-left:6px solid var(--hiq-lime)}
.hiq-hero h2{margin:0;font-size:1.25rem;font-weight:700;color:var(--hiq-hero-ink);padding:0}
.hiq-hero p{margin:2px 0 0;opacity:.82;font-size:.86rem}
.hiq-hero .hiq-pills{display:flex;gap:8px;flex-wrap:wrap}
.hiq-hero .hiq-pill{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);
  border-radius:999px;padding:4px 12px;font-size:.78rem;font-weight:500;white-space:nowrap}
.hiq-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}

/* Page header -------------------------------------------------------------- */
.hiq-kicker{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--hiq-accent);font-weight:600;margin:0}
.hiq-title{font-size:1.55rem;font-weight:700;color:var(--hiq-ink);margin:0 0 2px;line-height:1.2}
.hiq-sub{font-size:.86rem;color:var(--hiq-muted);margin:0}
.hiq-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.hiq-chip{background:var(--hiq-chip);color:var(--hiq-ink);border-radius:999px;padding:3px 10px;font-size:.74rem;font-weight:500}
.hiq-chip b{color:var(--hiq-accent);font-weight:600}

/* Section titles ----------------------------------------------------------- */
.hiq-section{display:flex;align-items:baseline;justify-content:space-between;margin:.4rem 0 .2rem}
.hiq-section h4{margin:0;padding:0;font-size:.98rem;font-weight:600;color:var(--hiq-ink)}
.hiq-section span{font-size:.76rem;color:var(--hiq-muted)}

/* Alert cards -------------------------------------------------------------- */
.hiq-alert{border-radius:0;border-left:4px solid var(--hiq-info);background:var(--hiq-info-bg);
  padding:10px 12px;margin-bottom:8px}
.hiq-alert.critical{border-left-color:var(--hiq-critical);background:var(--hiq-critical-bg)}
.hiq-alert.serious{border-left-color:var(--hiq-serious);background:var(--hiq-serious-bg)}
.hiq-alert.warning{border-left-color:var(--hiq-warning);background:var(--hiq-warning-bg)}
.hiq-alert .t{font-weight:600;font-size:.86rem;color:var(--hiq-ink)}
.hiq-alert .d{font-size:.78rem;color:var(--hiq-muted);margin-top:2px}
.hiq-alert .a{font-size:.8rem;color:var(--hiq-ink);margin-top:5px}
.hiq-alert .tag{font-size:.66rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--hiq-muted)}

/* Bed map ------------------------------------------------------------------ */
.hiq-bedgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(34px,1fr));gap:4px}
.hiq-bed{height:26px;border-radius:5px;background:var(--hiq-bed-free);font-size:.6rem;display:flex;
  align-items:center;justify-content:center;color:var(--hiq-ink);font-family:"JetBrains Mono",monospace}
.hiq-bed.busy{background:var(--hiq-bed-busy);color:#fff}
.hiq-bed.long{background:var(--hiq-bed-long);color:#fff}
.hiq-bed.maint,.hiq-maint-swatch{background:repeating-linear-gradient(45deg,var(--hiq-chip),var(--hiq-chip) 4px,var(--hiq-border) 4px,var(--hiq-border) 8px);color:var(--hiq-muted)}
.hiq-legend{display:flex;gap:14px;font-size:.76rem;color:var(--hiq-muted);margin:4px 0 10px;flex-wrap:wrap}
.hiq-legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-2px}

/* Chat --------------------------------------------------------------------- */
.hiq-agent-head{display:flex;align-items:center;gap:10px}
.hiq-avatar{width:34px;height:34px;border-radius:10px;background:var(--hiq-hero);display:flex;align-items:center;
  justify-content:center;border:2px solid var(--hiq-lime);color:#fff;font-weight:700;font-size:.8rem}
.hiq-agent-head .n{font-weight:700;font-size:.95rem;color:var(--hiq-ink);line-height:1.1}
.hiq-agent-head .s{font-size:.72rem;color:var(--hiq-muted)}
.hiq-context{font-size:.72rem;color:var(--hiq-muted);background:var(--hiq-chip);border-radius:8px;padding:6px 9px;margin:6px 0 2px}
.hiq-context b{color:var(--hiq-ink)}
.hiq-note{font-size:.76rem;color:var(--hiq-muted)}
"""
