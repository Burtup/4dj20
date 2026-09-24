"""Plotly figure factories with the HospitalIQ look.

Conventions (from the dataviz method):
* categorical colors come from the Streamlit theme palette in fixed order;
  semantic colors (triage, status) are explicit and never reused as series;
* thin marks: 2 px lines, 4 px rounded bar ends, recessive grid;
* a legend whenever there are ≥ 2 series; hover tooltips on every mark;
* one y-axis per chart (no dual axes).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.config import (CATEGORICAL_DARK, CATEGORICAL_LIGHT, SEQUENTIAL_DARK, SEQUENTIAL_LIGHT,
                         STATUS, TRIAGE_COLORS)
from ui.theme import theme_type

CONFIG = {"displayModeBar": False, "locale": "es"}
LABEL_MAX = 34  # longer category labels are ellipsised; the hover shows the full text


def palette() -> list[str]:
    return CATEGORICAL_DARK if theme_type() == "dark" else CATEGORICAL_LIGHT


def sequential() -> list[str]:
    return SEQUENTIAL_DARK if theme_type() == "dark" else SEQUENTIAL_LIGHT


def _muted() -> str:
    return "#9aa1c9" if theme_type() == "dark" else "#5b6189"


def style(fig: go.Figure, height: int = 300, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=4, r=8, t=8, b=4), hovermode="closest",
        barcornerradius=4, bargap=0.28, separators=",.",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None, font=dict(size=11)) if legend else None,
        showlegend=legend, font=dict(size=12),
        hoverlabel=dict(font_size=12),
    )
    fig.update_xaxes(showgrid=False, title=None, automargin=True)
    fig.update_yaxes(title=None, automargin=True, gridwidth=1, zeroline=False)
    return fig


def show(fig: go.Figure, key: str | None = None, **kwargs):
    return st.plotly_chart(fig, config=CONFIG, key=key, **kwargs)


def _fmt(values: pd.Series, kind: str) -> list[str]:
    if kind == "pct":
        return [f"{v * 100:.0f} %" for v in values]
    return [f"{v:,.0f}".replace(",", ".") for v in values]


# ── Basic forms ──────────────────────────────────────────────────────────────

def hbar(df: pd.DataFrame, x: str, y: str, *, fmt: str = "num", threshold: float | None = None,
         color: str | None = None, height: int | None = None, highlight_above: float | None = None) -> go.Figure:
    """Ranked horizontal bars with direct value labels."""
    d = df.sort_values(x)
    colors = color or palette()[0]
    if highlight_above is not None:
        colors = [STATUS["serious"] if v >= highlight_above else palette()[0] for v in d[x]]
    full = d[y].astype(str)
    short = full.where(full.str.len() <= LABEL_MAX, full.str[: LABEL_MAX - 1] + "…")
    # Plotly merges equal category labels: keep truncated labels unique.
    short = short + short.groupby(short).cumcount().map(lambda k: "​" * k)
    fig = go.Figure(go.Bar(
        x=d[x], y=short, orientation="h", marker_color=colors, customdata=full,
        text=_fmt(d[x], fmt), textposition="outside", cliponaxis=False,
        hovertemplate="%{customdata}<br>%{text}<extra></extra>",
    ))
    if threshold is not None:
        fig.add_vline(x=threshold, line_dash="dot", line_width=1.5, line_color=STATUS["serious"],
                      annotation_text=f"Umbral {threshold:.0%}" if fmt == "pct" else "Meta",
                      annotation_font_size=10, annotation_font_color=STATUS["serious"])
    fig.update_xaxes(showticklabels=False)
    top = float(d[x].max()) if len(d) else 1.0
    # Head-room so the outside value labels are never clipped.
    fig.update_xaxes(range=[0, max(1.05, top * 1.18) if fmt == "pct" else top * 1.2])
    return style(fig, height or max(220, 34 * len(d) + 40), legend=False)


def lines(df: pd.DataFrame, x: str, y: str, color: str | None = None, *, fmt: str = "num",
          target: float | None = None, height: int = 300, area: bool = False) -> go.Figure:
    fig = go.Figure()
    groups = [(None, df)] if color is None else list(df.groupby(color, observed=True))
    for i, (name, g) in enumerate(groups):
        fig.add_trace(go.Scatter(
            x=g[x], y=g[y], name=str(name) if name is not None else y, mode="lines",
            line=dict(width=2, color=palette()[i % len(palette())], shape="spline", smoothing=0.4),
            fill="tozeroy" if area and len(groups) == 1 else None,
            hovertemplate="%{x|%d %b}: %{y:,.0f}<extra>%{fullData.name}</extra>" if fmt == "num"
            else "%{x|%d %b}: %{y:.0%}<extra>%{fullData.name}</extra>",
        ))
    if target is not None:
        fig.add_hline(y=target, line_dash="dot", line_width=1.5, line_color=STATUS["serious"],
                      annotation_text="Meta", annotation_font_size=10, annotation_font_color=STATUS["serious"])
    if fmt == "pct":
        fig.update_yaxes(tickformat=".0%")
    fig.update_layout(hovermode="x unified")
    return style(fig, height, legend=len(groups) > 1)


def donut(labels: pd.Series, values: pd.Series, colors: list[str] | None = None, height: int = 260,
          center: str = "") -> go.Figure:
    fig = go.Figure(go.Pie(
        labels=labels.astype(str), values=values, hole=0.62, sort=False, direction="clockwise",
        marker=dict(colors=colors or palette(), line=dict(width=2, color="rgba(0,0,0,0)")),
        textinfo="percent", textfont_size=11, hovertemplate="%{label}: %{value:,.0f} (%{percent})<extra></extra>",
    ))
    if center:
        fig.add_annotation(text=center, showarrow=False, font=dict(size=15))
    return style(fig, height, legend=True)


def heatmap(grid: pd.DataFrame, *, fmt: str = ".1f", height: int = 280, colorbar_title: str = "") -> go.Figure:
    fig = go.Figure(go.Heatmap(
        z=grid.to_numpy(dtype=float), x=[str(c) for c in grid.columns], y=[str(i) for i in grid.index],
        colorscale=[[i / (len(sequential()) - 1), c] for i, c in enumerate(sequential())],
        xgap=2, ygap=2, colorbar=dict(title=colorbar_title, thickness=10, len=0.8),
        hovertemplate="%{y} · %{x}: %{z:" + fmt + "}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return style(fig, height, legend=False)


# ── Domain charts ────────────────────────────────────────────────────────────

def triage_bars(df: pd.DataFrame, value: str = "mediana", target: float | None = None, height: int = 260) -> go.Figure:
    """Bars colored by the (semantic) triage scale, with P90 whisker."""
    colors = [TRIAGE_COLORS.get(int(n), palette()[0]) for n in df["nivel_triage"]]
    error = dict(type="data", symmetric=False, array=(df["p90"] - df[value]).clip(lower=0), thickness=1.2,
                 width=6, color=_muted()) if "p90" in df else None
    fig = go.Figure(go.Bar(
        x=df["nivel"], y=df[value], marker_color=colors, error_y=error,
        text=[f"{v:.0f} min" for v in df[value]], textposition="inside", insidetextanchor="end",
        hovertemplate="%{x}<br>Mediana: %{y:.0f} min<extra></extra>",
    ))
    if target is not None:
        fig.add_hline(y=target, line_dash="dot", line_width=1.5, line_color=STATUS["serious"],
                      annotation_text=f"Meta {target:.0f} min", annotation_font_size=10,
                      annotation_font_color=STATUS["serious"])
    return style(fig, height, legend=False)


def forecast(fc: pd.DataFrame, height: int = 300) -> go.Figure:
    hist, fut = fc.dropna(subset=["real"]), fc.dropna(subset=["pronostico"])
    c = palette()
    fig = go.Figure([
        go.Scatter(x=pd.concat([fut["fecha"], fut["fecha"][::-1]]), y=pd.concat([fut["alto"], fut["bajo"][::-1]]),
                   fill="toself", fillcolor="rgba(92,154,27,.18)", line=dict(width=0), name="Banda 80 %",
                   hoverinfo="skip"),
        go.Scatter(x=hist["fecha"], y=hist["real"], name="Real", mode="lines", line=dict(width=2, color=c[0]),
                   hovertemplate="%{x|%d %b}: %{y:.0f}<extra>Real</extra>"),
        go.Scatter(x=fut["fecha"], y=fut["pronostico"], name="Pronóstico", mode="lines+markers",
                   line=dict(width=2, dash="dash", color=c[1]), marker=dict(size=8),
                   hovertemplate="%{x|%d %b}: %{y:.0f}<extra>Pronóstico</extra>"),
    ])
    fig.update_layout(hovermode="x unified")
    return style(fig, height, legend=True)


def sankey(links: pd.DataFrame, height: int = 460) -> go.Figure:
    nodes = pd.unique(pd.concat([links["origen"], links["destino"]]).astype(str))
    index = {n: i for i, n in enumerate(nodes)}
    stage_color = {"via_ingreso→clase_ingreso": 0, "clase_ingreso→servicio": 1, "servicio→capitulo_dx": 2}
    c = palette()
    link_colors = [_rgba(c[stage_color.get(s, 0)], 0.28) for s in links["etapa"]]
    node_colors = [c[3] if n in set(links.loc[links["etapa"] == "servicio→capitulo_dx", "destino"].astype(str))
                   else c[0] if n in set(links.loc[links["etapa"] == "via_ingreso→clase_ingreso", "origen"].astype(str))
                   else c[1] if n in {"Ambulatorio", "Hospitalario"} else c[2] for n in nodes]
    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(label=list(nodes), pad=14, thickness=14, color=node_colors, line=dict(width=0),
                  hovertemplate="%{label}: %{value:,.0f} ingresos<extra></extra>"),
        link=dict(source=links["origen"].astype(str).map(index), target=links["destino"].astype(str).map(index),
                  value=links["valor"], color=link_colors,
                  hovertemplate="%{source.label} → %{target.label}: %{value:,.0f}<extra></extra>"),
    ))
    return style(fig, height, legend=False)


def network(edges: pd.DataFrame, height: int = 520, seed: int = 7) -> go.Figure:
    """Force-directed bipartite graph especialidad ↔ área (Fruchterman–Reingold).

    Implemented with numpy to avoid a graph dependency; deterministic seed so
    the layout is stable across reruns.
    """
    left = sorted(edges["especialidad"].unique())
    right = sorted(edges["area_servicio"].unique())
    # A name can be both a specialty and an area (e.g. terapia respiratoria):
    # node ids are namespaced by type.
    left_idx = {name: i for i, name in enumerate(left)}
    right_idx = {name: len(left) + i for i, name in enumerate(right)}
    n = len(left) + len(right)
    rng = np.random.default_rng(seed)
    pos = rng.uniform(-1, 1, (n, 2))
    pos[: len(left), 0] -= 0.6
    pos[len(left):, 0] += 0.6
    src = edges["especialidad"].map(left_idx).to_numpy()
    dst = edges["area_servicio"].map(right_idx).to_numpy()
    w = np.log1p(edges["peso"].to_numpy())
    w = w / w.max()
    k = 1.2 / np.sqrt(max(n, 1))
    for step in range(220):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=-1) + 1e-6
        disp = ((k * k / dist**2)[..., None] * delta).sum(axis=1)            # repulsion
        d_edge = pos[src] - pos[dst]
        l_edge = np.linalg.norm(d_edge, axis=-1)[:, None] + 1e-6
        pull = d_edge * (l_edge / k) * w[:, None]                           # weighted attraction
        np.add.at(disp, src, -pull)
        np.add.at(disp, dst, pull)
        temp = 0.08 * (1 - step / 220) + 0.005
        length = np.linalg.norm(disp, axis=-1)[:, None] + 1e-9
        pos += disp / length * np.minimum(length, temp)
    vol_left = edges.groupby("especialidad")["peso"].sum().reindex(left).to_numpy()
    vol_right = edges.groupby("area_servicio")["peso"].sum().reindex(right).to_numpy()
    top = max(vol_left.max(), vol_right.max())
    size = 12 + 26 * np.sqrt(np.concatenate([vol_left, vol_right]) / top)

    c = palette()
    edge_x, edge_y = [], []
    for s, t in zip(src, dst):
        edge_x += [pos[s, 0], pos[t, 0], None]
        edge_y += [pos[s, 1], pos[t, 1], None]
    fig = go.Figure([
        go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(width=1, color=_rgba(c[0], 0.25)),
                   hoverinfo="skip", showlegend=False),
        go.Scatter(x=pos[: len(left), 0], y=pos[: len(left), 1], mode="markers+text", name="Especialidad",
                   marker=dict(size=size[: len(left)], color=c[0], line=dict(width=2, color="rgba(255,255,255,.8)")),
                   text=left, textposition="top center", textfont=dict(size=10),
                   customdata=vol_left,
                   hovertemplate="<b>%{text}</b><br>%{customdata:,.0f} servicios<extra>Especialidad</extra>"),
        go.Scatter(x=pos[len(left):, 0], y=pos[len(left):, 1], mode="markers+text", name="Área de servicio",
                   marker=dict(size=size[len(left):], color=c[1], symbol="diamond",
                               line=dict(width=2, color="rgba(255,255,255,.8)")),
                   text=right, textposition="bottom center", textfont=dict(size=10),
                   customdata=vol_right,
                   hovertemplate="<b>%{text}</b><br>%{customdata:,.0f} servicios<extra>Área</extra>"),
    ])
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return style(fig, height, legend=True)


def sunburst(df: pd.DataFrame, height: int = 460) -> go.Figure:
    import plotly.express as px

    fig = px.sunburst(df, path=["servicio", "capitulo_dx", "nombre_diagnostico"], values="n",
                      color_discrete_sequence=palette())
    fig.update_traces(insidetextorientation="radial", hovertemplate="%{label}<br>%{value:,.0f} ingresos<extra></extra>",
                      marker=dict(line=dict(width=1.5)))
    return style(fig, height, legend=False)


def from_spec(spec: dict, height: int = 260) -> go.Figure | None:
    """Build a figure from an agent chart spec (see core.agent.AgentAnswer)."""
    kind, data = spec.get("type"), spec.get("data")
    if data is None or len(data) == 0:
        return None
    if kind == "barh":
        return hbar(data.head(12), spec["x"], spec["y"], fmt=spec.get("format", "num"), height=height)
    if kind == "bar" and spec.get("triage"):
        return triage_bars(data, spec.get("y", "mediana"), height=height)
    if kind == "line":
        return lines(data, spec["x"], spec["y"], spec.get("color"), height=height)
    if kind == "donut":
        return donut(data[spec["x"]], data[spec["y"]], height=height)
    if kind == "heatmap":
        grid = data.copy()
        grid.columns = [f"Triage {int(c)}" for c in grid.columns]
        return heatmap(grid, fmt=".0f", height=height)
    if kind == "heatmap_raw":
        return heatmap(data, fmt=".1f", height=height)
    if kind == "forecast":
        return forecast(data, height=height)
    return None


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
