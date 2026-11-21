"""Cell entity and configuration models for RAN simulation."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from arbiter.shared.models import CellState


class CellConfiguration(BaseModel):
    """Configuration and state parameters for a single RAN cell sector."""

    cell_id: str = Field(
        ..., description="Unique cell identifier, e.g. 'cell_A'"
    )
    x_coord: float = Field(..., description="Cartesian X coordinate in meters")
    y_coord: float = Field(..., description="Cartesian Y coordinate in meters")
    carrier_freq_mhz: float = Field(
        default=2100.0, description="Carrier frequency in MHz")
    max_tx_power_dbm: float = Field(
        default=46.0, description="Maximum transmission power (40W)")
    min_tx_power_dbm: float = Field(
        default=30.0, description="Minimum transmission power (1W)")
    current_tx_power_dbm: float = Field(
        default=43.0, description="Operational transmit power (20W)"
    )
    antenna_tilt_deg: float = Field(
        default=6.0, ge=0.0, le=15.0, description="Electrical down-tilt"
    )
    is_sleeping: bool = Field(
        default=False, description="Sleep state: True=0W standby, False=Active"
    )
    neighbor_cell_ids: List[str] = Field(
        default_factory=list, description="Adjacency list"
    )


class UserEquipment(BaseModel):
    """User equipment state in the radio access network."""

    ue_id: str = Field(..., description="Unique identifier for user equipment")
    x_coord: float = Field(..., description="Cartesian X coordinate in meters")
    y_coord: float = Field(..., description="Cartesian Y coordinate in meters")
    attached_cell_id: str = Field(..., description="ID of serving cell")
    rx_power_dbm: float = Field(
        default=-100.0, description="Received signal power in dBm")
    interference_power_dbm: float = Field(
        default=-104.0, description="Inter-cell interference in dBm"
    )
    sinr_db: float = Field(
        default=0.0, description="Signal-to-Interference-plus-Noise Ratio")
    required_rate_mbps: float = Field(
        default=2.0, description="Requested data rate in Mbps")
    is_dropped: bool = Field(default=False, description="Call drop flag")


class CellEntity:
    """Domain entity encapsulating cell physical state, power, and energy."""

    def __init__(
        self,
        config: Optional[CellConfiguration] = None,
        **kwargs: Any,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = CellConfiguration(**kwargs)

    @property
    def cell_id(self) -> str:
        return self.config.cell_id

    @property
    def x_coord(self) -> float:
        return self.config.x_coord

    @property
    def y_coord(self) -> float:
        return self.config.y_coord

    @property
    def current_tx_power_dbm(self) -> float:
        return self.config.current_tx_power_dbm

    @property
    def antenna_tilt_deg(self) -> float:
        return self.config.antenna_tilt_deg

    @property
    def is_sleeping(self) -> bool:
        return self.config.is_sleeping

    @property
    def neighbor_cell_ids(self) -> List[str]:
        return self.config.neighbor_cell_ids

    @property
    def carrier_freq_mhz(self) -> float:
        return self.config.carrier_freq_mhz

    def set_tx_power(self, dbm: float) -> None:
        """Set transmit power clamped to min and max boundaries."""
        clamped = max(
            self.config.min_tx_power_dbm,
            min(self.config.max_tx_power_dbm, float(dbm)),
        )
        self.config.current_tx_power_dbm = clamped

    def set_tilt(self, deg: float) -> None:
        """Set electrical down-tilt clamped to [0.0, 15.0] degrees."""
        clamped = max(0.0, min(15.0, float(deg)))
        self.config.antenna_tilt_deg = clamped

    def set_sleep(self, is_sleeping: bool) -> None:
        """Update operational sleep state."""
        self.config.is_sleeping = bool(is_sleeping)

    def effective_tx_power_w(self) -> float:
        """Effective radiated power in Watts. Strictly 0.0 W when sleeping."""
        if self.config.is_sleeping:
            return 0.0
        # P(W) = 10 ** ((P_dBm - 30) / 10)
        p_dbm = self.config.current_tx_power_dbm
        return float(10.0 ** ((p_dbm - 30.0) / 10.0))

    def energy_consumption_w(self) -> float:
        """Total operational power consumption in Watts."""
        if self.config.is_sleeping:
            return 0.0
        # 220 W base + 10 * RF transmit watts (43 dBm gives ~420 W)
        return float(220.0 + 10.0 * self.effective_tx_power_w())

    @classmethod
    def from_fixture_dict(cls, data: Dict[str, Any]) -> "CellEntity":
        """Instantiate a CellEntity from a fixture dictionary."""
        cell_id = str(data["cell_id"])
        x_coord = float(data.get("x_coord", data.get("x", 0.0)))
        y_coord = float(data.get("y_coord", data.get("y", 0.0)))
        carrier_freq_mhz = float(data.get("carrier_freq_mhz", 2100.0))
        max_tx_power_dbm = float(data.get("max_tx_power_dbm", 46.0))
        min_tx_power_dbm = float(data.get("min_tx_power_dbm", 30.0))
        current_tx_power_dbm = float(
            data.get("current_tx_power_dbm", data.get("tx_power_dbm", 43.0))
        )
        antenna_tilt_deg = float(
            data.get("antenna_tilt_deg", data.get("tilt_deg", 6.0))
        )
        is_sleeping = bool(data.get("is_sleeping", False))
        neighbor_cell_ids = list(
            data.get("neighbor_cell_ids", data.get("neighbors", []))
        )

        config = CellConfiguration(
            cell_id=cell_id,
            x_coord=x_coord,
            y_coord=y_coord,
            carrier_freq_mhz=carrier_freq_mhz,
            max_tx_power_dbm=max_tx_power_dbm,
            min_tx_power_dbm=min_tx_power_dbm,
            current_tx_power_dbm=current_tx_power_dbm,
            antenna_tilt_deg=antenna_tilt_deg,
            is_sleeping=is_sleeping,
            neighbor_cell_ids=neighbor_cell_ids,
        )
        return cls(config=config)

    def to_cell_state(
        self, active_users: int = 0, load_ratio: float = 0.0
    ) -> CellState:
        """Convert current entity state to shared CellState model."""
        return CellState(
            cell_id=self.config.cell_id,
            x_coord=self.config.x_coord,
            y_coord=self.config.y_coord,
            current_tx_power_dbm=self.config.current_tx_power_dbm,
            antenna_tilt_deg=self.config.antenna_tilt_deg,
            is_sleeping=self.config.is_sleeping,
            active_users=int(active_users),
            load_ratio=float(load_ratio),
            neighbor_cell_ids=list(self.config.neighbor_cell_ids),
        )
