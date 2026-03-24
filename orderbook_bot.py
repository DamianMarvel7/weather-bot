"""
orderbook_bot.py - Polymarket order-book simulation engine.

Models implemented
------------------
  I.   Kyle's Lambda          - price-impact / informed-trading detection (OLS)
  II.  Avellaneda-Stoikov MM  - optimal bid/ask with inventory management
  III. Hawkes Process         - self-exciting order arrivals (clustering)
  IV.  Almgren-Chriss         - optimal execution schedule for large positions
  V.   Full MM simulation     - 100-session Monte Carlo with resolution P&L
  VI.  VPIN                   - Volume-Synchronized Probability of Informed Trading
       Dynamic Hedging        - correlated-contract hedge to prevent inventory spiral

Spread protection layers (applied multiplicatively)
----------------------------------------------------
  1. Kyle's lambda  - widens when R² > 0.15 (informed flow detected by OLS)
  2. Hawkes factor  - widens when local arrival rate >> baseline (hot market)
  3. VPIN factor    - widens 2x at VPIN > 0.65, pulls quotes at VPIN > 0.80

Outputs
-------
  data/processed/orderbook_sim.png      - 2x3 main simulation plot
  data/processed/orderbook_analysis.png - 2x2 Monte Carlo + VPIN analysis

Usage
-----
    uv run orderbook_bot.py
    uv run orderbook_bot.py --ticks 3000 --informed-frac 0.40
    uv run orderbook_bot.py --exec-size 25000
    uv run orderbook_bot.py --mc-sessions 200
    uv run orderbook_bot.py --calibrate

Disclaimer: For research / simulation only.  Not financial advice.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.orderbook.execution import AlmgrenChrissParams, compare_risk_aversions
from src.orderbook.hawkes import HawkesParams, compute_intensity, fit_hawkes, simulate_hawkes
from src.orderbook.hedger import DynamicHedger, HedgeContract
from src.orderbook.kyle_model import RollingKyleLambda, estimate_kyle_lambda
from src.orderbook.market_maker import AvellanedaStoikovMM
from src.orderbook.monte_carlo import simulate_prediction_market
from src.orderbook.vpin import RollingVPIN, SpreadAction, compute_vpin


# ── simulation helpers ────────────────────────────────────────────────────────

def _generate_true_path(p0, n_ticks, vol_per_tick, rng):
    logit0 = np.log(p0 / (1.0 - p0))
    shocks = rng.standard_normal(n_ticks) * vol_per_tick
    return 1.0 / (1.0 + np.exp(-np.concatenate([[logit0], logit0 + np.cumsum(shocks)])))


def _simulate_order_flow(true_path, informed_frac, rng):
    n = len(true_path) - 1
    volumes = rng.exponential(500.0, size=n)
    true_direction = np.sign(np.diff(true_path))
    signs = np.where(
        rng.random(n) < informed_frac,
        np.where(true_direction != 0, true_direction, 1.0),
        rng.choice([-1.0, 1.0], size=n),
    )
    return volumes, signs


def _hawkes_tick_times(hawkes, n_ticks, T_hours, seed):
    avg = hawkes.avg_intensity
    T_target = n_ticks / avg * 2.0
    times = simulate_hawkes(hawkes.mu, hawkes.alpha, hawkes.beta, T_target, seed=seed)
    if len(times) < n_ticks:
        times = simulate_hawkes(hawkes.mu, hawkes.alpha, hawkes.beta, T_target * 3.0,
                                seed=seed + 1)
    times = times[:n_ticks]
    return times / times[-1] * T_hours


def _branching_spread_factor(tick_times, tick, window_ticks, fitted_params):
    lo = max(0, tick - window_ticks)
    local_times = tick_times[lo: tick + 1]
    if len(local_times) < 5:
        return 1.0
    dur = local_times[-1] - local_times[0]
    if dur < 1e-6:
        return 1.0
    return float(np.clip(np.sqrt(len(local_times) / dur / max(fitted_params.avg_intensity, 1e-6)), 1.0, 2.0))


# ── Part V: session Monte Carlo ───────────────────────────────────────────────

def run_session(
    true_prob: float,
    n_steps: int,
    T_hours: float,
    gamma: float,
    kappa: float,
    sigma: float,
    rng: np.random.Generator,
) -> dict:
    """
    One independent trading session: mean-reverting price, random takers,
    binary resolution at T.  Matches the Part V article simulation.
    """
    mm = AvellanedaStoikovMM(gamma=gamma, kappa=kappa, sigma=sigma)
    price = 0.50
    prices = [price]
    spreads_s = []
    pnl_hist = []

    for step in range(n_steps):
        t_rem = max(T_hours * (1 - step / n_steps), 1e-3)
        t_q = min(t_rem, 1.0)

        # Mean-reverting price walk
        drift = 0.001 * (true_prob - price)
        shock = rng.normal(0, 0.008)
        price = float(np.clip(price + drift + shock, 0.01, 0.99))
        prices.append(price)

        q = mm.quote(mid_price=price, time_to_resolution=t_q)
        spreads_s.append(q.spread)

        # Taker arrival: probability inversely proportional to spread
        trade_prob = min(0.3, 0.05 / (q.spread + 1e-6))
        if rng.random() < trade_prob:
            size = rng.exponential(200.0)
            side = "sell" if rng.random() < 0.5 else "buy"
            mm.fill(side, q.bid if side == "sell" else q.ask, size)

        pnl_hist.append(mm.mark_to_market(price))

    # Binary resolution
    final_price = 1.0 if rng.random() < true_prob else 0.0
    final_pnl = mm.mark_to_market(final_price)

    return {
        "final_pnl": final_pnl,
        "final_inventory": mm.inventory,
        "avg_spread": float(np.mean(spreads_s)) if spreads_s else 0.0,
        "n_fills": len(mm.trades),
        "pnl_history": pnl_hist,
    }


def run_monte_carlo_sessions(
    n_sessions: int = 100,
    true_prob: float = 0.65,
    n_steps: int = 1_000,
    T_hours: float = 24.0,
    gamma: float = 0.10,
    kappa: float = 100.0,
    sigma: float = 0.05,
    seed: int | None = 42,
) -> dict:
    """
    Part V: run N independent trading sessions and aggregate P&L statistics.
    """
    rng = np.random.default_rng(seed)
    sessions = [
        run_session(true_prob, n_steps, T_hours, gamma, kappa, sigma, rng)
        for _ in range(n_sessions)
    ]
    pnls = np.array([s["final_pnl"] for s in sessions])
    spreads = np.array([s["avg_spread"] for s in sessions])
    fills = np.array([s["n_fills"] for s in sessions])

    # Align pnl histories to shortest session for matrix plot
    min_len = min(len(s["pnl_history"]) for s in sessions)
    pnl_matrix = np.array([s["pnl_history"][:min_len] for s in sessions])

    return {
        "pnls": pnls,
        "spreads": spreads,
        "fills": fills,
        "pnl_matrix": pnl_matrix,
        "n_sessions": n_sessions,
        "true_prob": true_prob,
    }


# ── main simulation loop ──────────────────────────────────────────────────────

def run_simulation(
    p0: float = 0.62,
    n_ticks: int = 2_000,
    vol_per_tick: float = 0.008,
    informed_frac: float = 0.15,
    true_lambda: float = 0.0015,
    gamma: float = 0.10,
    kappa: float = 100.0,
    sigma_mm: float = 0.02,
    max_inventory: float = 100.0,
    kyle_window: int = 200,
    days_to_resolution: float = 7.0,
    hawkes_mu: float = 5.0,
    hawkes_alpha: float = 3.5,
    hawkes_beta: float = 5.0,
    exec_size: float = 10_000.0,
    seed: int | None = 42,
) -> dict:
    """
    Full simulation with Kyle + Hawkes + A-S MM + VPIN + hedging.
    Returns dict of time-series and model results.
    """
    rng = np.random.default_rng(seed)
    T_hours = days_to_resolution * 24.0

    # ── Hawkes arrival times ──────────────────────────────────────────────────
    hawkes_params = HawkesParams(mu=hawkes_mu, alpha=hawkes_alpha, beta=hawkes_beta)
    tick_times = _hawkes_tick_times(hawkes_params, n_ticks, T_hours, seed=seed or 0)
    hawkes_fit = fit_hawkes(tick_times, T=T_hours, n_restarts=8, seed=seed)

    # ── True path + order flow ────────────────────────────────────────────────
    true_path = _generate_true_path(p0, n_ticks, vol_per_tick, rng)
    volumes, signs = _simulate_order_flow(true_path, informed_frac, rng)

    # ── Models ────────────────────────────────────────────────────────────────
    mm   = AvellanedaStoikovMM(gamma=gamma, kappa=kappa, sigma=sigma_mm,
                               max_inventory=max_inventory)
    rkl  = RollingKyleLambda(window=kyle_window)
    rvp  = RollingVPIN(window=kyle_window, min_ticks=50)

    # ── Tracking ──────────────────────────────────────────────────────────────
    mid_prices  = [p0]
    bids        = [p0 - 0.02]
    asks        = [p0 + 0.02]
    lambdas     = [0.0]
    inventories = [0.0]
    mtm_pnl     = [0.0]
    spreads     = [0.04]
    vpin_series = [0.0]
    vpin_mult   = [1.0]
    hf_series   = [1.0]
    quotes_pulled = 0

    current_mid = p0

    for tick in range(n_ticks):
        t_elapsed   = tick_times[tick]
        t_remaining = max(T_hours - t_elapsed, 1e-3) / 24.0
        t_quote     = min(t_remaining, 1.0)

        # ── Kyle's lambda ─────────────────────────────────────────────────────
        kyle_res = rkl.update(current_mid, volumes[tick], signs[tick])
        lam = kyle_res.lambda_ if kyle_res else 0.0
        lambdas.append(lam)

        # ── Hawkes spread factor ──────────────────────────────────────────────
        hf = _branching_spread_factor(tick_times, tick, kyle_window, hawkes_fit.params)
        hf_series.append(hf)

        # ── VPIN ──────────────────────────────────────────────────────────────
        signed_vol = volumes[tick] * signs[tick]
        vpin_res = rvp.update(signed_vol)
        vp  = vpin_res.vpin if vpin_res else 0.0
        vm  = vpin_res.spread_multiplier if vpin_res else 1.0
        vpin_series.append(vp)
        vpin_mult.append(vm)

        # ── Quote ─────────────────────────────────────────────────────────────
        if vpin_res and vpin_res.action == SpreadAction.PULL:
            # VPIN > 0.80: pull quotes entirely - no fill this tick
            quotes_pulled += 1
            bids.append(current_mid - 0.10)
            asks.append(current_mid + 0.10)
            spreads.append(0.20)
        else:
            effective_kyle = max(lam, 0.0) * hf * vm
            q = mm.quote(mid_price=current_mid, time_to_resolution=t_quote,
                         kyle_lambda=effective_kyle)
            bids.append(q.bid)
            asks.append(q.ask)
            spreads.append(q.spread)

            # Fill
            side = "buy" if signs[tick] > 0 else "sell"
            fill_price = q.ask if side == "buy" else q.bid
            fill_prob = np.exp(-abs(fill_price - current_mid) / (q.spread + 1e-6))
            if rng.random() < fill_prob:
                mm.fill(side, fill_price, size=volumes[tick] / 1_000.0)

        # ── Price update ──────────────────────────────────────────────────────
        true_impact = true_lambda * signs[tick] * volumes[tick] / 1_000.0
        noise = rng.normal(0, vol_per_tick * 0.3)
        current_mid = float(np.clip(current_mid + true_impact + noise, 0.01, 0.99))

        mid_prices.append(current_mid)
        inventories.append(mm.inventory)
        mtm_pnl.append(mm.mark_to_market(current_mid))

    # ── Retrospective models ──────────────────────────────────────────────────
    mc = simulate_prediction_market(
        current_price=current_mid,
        volatility=vol_per_tick * np.sqrt(1_440),
        time_to_resolution=max(days_to_resolution * 0.05, 0.1),
        n_sims=10_000, seed=seed,
    )
    full_kyle = estimate_kyle_lambda(
        np.array(mid_prices),
        np.append(volumes, volumes[-1]),
        np.append(signs, signs[-1]),
    )

    # ── Full-series VPIN (bucket-based, for reference) ────────────────────────
    buy_vols  = np.where(signs > 0, volumes, 0.0)
    sell_vols = np.where(signs < 0, volumes, 0.0)
    full_vpin = compute_vpin(buy_vols, sell_vols, bucket_size=50)

    # ── Almgren-Chriss schedules ──────────────────────────────────────────────
    ac_params = AlmgrenChrissParams(
        total_shares=exec_size, T=4.0, N=8,
        sigma=sigma_mm, eta=1e-7, gamma=5e-7, risk_aversion=1e-6,
    )
    ac_schedules = compare_risk_aversions(ac_params, [1e-7, 1e-6, 1e-5, 1e-4])

    # ── Hedge plan for final inventory ────────────────────────────────────────
    hedger = DynamicHedger(hedge_fraction=0.80)
    hedge_contracts = [
        HedgeContract("Corr-A (rho=0.85)", correlation=0.85, mid_price=0.58, capacity=8_000.0),
        HedgeContract("Corr-B (rho=0.60)", correlation=0.60, mid_price=0.45, capacity=5_000.0),
    ]
    hedge_plan = hedger.compute_plan(
        primary_inventory=mm.inventory * current_mid,   # convert to $
        hedge_contracts=hedge_contracts,
    )

    return {
        "true_path": true_path,
        "mid_prices": np.array(mid_prices),
        "bids": np.array(bids),
        "asks": np.array(asks),
        "spreads": np.array(spreads),
        "lambdas": np.array(lambdas),
        "hf_series": np.array(hf_series),
        "inventories": np.array(inventories),
        "mtm_pnl": np.array(mtm_pnl),
        "vpin_series": np.array(vpin_series),
        "vpin_mult": np.array(vpin_mult),
        "full_vpin": full_vpin,
        "tick_times": tick_times,
        "hawkes_fit": hawkes_fit,
        "hawkes_params": hawkes_params,
        "mc_result": mc,
        "kyle_result": full_kyle,
        "ac_schedules": ac_schedules,
        "ac_params": ac_params,
        "hedge_plan": hedge_plan,
        "quotes_pulled": quotes_pulled,
        "n_ticks": n_ticks,
        "informed_frac": informed_frac,
        "final_mid": current_mid,
        "T_hours": T_hours,
        "mm": mm,
    }


# ── main simulation plot (2x3) ────────────────────────────────────────────────

def plot_simulation(results: dict, output_path: str) -> None:
    """
    2x3 plot:
      [0,0] Price path + bid/ask band
      [0,1] Kyle's lambda + VPIN threshold zones
      [0,2] Hawkes intensity + event rug
      [1,0] MM inventory + VPIN multiplier (twin axis)
      [1,1] MtM P&L + MC terminal distribution
      [1,2] Almgren-Chriss execution schedules
    """
    n = results["n_ticks"] + 1
    ticks = np.arange(n)
    fit = results["hawkes_fit"]
    T_h = results["T_hours"]

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(
        f"Polymarket Order-Book Simulation  |  {results['n_ticks']:,} ticks  "
        f"|  {results['informed_frac']:.0%} informed  "
        f"|  Hawkes rho={fit.params.branching_ratio:.2f}  "
        f"|  Quotes pulled={results['quotes_pulled']}",
        fontsize=11, fontweight="bold",
    )

    # [0,0] Price path ─────────────────────────────────────────────────────────
    ax = axes[0, 0]
    ax.fill_between(ticks, results["bids"], results["asks"],
                    alpha=0.15, color="steelblue", label="Bid-Ask band")
    ax.plot(ticks, results["mid_prices"], color="steelblue", lw=1.2, label="MM mid")
    ax.plot(np.linspace(0, n - 1, len(results["true_path"])),
            results["true_path"], color="crimson", lw=1.0, ls="--", alpha=0.7,
            label="True prob")
    ax.axhline(0.5, color="gray", lw=0.7, ls=":")
    ax.set_title("Price Path & Quotes")
    ax.set_ylabel("YES probability")
    ax.set_xlabel("Tick")
    ax.set_ylim(0, 1)
    ax.legend(fontsize=7)

    # [0,1] Kyle's lambda + VPIN zones ────────────────────────────────────────
    ax = axes[0, 1]
    lam = results["lambdas"]
    thresh = np.percentile(lam[lam > 0], 90) if (lam > 0).any() else 1e-5
    ax.plot(ticks, lam, color="darkorange", lw=0.8, label="Kyle lambda (rolling)")
    ax.axhline(thresh, color="red", lw=1.0, ls="--",
               label=f"90th pctile ({thresh:.5f})")
    ax.fill_between(ticks, lam, thresh, where=(lam > thresh),
                    alpha=0.3, color="red", label="Informed-flow zone")
    # VPIN multiplier on twin axis
    ax2 = ax.twinx()
    ax2.plot(ticks, results["vpin_mult"], color="purple", lw=0.8, alpha=0.6,
             label="VPIN spread mult")
    ax2.axhline(2.0, color="purple", lw=0.6, ls=":", alpha=0.5)
    ax2.set_ylabel("VPIN spread mult", color="purple", fontsize=8)
    ax2.tick_params(axis="y", labelcolor="purple", labelsize=7)
    ax2.set_ylim(0.8, 3.5)
    lines1, lbl1 = ax.get_legend_handles_labels()
    lines2, lbl2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, lbl1 + lbl2, fontsize=6, loc="upper left")
    ax.set_title("Kyle Lambda & VPIN Spread Multiplier")
    ax.set_ylabel("Kyle lambda")
    ax.set_xlabel("Tick")

    # [0,2] Hawkes intensity ───────────────────────────────────────────────────
    ax = axes[0, 2]
    tick_times = results["tick_times"]
    t_eval = np.linspace(tick_times[0], tick_times[-1], 400)
    intensity = compute_intensity(tick_times, fit.params, t_eval)
    ax.plot(t_eval, intensity, color="teal", lw=1.2, label="Fitted lambda(t)")
    ax.axhline(fit.params.mu, color="gray", lw=0.8, ls="--",
               label=f"Baseline mu={fit.params.mu:.2f}")
    ax.axhline(fit.params.avg_intensity, color="navy", lw=0.8, ls=":",
               label=f"Mean={fit.params.avg_intensity:.2f}")
    ax.plot(tick_times, np.zeros_like(tick_times), "|", color="teal",
            alpha=0.15, markersize=4)
    ax.set_title(
        f"Hawkes Arrivals  (rho={fit.params.branching_ratio:.2f}  "
        f"{fit._interpret().split(' -')[0]})"
    )
    ax.set_ylabel("Intensity (events/hour)")
    ax.set_xlabel("Time (hours)")
    ax.legend(fontsize=7)

    # [1,0] Inventory + VPIN twin axis ─────────────────────────────────────────
    ax = axes[1, 0]
    inv = results["inventories"]
    vpin = results["vpin_series"]
    lim = results["mm"].max_inventory

    ax.plot(ticks, inv, color="purple", lw=1.0, label="Inventory")
    ax.axhline(0, color="black", lw=0.7)
    ax.axhline(lim * 0.8, color="red", lw=0.7, ls="--", alpha=0.5)
    ax.axhline(-lim * 0.8, color="red", lw=0.7, ls="--", alpha=0.5)
    # Shade high-VPIN periods (danger zone)
    danger = vpin > 0.65
    ax.fill_between(ticks, inv.min() * 1.1, inv.max() * 1.1,
                    where=danger, alpha=0.10, color="red",
                    label="VPIN danger zone")
    ax3 = ax.twinx()
    ax3.plot(ticks, vpin, color="firebrick", lw=0.8, alpha=0.7, label="VPIN")
    ax3.axhline(0.65, color="firebrick", lw=0.8, ls="--", alpha=0.5,
                label="Widen (0.65)")
    ax3.axhline(0.80, color="darkred", lw=0.8, ls=":", alpha=0.5,
                label="Pull (0.80)")
    ax3.set_ylabel("VPIN", color="firebrick", fontsize=8)
    ax3.tick_params(axis="y", labelcolor="firebrick", labelsize=7)
    ax3.set_ylim(0, 1)
    lines_a, lbl_a = ax.get_legend_handles_labels()
    lines_b, lbl_b = ax3.get_legend_handles_labels()
    ax.legend(lines_a + lines_b, lbl_a + lbl_b, fontsize=6)
    ax.set_title("MM Inventory & VPIN")
    ax.set_ylabel("Net position (contracts)")
    ax.set_xlabel("Tick")

    # [1,1] P&L + MC distribution ─────────────────────────────────────────────
    ax = axes[1, 1]
    ax4 = ax.twinx()
    ax.plot(ticks, results["mtm_pnl"], color="green", lw=1.0, label="MtM P&L ($)")
    ax.axhline(0, color="gray", lw=0.7)
    ax.set_ylabel("P&L ($)", color="green")
    ax.tick_params(axis="y", labelcolor="green")
    mc = results["mc_result"]
    ax4.hist(mc.final_prices, bins=60, alpha=0.22, color="navy", density=True)
    ax4.axvline(mc.fair_value, color="navy", lw=1.5, ls="--",
                label=f"MC fair={mc.fair_value:.3f}")
    ax4.set_ylabel("MC terminal density", color="navy")
    ax4.tick_params(axis="y", labelcolor="navy")
    l1, lb1 = ax.get_legend_handles_labels()
    l2, lb2 = ax4.get_legend_handles_labels()
    ax.legend(l1 + l2, lb1 + lb2, fontsize=7, loc="upper left")
    ax.set_title("MtM P&L  &  MC Terminal Distribution")
    ax.set_xlabel("Tick")

    # [1,2] Almgren-Chriss ────────────────────────────────────────────────────
    ax = axes[1, 2]
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    labels = ["Very patient (1e-7)", "Patient (1e-6)", "Moderate (1e-5)", "Urgent (1e-4)"]
    for sched, col, lbl in zip(results["ac_schedules"], colors, labels):
        ax.plot(sched.times, sched.remaining / results["ac_params"].total_shares,
                color=col, lw=1.5, label=lbl)
    ax.set_title(f"Almgren-Chriss  (${results['ac_params'].total_shares:,.0f} position)")
    ax.set_ylabel("Fraction remaining")
    ax.set_xlabel("Hours elapsed")
    ax.set_ylim(0, 1.05)
    ax.axhline(0, color="gray", lw=0.7)
    ax.legend(fontsize=7)

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[plot] Saved -> {output_path}")


# ── analysis plot (2x2) ───────────────────────────────────────────────────────

def plot_analysis(
    sim_results: dict,
    mc_results: dict,
    output_path: str,
) -> None:
    """
    2x2 plot for Part V/VI analysis:
      [0,0] 100-session final P&L histogram
      [0,1] Median P&L over time ± 1 std across sessions
      [1,0] Full-series VPIN (bucket-based) + thresholds
      [1,1] Spread action breakdown (normal / widen / pull proportions)
    """
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle(
        f"Part V/VI Analysis  |  {mc_results['n_sessions']} sessions  "
        f"|  true_prob={mc_results['true_prob']:.2f}",
        fontsize=12, fontweight="bold",
    )

    # [0,0] Session P&L histogram ──────────────────────────────────────────────
    ax = axes[0, 0]
    pnls = mc_results["pnls"]
    ax.hist(pnls, bins=30, color="steelblue", edgecolor="white", alpha=0.85)
    ax.axvline(pnls.mean(), color="red", lw=1.5, ls="--",
               label=f"Mean=${pnls.mean():.1f}")
    ax.axvline(0, color="gray", lw=0.8)
    win_rate = (pnls > 0).mean()
    sharpe = pnls.mean() / (pnls.std() + 1e-9)
    ax.set_title(
        f"100-Session Final P&L\n"
        f"Win rate={win_rate:.0%}  Sharpe={sharpe:.2f}  "
        f"Std=${pnls.std():.1f}"
    )
    ax.set_xlabel("Final P&L ($)")
    ax.set_ylabel("Sessions")
    ax.legend(fontsize=8)

    # [0,1] Median P&L trajectory ─────────────────────────────────────────────
    ax = axes[0, 1]
    mat = mc_results["pnl_matrix"]
    median_pnl = np.median(mat, axis=0)
    std_pnl    = mat.std(axis=0)
    steps = np.arange(len(median_pnl))
    ax.plot(steps, median_pnl, color="green", lw=1.5, label="Median P&L")
    ax.fill_between(steps, median_pnl - std_pnl, median_pnl + std_pnl,
                    alpha=0.20, color="green", label="±1 std")
    ax.fill_between(steps, median_pnl - 2 * std_pnl, median_pnl + 2 * std_pnl,
                    alpha=0.08, color="green", label="±2 std")
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_title("P&L Trajectory Across Sessions")
    ax.set_xlabel("Step")
    ax.set_ylabel("Mark-to-market P&L ($)")
    ax.legend(fontsize=8)

    # [1,0] VPIN (bucket series from main sim) ─────────────────────────────────
    ax = axes[1, 0]
    fv = sim_results["full_vpin"]
    buckets = np.arange(len(fv))
    ax.plot(buckets, fv, color="firebrick", lw=1.2, label="VPIN (50-trade bucket)")
    ax.axhline(0.65, color="orange", lw=1.2, ls="--", label="Widen threshold (0.65)")
    ax.axhline(0.80, color="red",    lw=1.2, ls="--", label="Pull threshold (0.80)")
    ax.fill_between(buckets, fv, 0.65, where=(fv > 0.65), alpha=0.25, color="orange")
    ax.fill_between(buckets, fv, 0.80, where=(fv > 0.80), alpha=0.35, color="red")
    ax.set_ylim(0, 1)
    ax.set_title("VPIN - Probability of Informed Trading")
    ax.set_xlabel("Volume bucket (50 trades)")
    ax.set_ylabel("VPIN")
    ax.legend(fontsize=8)

    # [1,1] Spread action pie ─────────────────────────────────────────────────
    ax = axes[1, 1]
    vp = sim_results["vpin_series"]
    n_normal = (vp <= 0.65).sum()
    n_widen  = ((vp > 0.65) & (vp <= 0.80)).sum()
    n_pull   = (vp > 0.80).sum()
    total    = len(vp)
    sizes  = [n_normal, n_widen, n_pull]
    labels = [
        f"Normal\n({n_normal/total:.0%})",
        f"Widen 2x\n({n_widen/total:.0%})",
        f"Pull quotes\n({n_pull/total:.0%})",
    ]
    colors_p = ["steelblue", "orange", "firebrick"]
    explode  = [0, 0.05, 0.10]
    wedge_data = [(s, l, c, e) for s, l, c, e in zip(sizes, labels, colors_p, explode) if s > 0]
    if wedge_data:
        ax.pie(
            [w[0] for w in wedge_data],
            labels=[w[1] for w in wedge_data],
            colors=[w[2] for w in wedge_data],
            explode=[w[3] for w in wedge_data],
            autopct="%1.0f%%",
            startangle=90,
        )
    ax.set_title("Spread Action Distribution\n(VPIN thresholds)")

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[plot] Saved -> {output_path}")


# ── calibration ───────────────────────────────────────────────────────────────

def calibrate_from_csv(csv_path: str) -> dict:
    df = pd.read_csv(csv_path)
    print(f"[calibrate] Loaded {len(df):,} rows from {csv_path}")
    params: dict = {}
    if "start_price_yes" in df.columns and "final_price_yes" in df.columns:
        p0_col = df["start_price_yes"].dropna()
        pf_col = df["final_price_yes"].dropna()
        params["p0"] = float(np.clip(p0_col.median(), 0.05, 0.95))
        if "days_open" in df.columns:
            days = df["days_open"].clip(lower=1).fillna(30)
            sp = np.clip(p0_col, 1e-4, 1 - 1e-4)
            fp = np.clip(pf_col, 1e-4, 1 - 1e-4)
            logit_ret = np.log(fp / (1 - fp)) - np.log(sp / (1 - sp))
            daily_std = (logit_ret / np.sqrt(days)).std()
            params["vol_per_tick"] = float(np.clip(daily_std / np.sqrt(1_440), 0.001, 0.05))
            params["sigma_mm"] = float(np.clip(daily_std, 0.01, 0.5))
            params["days_to_resolution"] = float(np.clip(days.median(), 1.0, 90.0))
        print(f"[calibrate] p0={params.get('p0', 0.62):.4f}  "
              f"vol_per_tick={params.get('vol_per_tick', 0.008):.5f}  "
              f"days={params.get('days_to_resolution', 7.0):.1f}")
    return params


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket order-book engine (Parts I-VI)"
    )
    parser.add_argument("--ticks",         type=int,   default=2_000)
    parser.add_argument("--p0",            type=float, default=0.62)
    parser.add_argument("--informed-frac", type=float, default=0.15)
    parser.add_argument("--gamma",         type=float, default=0.10)
    parser.add_argument("--kappa",         type=float, default=100.0)
    parser.add_argument("--days",          type=float, default=7.0)
    parser.add_argument("--hawkes-mu",     type=float, default=5.0)
    parser.add_argument("--hawkes-alpha",  type=float, default=3.5)
    parser.add_argument("--hawkes-beta",   type=float, default=5.0)
    parser.add_argument("--exec-size",     type=float, default=10_000.0)
    parser.add_argument("--mc-sessions",   type=int,   default=100)
    parser.add_argument("--true-prob",     type=float, default=0.65,
                        help="True resolution probability for MC sessions")
    parser.add_argument("--seed",          type=int,   default=42)
    parser.add_argument("--calibrate",     action="store_true")
    parser.add_argument("--output",        type=str,
                        default="data/processed/orderbook_sim.png")
    parser.add_argument("--output-analysis", type=str,
                        default="data/processed/orderbook_analysis.png")
    args = parser.parse_args()

    sim_kwargs: dict = {
        "p0": args.p0,
        "n_ticks": args.ticks,
        "informed_frac": args.informed_frac,
        "gamma": args.gamma,
        "kappa": args.kappa,
        "days_to_resolution": args.days,
        "hawkes_mu": args.hawkes_mu,
        "hawkes_alpha": args.hawkes_alpha,
        "hawkes_beta": args.hawkes_beta,
        "exec_size": args.exec_size,
        "seed": args.seed,
    }
    if args.calibrate:
        for path in ["data/processed/markets_features.csv",
                     "data/processed/markets_clean.csv"]:
            if Path(path).exists():
                sim_kwargs.update(calibrate_from_csv(path))
                break
        else:
            print("[calibrate] No CSV found - using defaults.")

    sep  = "=" * 60
    dash = "-" * 60
    print(f"\n{sep}")
    print("  POLYMARKET ORDER-BOOK SIMULATION  (Parts I-VI)")
    print(sep)
    for k, v in sim_kwargs.items():
        print(f"  {k:<26}: {v}")
    print(f"{sep}\n")

    # ── Run main simulation ───────────────────────────────────────────────────
    results = run_simulation(**sim_kwargs)
    mm    = results["mm"]
    kyle  = results["kyle_result"]
    mc    = results["mc_result"]
    fit   = results["hawkes_fit"]
    ac_d  = results["ac_schedules"][1]
    hp    = results["hedge_plan"]

    # ── Part I/II: Kyle + MM ─────────────────────────────────────────────────
    print(dash)
    print("  PART I  - KYLE'S LAMBDA")
    print(dash)
    print(f"  lambda       : {kyle.lambda_:.6f}")
    print(f"  R-squared    : {kyle.r_squared:.4f}")
    print(f"  p-value      : {kyle.p_value:.4f}")
    print(f"  Verdict      : {kyle.interpretation}")

    # ── Part III: Hawkes ──────────────────────────────────────────────────────
    print()
    print(dash)
    print("  PART III - HAWKES PROCESS")
    print(dash)
    print(fit.summary())

    # ── Part IV: Almgren-Chriss ───────────────────────────────────────────────
    print()
    print(dash)
    print(f"  PART IV - ALMGREN-CHRISS  (${args.exec_size:,.0f} position)")
    print(dash)
    print(ac_d.summary())
    print()
    ac_d.print_schedule()

    # ── Part VI: VPIN ─────────────────────────────────────────────────────────
    vp = results["vpin_series"]
    n  = len(vp)
    print()
    print(dash)
    print("  PART VI - VPIN")
    print(dash)
    print(f"  Full-series mean VPIN    : {results['full_vpin'].mean():.3f}")
    print(f"  Ticks with VPIN > 0.65  : {(vp > 0.65).sum():,}  ({(vp > 0.65).mean():.1%})")
    print(f"  Ticks with VPIN > 0.80  : {(vp > 0.80).sum():,}  ({(vp > 0.80).mean():.1%})")
    print(f"  Quotes pulled (VPIN>0.80): {results['quotes_pulled']:,}")

    # ── Dynamic hedging ───────────────────────────────────────────────────────
    print()
    print(dash)
    print("  PART VI - DYNAMIC HEDGING (inventory death-spiral defence)")
    print(dash)
    print(hp.summary())

    # ── MM results ────────────────────────────────────────────────────────────
    print()
    print(dash)
    print("  PART II/V - MARKET MAKER RESULTS")
    print(dash)
    final_mtm = mm.mark_to_market(results["final_mid"])
    print(f"  Final mid price          : {results['final_mid']:.4f}")
    print(f"  Inventory (net)          : {mm.inventory:+.2f} contracts")
    print(f"  Inventory utilisation    : {mm.inventory_utilisation:.1%}")
    print(f"  Cash P&L (realised)      : ${mm.realised_pnl():+.2f}")
    print(f"  Mark-to-market P&L       : ${final_mtm:+.2f}")
    print(f"  Total fills              : {len(mm.trades):,}")
    print(f"  Average spread quoted    : {np.mean(results['spreads']):.4f}  "
          f"({np.mean(results['spreads'])*100:.2f}%)")

    # ── Part V: Monte Carlo sessions ──────────────────────────────────────────
    print()
    print(dash)
    print(f"  PART V - 100-SESSION MONTE CARLO  (true_prob={args.true_prob})")
    print(dash)
    print("  Running sessions...", end="", flush=True)
    mc_sess = run_monte_carlo_sessions(
        n_sessions=args.mc_sessions,
        true_prob=args.true_prob,
        gamma=args.gamma,
        kappa=args.kappa,
        seed=args.seed,
    )
    print(" done.")
    pnls = mc_sess["pnls"]
    sharpe = pnls.mean() / (pnls.std() + 1e-9)
    print(f"  Mean P&L                 : ${pnls.mean():>8.2f}")
    print(f"  Median P&L               : ${np.median(pnls):>8.2f}")
    print(f"  Std P&L                  : ${pnls.std():>8.2f}")
    print(f"  Sharpe (approx)          :  {sharpe:>8.3f}")
    print(f"  Win rate                 :  {(pnls > 0).mean():.0%}")
    print(f"  Avg spread               :  {mc_sess['spreads'].mean():.4f}")

    # ── Production stack summary ───────────────────────────────────────────────
    print()
    print(dash)
    print("  PART VII - PRODUCTION STACK READINESS")
    print(dash)
    layers = [
        ("LAYER 1  Data ingestion",   "WebSocket CLOB API  |  historical tape"),
        ("LAYER 2  Parameter est.",   f"Kyle lambda={kyle.lambda_:.5f}  "
                                      f"Hawkes rho={fit.params.branching_ratio:.2f}  "
                                      f"VPIN={results['full_vpin'].mean():.3f}  (15-min refresh)"),
        ("LAYER 3  Quoting",          f"A-S spread={np.mean(results['spreads']):.4f}  "
                                      f"VPIN widen at 0.65  pull at 0.80"),
        ("LAYER 4  Execution",        f"A-C shortfall={ac_d.implementation_shortfall*100:.3f}%  "
                                      f"on ${args.exec_size:,.0f}"),
        ("LAYER 5  Monitoring",       f"MtM P&L={final_mtm:+.2f}  "
                                      f"fills={len(mm.trades)}  "
                                      f"inv_util={mm.inventory_utilisation:.1%}"),
    ]
    for layer, status in layers:
        print(f"  {layer:<28}: {status}")
    print(sep)

    # ── Plots ─────────────────────────────────────────────────────────────────
    plot_simulation(results, output_path=args.output)
    plot_analysis(results, mc_sess, output_path=args.output_analysis)
    print(f"\n  Plots saved:")
    print(f"    {args.output}")
    print(f"    {args.output_analysis}")
    print(f"{sep}\n")


if __name__ == "__main__":
    main()
