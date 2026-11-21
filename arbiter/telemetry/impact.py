"""Environmental and economic impact telemetry for project ARBITER v2.

Converts energy saved versus the uncoordinated baseline into:
- Avoided energy consumption in kilowatt-hours (kWh)
- Monetary cost savings in Indian Rupees (INR, ₹)
- Avoided carbon dioxide emissions in grams (gCO2)

Traceability:
  Feature #7: Impact Telemetry & Environmental Auditing
  Contract: arbiter.shared.v2_contracts.ImpactRecord
  Database: v2_impact table (arbiter.shared.database_v2)
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from arbiter.shared.v2_contracts import ImpactRecord

# ---------------------------------------------------------------------------
# DOCUMENTED ASSUMPTIONS & SOURCES
# ---------------------------------------------------------------------------
IMPACT_ASSUMPTIONS: Dict[str, str] = {
    "GRID_EMISSION_FACTOR": (
        "ASSUMPTION: 820.0 gCO2/kWh based on Central Electricity Authority (CEA) "
        "India CO2 Baseline Database for the Indian National Electricity Grid. "
        "Configurable via ARBITER_GRID_EMISSION_FACTOR_G_PER_KWH."
    ),
    "COMMERCIAL_OFF_PEAK_TARIFF": (
        "ASSUMPTION: ₹7.00/kWh night/off-peak commercial slab (22:00 - 06:00). "
        "Configurable via ARBITER_TARIFF_OFF_PEAK_INR."
    ),
    "COMMERCIAL_NORMAL_TARIFF": (
        "ASSUMPTION: ₹9.50/kWh normal daytime commercial slab (06:00 - 18:00). "
        "Configurable via ARBITER_TARIFF_NORMAL_INR."
    ),
    "COMMERCIAL_PEAK_TARIFF": (
        "ASSUMPTION: ₹12.00/kWh evening peak commercial slab (18:00 - 22:00). "
        "Configurable via ARBITER_TARIFF_PEAK_INR."
    ),
    "EXTRAPOLATION_MODEL": (
        "ASSUMPTION: Linear extrapolation to 1,000 macro radio sites operating "
        "8,760 hours/year assuming steady nocturnal power saving patterns. "
        "Clearly labelled as an extrapolation."
    ),
}

# ---------------------------------------------------------------------------
# CONFIGURABLE PARAMETERS (.env-style overrides)
# ---------------------------------------------------------------------------
DEFAULT_EMISSION_FACTOR_G_PER_KWH: float = float(
    os.environ.get("ARBITER_GRID_EMISSION_FACTOR_G_PER_KWH", "820.0")
)
TARIFF_OFF_PEAK_INR: float = float(
    os.environ.get("ARBITER_TARIFF_OFF_PEAK_INR", "7.00")
)
TARIFF_NORMAL_INR: float = float(
    os.environ.get("ARBITER_TARIFF_NORMAL_INR", "9.50")
)
TARIFF_PEAK_INR: float = float(
    os.environ.get("ARBITER_TARIFF_PEAK_INR", "12.00")
)

# Time-of-Use tariff definitions (hour 0-23 in UTC/local time)
TOU_SCHEDULE: List[Tuple[int, int, str, float]] = [
    (22, 24, "COMMERCIAL_OFF_PEAK_NIGHT", TARIFF_OFF_PEAK_INR),
    (0, 6, "COMMERCIAL_OFF_PEAK_NIGHT", TARIFF_OFF_PEAK_INR),
    (6, 18, "COMMERCIAL_NORMAL_DAY", TARIFF_NORMAL_INR),
    (18, 22, "COMMERCIAL_PEAK_EVENING", TARIFF_PEAK_INR),
]


def resolve_tariff(hour: int) -> Tuple[str, float]:
    """Resolve Time-of-Use tariff label and unit cost (INR/kWh) for an hour of day.

    Args:
        hour: Hour of day in [0, 23].

    Returns:
        Tuple of (tariff_label, tariff_rate_inr_per_kwh).
    """
    normalized_hour = int(hour) % 24
    for start, end, label, rate in TOU_SCHEDULE:
        if start <= normalized_hour < end:
            return label, rate
    return "COMMERCIAL_NORMAL_DAY", TARIFF_NORMAL_INR


class ImpactCalculator:
    """Computes environmental and economic impact metrics from energy deltas."""

    def __init__(
        self,
        grid_emission_factor_g_per_kwh: float = DEFAULT_EMISSION_FACTOR_G_PER_KWH,
    ) -> None:
        self.grid_emission_factor = max(0.0, grid_emission_factor_g_per_kwh)

    def calculate_tick_impact(
        self,
        tick: int,
        energy_saved_w: float,
        duration_s: float = 0.1,
        hour: int = 2,
    ) -> ImpactRecord:
        """Calculate economic and carbon impact for a single simulation tick.

        Args:
            tick: Simulation tick index.
            energy_saved_w: Avoided power in Watts (relative to uncoordinated baseline).
            duration_s: Duration of the tick in seconds (default 100 ms = 0.1 s).
            hour: Simulated hour of day (default 2 for 2 AM collision).

        Returns:
            Validated ImpactRecord.
        """
        clamped_w = max(0.0, float(energy_saved_w))
        # Joules = Watts * seconds; kWh = Joules / 3,600,000
        energy_saved_kwh = (clamped_w * float(duration_s)) / 3_600_000.0
        tariff_label, tariff_rate = resolve_tariff(hour)
        cost_inr = energy_saved_kwh * tariff_rate
        co2_g = energy_saved_kwh * self.grid_emission_factor

        return ImpactRecord(
            tick=int(tick),
            energy_saved_kwh=round(energy_saved_kwh, 6),
            cost_inr=round(cost_inr, 6),
            co2_g=round(co2_g, 4),
            tariff_label=tariff_label,
        )

    def calculate_extrapolation_1000_sites(
        self,
        cluster_energy_saved_w: float,
        cluster_sites: int = 5,
        target_sites: int = 1000,
        annual_hours: float = 8760.0,
        average_tariff_inr: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Extrapolate energy savings to 1,000 radio base station sites per year.

        Clearly marked with extrapolation flag and explicit assumption notes.

        Args:
            cluster_energy_saved_w: Instantaneous cluster power saved in Watts.
            cluster_sites: Number of sites in evaluated cluster (default 5).
            target_sites: Scaled target macro fleet size (default 1,000).
            annual_hours: Operational hours per year (default 8,760).
            average_tariff_inr: Blended tariff rate in INR/kWh.

        Returns:
            Extrapolation summary dictionary.
        """
        safe_sites = max(1, cluster_sites)
        power_saved_per_site_kw = (
            max(0.0, float(cluster_energy_saved_w)) / 1000.0
        ) / safe_sites
        annual_kwh_per_site = power_saved_per_site_kw * annual_hours
        annual_kwh_fleet = annual_kwh_per_site * float(target_sites)

        blended_rate = (
            average_tariff_inr
            if average_tariff_inr is not None
            else TARIFF_OFF_PEAK_INR
        )
        annual_cost_inr = annual_kwh_fleet * blended_rate
        annual_co2_kg = (annual_kwh_fleet * self.grid_emission_factor) / 1000.0

        return {
            "extrapolation": True,
            "target_sites": target_sites,
            "cluster_sites": safe_sites,
            "annual_operational_hours": annual_hours,
            "annual_kwh_saved": round(annual_kwh_fleet, 2),
            "annual_cost_savings_inr": round(annual_cost_inr, 2),
            "annual_cost_savings_lakhs_inr": round(annual_cost_inr / 100_000.0, 3),
            "annual_co2_avoided_kg": round(annual_co2_kg, 2),
            "annual_co2_avoided_metric_tons": round(annual_co2_kg / 1000.0, 3),
            "assumptions_disclaimer": (
                "EXTRAPOLATION ONLY: Assumes continuous annual operational baseline "
                f"across {target_sites} radio sites using blended tariff ₹{blended_rate:.2f}/kWh "
                f"and CEA grid factor {self.grid_emission_factor} gCO2/kWh."
            ),
        }


def store_impact_record(conn: sqlite3.Connection, record: ImpactRecord) -> int:
    """Persist an ImpactRecord into the v2_impact database table."""
    payload_json = record.model_dump_json()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO v2_impact (tick, tariff_label, payload)
            VALUES (?, ?, ?)
            """,
            (record.tick, record.tariff_label, payload_json),
        )
        return int(cursor.lastrowid or 0)


def fetch_latest_impact(conn: sqlite3.Connection) -> Optional[ImpactRecord]:
    """Retrieve the most recent ImpactRecord from v2_impact."""
    cursor = conn.execute(
        """
        SELECT payload
        FROM v2_impact
        ORDER BY impact_id DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if not row:
        return None
    data = json.loads(row[0])
    return ImpactRecord.model_validate(data)


def fetch_impact_history(
    conn: sqlite3.Connection, limit: int = 50
) -> List[ImpactRecord]:
    """Retrieve recent impact history records from v2_impact."""
    cursor = conn.execute(
        """
        SELECT payload
        FROM v2_impact
        ORDER BY impact_id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    )
    results: List[ImpactRecord] = []
    for row in cursor.fetchall():
        results.append(ImpactRecord.model_validate(json.loads(row[0])))
    return results
