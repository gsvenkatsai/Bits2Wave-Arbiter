# Frame Message & WebSocket Broadcast Contract

The unified frame packet (`type: "frame"`) is broadcast every 100 ms simulation tick over `WS /api/v1/ws/live` and ingested via `POST /api/v1/frames/ingest`.

## Top-Level Frame Message Schema

| Field | Type | Required | Description |
| :--- | :---: | :---: | :--- |
| `type` | `string` | **Yes** | Fixed packet discriminator: `"frame"` |
| `tick` | `integer` | **Yes** | Discrete simulation tick counter ($\ge 0$, 100 ms increments) |
| `sim_time_ms` | `integer` | **Yes** | Unix epoch simulation timestamp in milliseconds |
| `scenario` | `string` | **Yes** | Active traffic scenario identifier (e.g. `"2am_collision"`, `"quiet_2am"`) |
| `world` | `string` | **Yes** | Execution world: `"arbiter"` (coordinated) or `"counterfactual"` (raw baseline) |
| `cells` | `array[CellFrame]` | **Yes** | Per-cell radio state and RF operating parameters |
| `users` | `array[UserFrame]` | **Yes** | Active user equipments, kinematic coordinates, and radio channel quality |
| `proposals` | `array[ProposalFrame]` | **Yes** | Autonomous controller command proposals arriving in this tick window |
| `decisions` | `array[DecisionFrame]` | **Yes** | ARBITER referee verdicts, approved parameter values, and reason codes |
| `conflicts` | `array[ConflictFrame]` | **Yes** | Pairwise conflict graph edges identifying competing proposals |
| `plans` | `array[PlanFrame]` | **Yes** | Active multi-tick sequential execution plans |
| `twin` | `TwinFrame` | **Yes** | Conformal digital twin calibration status and KPI uncertainty intervals |
| `kpis` | `KpiFrame` | **Yes** | Aggregated cluster KPIs (dropped users, mean SINR, power consumption) |
| `meta` | `object` | No | Engine configuration provenance (`twin_kind`: `"real"`/`"scripted"`, `sink`: `"queue"`/`"mock"`) |

---

## Nested Payloads

### 1. `CellFrame` (`cells[]`)
| Field | Type | Unit | Description |
| :--- | :---: | :---: | :--- |
| `id` | `string` | — | Cell sector identifier (e.g. `"cell_A"`, `"cell_B"`) |
| `x`, `y` | `float` | m | Cartesian coordinate position on hexagonal grid |
| `tx_power_dbm` | `float` | dBm | Current RF transmit power (nominal: 40.0 dBm, clamped [20, 46]) |
| `tilt_deg` | `float` | deg | Electrical antenna downtilt angle (clamped [0.0, 15.0]) |
| `is_sleeping` | `boolean` | — | Nocturnal energy-saving standby state (`true` = standby / 0W RF, `false` = active) |
| `active_users` | `integer` | count | Number of attached active subscribers |
| `load_ratio` | `float` | ratio | Cell capacity utilization ratio $[0.0, 1.0]$ |
| `energy_w` | `float` | W | Instantaneous electrical power draw in Watts |

### 2. `UserFrame` (`users[]`)
| Field | Type | Unit | Description |
| :--- | :---: | :---: | :--- |
| `id` | `string` | — | User equipment unique identifier (e.g. `"ue_0042"`) |
| `x`, `y` | `float` | m | Current Cartesian location |
| `cell` | `string` | — | Attached serving cell identifier |
| `sinr_db` | `float` | dB | Downlink channel Signal-to-Interference-plus-Noise Ratio |
| `dropped` | `boolean` | — | Call drop flag (`true` when $\text{SINR} < -3.0\text{ dB}$ or serving cell sleeps) |

### 3. `ProposalFrame` (`proposals[]`)
| Field | Type | Description |
| :--- | :---: | :--- |
| `command_id` | `string` | Unique proposal UUID |
| `controller_type` | `string` | Originating controller: `ENERGY_SAVING`, `LOAD_BALANCING`, `COVERAGE_CAPACITY`, `PUBLIC_SAFETY` |
| `target_cell_id` | `string` | Target cell identifier |
| `parameter` | `string` | Target parameter: `TX_POWER_DBM`, `TILT_DEG`, `SLEEP_STATE`, `OFFLOAD_RATIO` |
| `requested_value`| `float` | Requested actuation parameter value |
| `emergency_flag` | `boolean`| Emergency bypass lane flag (`true` for public safety) |
| `hold_count` | `integer`| Number of consecutive ticks command has been held ($\le 3$) |
| `timestamp_epoch_ms`| `integer`| Proposal emission timestamp |

### 4. `DecisionFrame` (`decisions[]`)
| Field | Type | Description |
| :--- | :---: | :--- |
| `decision_id` | `string` | Unique arbitration decision UUID |
| `command_id` | `string` | Reference to evaluated `CommandProposal` |
| `action` | `string` | Referee verdict: `APPROVE`, `MODIFY`, `DELAY`, `REJECT` |
| `approved_value`| `float` / `null` | Enforced parameter value (or null if delayed/rejected) |
| `reason_code` | `string` | Audit reason code: `NONE`, `ACTIVE_HO`, `HEADROOM`, `CYCLIC_PINGPONG`, `TWIN_TIMEOUT`, `UNCERTAINTY_GUARD`, `FLOOR_BREACH`, `EMERGENCY_OVERRIDE` |
| `hold_count` | `integer`| Updated hold counter state |
| `delta_utility` | `float` | Predicted multi-objective scalar utility improvement |
