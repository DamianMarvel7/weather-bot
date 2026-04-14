---
date: 2026-04-09
type: insight
tags: [insight, model, sigma, calibration]
---

# Insight: Probability Model Was Systematically Overconfident

## What We Learned

The bot's sigma formula `MAE * 1.8` inflated the distribution width ~44% beyond the theoretically correct conversion (`MAE * 1.253` for normal distributions). This meant tail buckets — cheap YES positions at 15-25 cents — were assigned 2-3x higher probability than their market price implied.

**Effect**: Every cheap bucket looked like a bargain (EV > 0.5), but the market was correctly pricing the true probability. The bot was manufacturing edge from miscalibration, not from genuine information advantage.

## The Fix

Use the **standard deviation of forecast errors** directly as sigma — no multiplier. This is mathematically correct: `std` IS the sigma of the error distribution.

Additionally, normalize all bucket probabilities to sum=1 before computing EV. The old model computed each bucket independently; they could sum to >1.2, meaning the model thought multiple buckets were simultaneously underpriced — mathematically impossible for a single-outcome event.

## How to Detect This Again

Run `weatherbet.py edge` and check the Probability Calibration table:
- If model says P=0.30, actual win rate should be near 30%
- If actual win rate is consistently much lower than estimated P, sigma is too wide
- If actual win rate is consistently much higher, sigma is too narrow (adjust default_std)

## Key Numbers

| Metric | Old model | Fixed model |
|---|---|---|
| Default sigma (US) | 5°F * 1.8 = **9.0°F** | **3.5°F** |
| Default sigma (EU/Asia) | 2.5°C * 1.8 = **4.5°C** | **2.0°C** |
| Bucket prob normalization | independent (can sum >1) | normalized to 1.0 |
