# Conformal Safety Intervals on the Digital Twin (ARBITER v2)

## 1. Overview & Theoretical Guarantee

The Project ARBITER v2 Digital Twin implements **split conformal prediction** to construct non-parametric, distribution-free uncertainty intervals for cellular radio Key Performance Indicators (KPIs):
- `dropped_users`
- `mean_sinr_db`
- `energy_w`
- `interference_db`
- `coverage_ratio`
- `drop_rate_ratio`
- `normalized_energy`
- `normalized_interference`

For any user-specified miscoverage tolerance $\alpha \in (0, 1)$ (default $\alpha = 0.10$ for 90% nominal coverage), the interval $\hat{C}_{1-\alpha}(X)$ satisfies the finite-sample marginal coverage property:

$$\mathbb{P}\left(Y_{n+1} \in \hat{C}_{1-\alpha}(X_{n+1})\right) \ge 1 - \alpha$$

where the quantile is computed over $n$ rolling calibration residuals $R_i = |\hat{Y}_i - Y_i|$ as:

$$q = R_{\left(\lceil (n + 1)(1 - \alpha) \rceil\right)}$$

and the interval is bounded symmetrically:

$$\hat{C}_{1-\alpha}(X) = [\hat{Y} - q, \; \hat{Y} + q]$$

---

## 2. Clarification on Assumptions & Limitations in Cellular Networks

> **Important Boundary Note:**
> The mathematical coverage guarantee of conformal prediction holds strictly under the assumption of **exchangeability** between calibration samples and test samples (e.g., independent and identically distributed realizations).

In live or simulated cellular networks, **exchangeability is only approximately true** due to:
1. **Diurnal Non-Stationarity:** Traffic demand changes continuously between nocturnal quiet periods (e.g. 2 AM) and daytime peaks.
2. **Controller Actuation Feedback:** Closed-loop interventions (e.g., cell sleep switch-off, load offloading, power boosts) alter the underlying physical distribution $P(Y \mid X)$.
3. **Temporal Autocorrelation:** Fast/slow fading and user mobility create short-term dependency structures across successive ticks.

### Practical Engineering Mitigations:
- **Rolling Calibration Buffer:** Residuals are tracked over a moving window ($N = 200$ ticks) rather than a static dataset. This localizes the calibration distribution to the current operational regime.
- **Cold-Start Guard:** If fewer than 30 residuals are present ($n < 30$), `calibrated = False` is returned along with wide, conservative default intervals.
- **Dynamic Recalibration & Drift Tracking:** If environmental drift or controller interventions induce larger prediction errors, the conformal quantile $q$ automatically widens across subsequent ticks.
- **Honest Marginal Guarantee:** The guarantees provided are **marginal over the rolling window**, not uniform conditional coverage across arbitrary distribution shifts.
