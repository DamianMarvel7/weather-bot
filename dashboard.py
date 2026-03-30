"""
Polymarket Weather Bot — Streamlit Dashboard

Run with:
    uv run streamlit run dashboard.py
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
MARKETS_DIR = ROOT / "data" / "markets"
BOT_STATE = ROOT / "data" / "bot_state.json"
CALIBRATION = ROOT / "data" / "calibration.json"
CONFIG_FILE = ROOT / "src" / "weatherbot" / "config.json"

# ── page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Weather Bot Dashboard",
    page_icon="🌤",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── clean light theme CSS ─────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* ── global ── */
    html, body, [class*="css"] {
        font-family: 'Inter', 'Segoe UI', sans-serif;
        font-size: 14px;
        color: #1a1a2e;
    }

    /* ── top header strip ── */
    .dash-header {
        background: linear-gradient(135deg, #1e3a5f 0%, #16213e 100%);
        border-radius: 12px;
        padding: 20px 28px;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .dash-title {
        font-size: 24px;
        font-weight: 700;
        color: #ffffff;
        letter-spacing: 0.5px;
    }
    .dash-subtitle {
        font-size: 12px;
        color: #90aecb;
        margin-top: 4px;
    }
    .live-badge {
        background: #22c55e;
        color: #fff;
        font-size: 11px;
        font-weight: 600;
        padding: 4px 12px;
        border-radius: 20px;
        letter-spacing: 1px;
    }

    /* ── KPI cards ── */
    [data-testid="metric-container"] {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    [data-testid="metric-container"] label {
        font-size: 11px !important;
        font-weight: 600 !important;
        color: #64748b !important;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-size: 26px !important;
        font-weight: 700 !important;
        color: #1e3a5f !important;
    }

    /* ── section cards ── */
    .section-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    }
    .section-title {
        font-size: 13px;
        font-weight: 700;
        color: #1e3a5f;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 14px;
        padding-bottom: 8px;
        border-bottom: 2px solid #e2e8f0;
    }

    /* ── position card ── */
    .pos-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #3b82f6;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
    }
    .pos-city { font-size: 13px; font-weight: 600; color: #1e3a5f; }
    .pos-bucket { font-size: 11px; color: #64748b; margin-top: 2px; }
    .pos-meta { font-size: 11px; color: #94a3b8; margin-top: 6px; display: flex; gap: 14px; }
    .tag { background: #eff6ff; color: #2563eb; padding: 2px 8px;
            border-radius: 4px; font-size: 10px; font-weight: 600; }
    .tag-green { background: #f0fdf4; color: #16a34a; }
    .tag-yellow { background: #fefce8; color: #ca8a04; }
    .tag-red { background: #fef2f2; color: #dc2626; }

    /* ── EV log row ── */
    .ev-row {
        padding: 10px 0;
        border-bottom: 1px solid #f1f5f9;
        font-size: 12px;
    }
    .ev-row:last-child { border-bottom: none; }
    .ev-time { font-size: 10px; color: #94a3b8; }
    .ev-location { font-weight: 600; color: #1e3a5f; }

    /* ── empty state ── */
    .empty-state {
        padding: 36px;
        text-align: center;
        color: #94a3b8;
        font-size: 13px;
        background: #f8fafc;
        border-radius: 8px;
        border: 1px dashed #cbd5e1;
    }

    /* ── info box (used for explanations) ── */
    .info-box {
        background: #eff6ff;
        border-left: 4px solid #3b82f6;
        border-radius: 0 8px 8px 0;
        padding: 12px 16px;
        margin-bottom: 12px;
        font-size: 12px;
        color: #1e40af;
        line-height: 1.6;
    }

    /* ── hide streamlit branding ── */
    #MainMenu, footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── data loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_all_markets() -> list[dict]:
    if not MARKETS_DIR.exists():
        return []
    markets = []
    for f in sorted(MARKETS_DIR.glob("*.json")):
        try:
            markets.append(json.loads(f.read_text()))
        except Exception:
            pass
    return markets


def load_bot_state() -> dict:
    if BOT_STATE.exists():
        try:
            return json.loads(BOT_STATE.read_text())
        except Exception:
            pass
    return {"balance": 0.0}


def load_calibration() -> dict:
    if CALIBRATION.exists():
        try:
            return json.loads(CALIBRATION.read_text())
        except Exception:
            pass
    return {}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


# ── derived data ─────────────────────────────────────────────────────────────

def split_markets(markets: list[dict]):
    open_m, resolved_m = [], []
    for m in markets:
        if m.get("status") == "resolved" or m.get("pnl") is not None:
            resolved_m.append(m)
        elif m.get("position") is not None:
            open_m.append(m)
    return open_m, resolved_m


def compute_portfolio_pnl(open_m, resolved_m, balance):
    realized = sum(m.get("pnl") or 0 for m in resolved_m)
    unrealized = 0.0
    for m in open_m:
        pos = m.get("position") or {}
        snaps = m.get("market_snapshots", [])
        cur_bid = snaps[-1].get("bid", pos.get("entry_ask", 0)) if snaps else pos.get("entry_ask", 0)
        size = pos.get("size", 0)
        entry = pos.get("entry_ask", 1)
        if entry > 0:
            unrealized += (size / entry) * cur_bid - size
    wins = sum(1 for m in resolved_m if (m.get("pnl") or 0) > 0)
    losses = sum(1 for m in resolved_m if (m.get("pnl") or 0) <= 0)
    return {
        "balance": balance,
        "realized": realized,
        "unrealized": unrealized,
        "wins": wins,
        "losses": losses,
        "total_resolved": len(resolved_m),
        "open_count": len(open_m),
    }


def balance_history_from_resolved(resolved_m, start_balance):
    trades = []
    for m in resolved_m:
        pos = m.get("position") or {}
        closed_at = pos.get("closed_at") or m.get("date")
        trades.append({"ts": closed_at, "pnl": m.get("pnl") or 0})
    if not trades:
        return pd.DataFrame(columns=["ts", "balance"])
    df = pd.DataFrame(trades).sort_values("ts").reset_index(drop=True)
    df["balance"] = start_balance + df["pnl"].cumsum()
    return df


# ── load data ─────────────────────────────────────────────────────────────────

markets   = load_all_markets()
state     = load_bot_state()
calib     = load_calibration()
cfg       = load_config()

open_m, resolved_m = split_markets(markets)
pf = compute_portfolio_pnl(open_m, resolved_m, state.get("balance", 0))

starting_balance = cfg.get("balance", 10_000.0)
total_pnl  = pf["realized"] + pf["unrealized"]
win_rate   = (pf["wins"] / pf["total_resolved"] * 100) if pf["total_resolved"] else 0


# ── header ────────────────────────────────────────────────────────────────────

now_str = datetime.utcnow().strftime("%d %b %Y · %H:%M UTC")
st.markdown(
    f"""
    <div class="dash-header">
      <div>
        <div class="dash-title">🌤 Weather Bet Dashboard</div>
        <div class="dash-subtitle">Polymarket · Kelly Criterion · Open-Meteo · EV Analysis</div>
      </div>
      <div style="text-align:right">
        <div class="live-badge">● LIVE</div>
        <div style="color:#90aecb;font-size:11px;margin-top:6px">{now_str}</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

col_refresh = st.columns([6, 1])[1]
with col_refresh:
    if st.button("↺ Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── KPI row ───────────────────────────────────────────────────────────────────

c1, c2, c3, c4, c5, c6 = st.columns(6)

with c1:
    st.metric("💰 Balance", f"${pf['balance']:,.2f}")
with c2:
    sign = "+" if total_pnl >= 0 else ""
    st.metric("📈 Total PnL", f"${total_pnl:,.2f}", delta=f"{sign}{total_pnl:.2f}")
with c3:
    st.metric("📂 Open Positions", str(pf["open_count"]))
with c4:
    wr = f"{win_rate:.0f}%" if pf["total_resolved"] else "—"
    st.metric("🎯 Win Rate", wr, delta=f"{pf['wins']}W  {pf['losses']}L")
with c5:
    st.metric("✅ Resolved Trades", str(pf["total_resolved"]))
with c6:
    unreal = f"${pf['unrealized']:+.2f}" if open_m else "—"
    st.metric("⏳ Unrealized PnL", unreal)

st.markdown("<br>", unsafe_allow_html=True)


# ── balance chart + open positions ───────────────────────────────────────────

chart_col, pos_col = st.columns([3, 2])

with chart_col:
    st.markdown('<div class="section-card"><div class="section-title">📊 Balance History</div>', unsafe_allow_html=True)
    hist_df = balance_history_from_resolved(resolved_m, starting_balance)
    if not hist_df.empty:
        peak   = hist_df["balance"].max()
        trough = hist_df["balance"].min()
        line_color = "#22c55e" if total_pnl >= 0 else "#ef4444"
        fill_color = "rgba(34,197,94,0.10)" if total_pnl >= 0 else "rgba(239,68,68,0.10)"

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist_df["ts"],
            y=hist_df["balance"],
            mode="lines+markers",
            line=dict(color=line_color, width=2.5),
            marker=dict(size=5, color=line_color),
            fill="tozeroy",
            fillcolor=fill_color,
            hovertemplate="<b>$%{y:,.2f}</b><br>%{x}<extra></extra>",
        ))
        # reference line at starting balance
        fig.add_hline(
            y=starting_balance,
            line_dash="dot",
            line_color="#94a3b8",
            annotation_text=f"Start ${starting_balance:,.0f}",
            annotation_font_size=10,
            annotation_font_color="#64748b",
        )
        fig.update_layout(
            paper_bgcolor="#ffffff",
            plot_bgcolor="#f8fafc",
            font=dict(family="Inter, Segoe UI, sans-serif", color="#64748b", size=11),
            height=280,
            margin=dict(l=10, r=10, t=10, b=40),
            xaxis=dict(gridcolor="#e2e8f0", showgrid=True, zeroline=False, title=""),
            yaxis=dict(gridcolor="#e2e8f0", showgrid=True, zeroline=False, tickprefix="$"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        # quick stats below chart
        s1, s2, s3 = st.columns(3)
        s1.metric("Peak Balance", f"${peak:,.2f}")
        s2.metric("Trough Balance", f"${trough:,.2f}")
        s3.metric("Net Change", f"${hist_df['balance'].iloc[-1] - starting_balance:+,.2f}")
    else:
        st.markdown('<div class="empty-state">No resolved trades yet — balance history will appear here once trades close.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

with pos_col:
    count_label = f"{pf['open_count']} active" if pf["open_count"] else "none"
    st.markdown(f'<div class="section-card"><div class="section-title">📌 Open Positions &nbsp;<span style="font-weight:400;color:#64748b;font-size:11px">({count_label})</span></div>', unsafe_allow_html=True)

    if open_m:
        for m in open_m:
            pos = m.get("position") or {}
            city  = m.get("city_name", m.get("city", ""))
            date  = m.get("date", "")
            bucket = pos.get("bucket", "?")
            ev     = pos.get("ev", 0)
            kelly  = pos.get("kelly", 0)
            size   = pos.get("size", 0)
            entry  = pos.get("entry_ask", 0)

            snaps   = m.get("market_snapshots", [])
            cur_bid = snaps[-1].get("bid", entry) if snaps else entry
            pct     = (cur_bid - entry) / entry * 100 if entry else 0
            pct_color = "#16a34a" if pct >= 0 else "#dc2626"
            left_border = "#22c55e" if pct >= 0 else "#ef4444"

            ev_tag_cls = "tag-green" if ev > 0.05 else "tag-yellow" if ev > 0 else "tag-red"

            st.markdown(
                f"""
                <div class="pos-card" style="border-left-color:{left_border}">
                  <div style="display:flex;justify-content:space-between;align-items:center">
                    <div class="pos-city">🌆 {city}</div>
                    <div style="font-size:13px;font-weight:700;color:{pct_color}">{pct:+.1f}%</div>
                  </div>
                  <div class="pos-bucket">📅 {date} &nbsp;·&nbsp; 🎯 {bucket}</div>
                  <div style="background:#e2e8f0;border-radius:4px;height:4px;margin:8px 0">
                    <div style="width:{min(100, cur_bid*100):.0f}%;height:100%;background:{left_border};border-radius:4px"></div>
                  </div>
                  <div class="pos-meta">
                    <span class="tag {ev_tag_cls}">EV {ev:+.3f}</span>
                    <span class="tag">Kelly {kelly*100:.1f}%</span>
                    <span class="tag">Size ${size:.2f}</span>
                    <span class="tag">Entry {entry:.3f}</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="empty-state">No open positions right now.<br>The bot will open trades when it finds positive EV opportunities.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── trade history + EV log ────────────────────────────────────────────────────

trade_col, ev_col = st.columns(2)

with trade_col:
    st.markdown('<div class="section-card"><div class="section-title">📋 Trade History  <span style="font-weight:400;color:#64748b;font-size:10px">(last 20 resolved)</span></div>', unsafe_allow_html=True)

    if resolved_m:
        rows = []
        for m in reversed(resolved_m[-20:]):
            pos = m.get("position") or {}
            pnl = m.get("pnl") or 0
            rows.append({
                "City":    m.get("city_name", m.get("city", "")),
                "Date":    m.get("date", ""),
                "Bucket":  pos.get("bucket", "?"),
                "Entry":   round(pos.get("entry_ask", 0), 3),
                "Size $":  round(pos.get("size", 0), 2),
                "PnL $":   round(pnl, 2),
                "Exit":    pos.get("close_reason", "—"),
                "EV":      round(pos.get("ev", 0), 3),
            })
        df_trades = pd.DataFrame(rows)

        def _pnl_bg(val):
            try:
                v = float(val)
                if v > 0:
                    return "background-color: #f0fdf4; color: #16a34a; font-weight: 600"
                elif v < 0:
                    return "background-color: #fef2f2; color: #dc2626; font-weight: 600"
            except Exception:
                pass
            return ""

        styled = df_trades.style.applymap(_pnl_bg, subset=["PnL $"])
        st.dataframe(styled, use_container_width=True, height=340, hide_index=True)
    else:
        st.markdown('<div class="empty-state">No resolved trades yet.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

with ev_col:
    st.markdown('<div class="section-card"><div class="section-title">🧮 EV / Kelly Entry Log  <span style="font-weight:400;color:#64748b;font-size:10px">(entries only)</span></div>', unsafe_allow_html=True)

    # Explain what EV and Kelly mean
    st.markdown(
        """
        <div class="info-box">
          <b>EV (Expected Value)</b> = how much profit we expect per $1 risked.<br>
          A positive EV (e.g. +0.08) means the market is mispriced in our favour.<br>
          <b>Kelly %</b> = optimal fraction of bankroll to bet, scaled by confidence.<br>
          <b>Size $</b> = actual USD staked (capped by <code>max_bet</code> in config).
        </div>
        """,
        unsafe_allow_html=True,
    )

    entries = [m for m in reversed(markets) if m.get("position") is not None][:12]
    if entries:
        for m in entries:
            pos = m.get("position") or {}
            city  = m.get("city_name", m.get("city", ""))
            date  = m.get("date", "")
            ev    = pos.get("ev", 0)
            kelly = pos.get("kelly", 0)
            size  = pos.get("size", 0)
            opened = (pos.get("opened_at") or "")[:16].replace("T", " ")

            snaps   = m.get("forecast_snapshots", [])
            best_fc = snaps[-1].get("best") if snaps else None
            src     = snaps[-1].get("best_source", "") if snaps else ""
            fc_str  = f"{best_fc:.1f}° ({src})" if best_fc is not None else "—"

            ev_color = "#16a34a" if ev > 0 else "#dc2626"
            ev_bg    = "#f0fdf4" if ev > 0 else "#fef2f2"

            st.markdown(
                f"""
                <div class="ev-row">
                  <div class="ev-time">{opened}</div>
                  <div class="ev-location">{city} &nbsp;·&nbsp; {date}</div>
                  <div style="margin-top:4px;font-size:11px;color:#475569">
                    Bucket: <b>{pos.get("bucket","?")}</b> &nbsp;·&nbsp; Forecast: <b>{fc_str}</b>
                  </div>
                  <div style="margin-top:5px;display:flex;gap:8px;flex-wrap:wrap">
                    <span style="background:{ev_bg};color:{ev_color};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">
                      EV {ev:+.3f}
                    </span>
                    <span class="tag">Kelly {kelly*100:.1f}%</span>
                    <span class="tag tag-green">Size ${size:.2f}</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="empty-state">No entries yet.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── calibration ───────────────────────────────────────────────────────────────

st.markdown('<div class="section-card"><div class="section-title">🎓 Forecast Calibration  <span style="font-weight:400;color:#64748b;font-size:10px">— MAE per city / source (lower = better)</span></div>', unsafe_allow_html=True)

st.markdown(
    """
    <div class="info-box">
      <b>MAE (Mean Absolute Error)</b> is how many degrees our forecast model was off on average.<br>
      <b>N</b> = number of historical days used. More data → more reliable calibration.<br>
      Sources: <b>ECMWF</b> = European model (most accurate long-range), <b>HRRR</b> = US high-res short-range, <b>METAR</b> = live airport observations.
    </div>
    """,
    unsafe_allow_html=True,
)

if calib:
    calib_rows = []
    for key, val in sorted(calib.items()):
        parts = key.rsplit("_", 1)
        city_key = parts[0] if len(parts) == 2 else key
        source   = parts[1].upper() if len(parts) == 2 else "?"
        mae      = val.get("mae", 0)
        calib_rows.append({
            "City":   city_key.replace("_", " ").title(),
            "Source": source,
            "MAE °":  round(mae, 2),
            "N (days)": val.get("n", 0),
            "Quality": "✅ Good" if mae < 2.5 else "⚠️ Fair" if mae < 4 else "❌ Poor",
        })
    df_calib = pd.DataFrame(calib_rows)
    st.dataframe(df_calib, use_container_width=True, height=300, hide_index=True)
else:
    st.markdown(
        '<div class="empty-state">No calibration data yet.<br>Run <code>uv run src/weatherbot/backfill.py</code> to generate historical calibration.</div>',
        unsafe_allow_html=True,
    )

st.markdown('</div>', unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)


# ── config + market browser ───────────────────────────────────────────────────

cfg_col, browser_col = st.columns(2)

with cfg_col:
    with st.expander("⚙️  Bot Configuration", expanded=False):
        st.markdown(
            """
            <div class="info-box">
              These are the trading rules the bot follows. Change them in <code>src/weatherbot/config.json</code>.
            </div>
            """,
            unsafe_allow_html=True,
        )
        if cfg:
            explanations = {
                "balance":          "Starting bankroll (USD)",
                "max_bet":          "Max USD per single trade",
                "min_ev":           "Minimum EV edge required to enter",
                "max_price":        "Won't buy above this price (e.g. 0.45 = 45¢)",
                "min_price":        "Won't buy below this price",
                "min_volume":       "Skip markets with less liquidity",
                "min_hours":        "Skip if market closes in less than this many hours",
                "max_hours":        "Skip if market closes in more than this many hours",
                "kelly_fraction":   "Fractional Kelly multiplier (0.25 = 25% of full Kelly)",
                "max_slippage":     "Max allowed bid-ask spread",
                "scan_interval":    "How often the bot scans (seconds)",
                "calibration_min":  "Min resolved trades before using calibration",
                "vc_key":           "Visual Crossing API key (for actual temps)",
            }
            cfg_rows = [{"Parameter": k, "Value": str(v), "What it means": explanations.get(k, "")} for k, v in cfg.items()]
            st.dataframe(pd.DataFrame(cfg_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("config.json not found.")

with browser_col:
    with st.expander("🔍  Market Browser", expanded=False):
        if markets:
            cities = sorted({m.get("city_name", m.get("city", "")) for m in markets})
            sel_city = st.selectbox("Filter by city", ["All"] + cities)
            filtered = [
                m for m in markets
                if sel_city == "All" or m.get("city_name", m.get("city")) == sel_city
            ]
            for m in sorted(filtered, key=lambda x: x.get("date", ""), reverse=True)[:10]:
                pos = m.get("position") or {}
                status = m.get("status", "?")
                pnl_v  = m.get("pnl")
                pnl_str = f"PnL: {pnl_v:+.2f}" if pnl_v is not None else ""
                badge_color  = "#22c55e" if status == "open" else "#64748b"
                badge_bg     = "#f0fdf4" if status == "open" else "#f1f5f9"
                st.markdown(
                    f"""
                    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                                padding:10px 14px;margin-bottom:8px;font-size:12px;">
                      <div style="display:flex;justify-content:space-between;align-items:center">
                        <b style="color:#1e3a5f">{m.get("city_name","?")} &nbsp;·&nbsp; {m.get("date","")}</b>
                        <span style="background:{badge_bg};color:{badge_color};padding:2px 8px;
                                     border-radius:10px;font-size:10px;font-weight:600">{status.upper()}</span>
                      </div>
                      <div style="color:#64748b;margin-top:4px">{m.get("event","")[:70]}</div>
                      {"<div style='margin-top:4px;color:#2563eb;font-size:11px'>📌 " + pos.get("bucket","") + " · $" + str(pos.get("size",0)) + "</div>" if pos else ""}
                      {"<div style='margin-top:2px;font-weight:600;color:" + ("#16a34a" if (pnl_v or 0) > 0 else "#dc2626") + "'>" + pnl_str + "</div>" if pnl_str else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No market files found in data/markets/.")


# ── footer ────────────────────────────────────────────────────────────────────

st.markdown("<br>", unsafe_allow_html=True)
st.markdown(
    """
    <div style="text-align:center;padding:16px;color:#94a3b8;font-size:11px;
                border-top:1px solid #e2e8f0;margin-top:8px">
      🌤 Weather Bet Dashboard &nbsp;·&nbsp; Polymarket &nbsp;·&nbsp; Kelly Criterion &nbsp;·&nbsp;
      Auto-refreshes every 30s &nbsp;·&nbsp;
      <code>uv run streamlit run dashboard.py</code>
    </div>
    """,
    unsafe_allow_html=True,
)
