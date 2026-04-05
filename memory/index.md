# Polymarket Bot — Knowledge Base

## Templates
- [[templates/bot_change_log]] — use whenever the bot code or config changes
- [[templates/trade_log]] — use for every trade (entry + post-resolution)

## Bot Changes
- [[bot_changes/2026-04-04_risk_and_filter_improvements]] — raise min_ev 0.10→0.25, stop-loss 0.65→0.35, blacklist 6 losing cities
- [[bot_changes/2026-04-04_expand_city_blacklist]] — expand blacklist from 6 to 17 cities based on edge report
- [[bot_changes/2026-04-05_reduce_churn_and_tighten_filters]] — min_price 0.05→0.12, min_ev 0.25→0.50, max_positions 10→4, fix forecast_change churn
- [[bot_changes/2026-04-05_revert_max_positions_to_10]] — revert max_positions 4→10, high-volume jackpot strategy

## Log
- [[log]] — append-only chronological record of all sessions (config changes, analyses, insights)

## Trades
> Link each trade log here.

## Strategies
- [[strategies/improvement_roadmap]] — 6-phase improvement plan based on 2026-04-04 loss analysis

## Insights
- [[insights/stop_loss_kills_edge]] — stop-loss fires always lost; holding to resolution is correct for weather markets
- [[insights/city_edge_not_uniform]] — 17/30 cities blacklisted; HRRR quality and spread liquidity likely cause variation
- [[insights/thin_price_spread_cost]] — entries below 0.12 have 40-60% spread cost; EV calc meaningless at thin prices
