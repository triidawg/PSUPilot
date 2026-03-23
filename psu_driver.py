"""
psu_driver.py — Loads a JSON driver file and wraps pyvisa communication.
"""

import json
import pyvisa


class PSUDriver:
    def __init__(self, driver_path: str):
        with open(driver_path, "r") as f:
            self._cfg = json.load(f)

        self.model = self._cfg["model"]
        self.brand = self._cfg["brand"]
        self.full_name = self._cfg["full_name"]
        self.name = self.full_name  # backwards compat
        self.max_voltage = float(self._cfg["max_voltage"])
        self.max_current = float(self._cfg["max_current"])
        self._cmds = self._cfg["commands"]
        self._term = self._cfg.get("termination", "\n")

        self._idn_model: str | None = self._cfg.get("idn_model")

        self._rm: pyvisa.ResourceManager | None = None
        self._inst = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, port: str) -> None:
        """Open a pyvisa-py serial connection on *port* (e.g. 'COM3')."""
        self._rm = pyvisa.ResourceManager("@py")
        # pyvisa-py expects ASRL4::INSTR not ASRLCOM4::INSTR
        port_num = port.upper().replace("COM", "")
        resource_str = f"ASRL{port_num}::INSTR"
        self._inst = self._rm.open_resource(resource_str)
        self._inst.read_termination = self._term
        self._inst.write_termination = self._term
        self._inst.timeout = 3000  # ms
        self.set_mode("cv")  # default to CV on connect

    def disconnect(self) -> None:
        try:
            self.output_off()
        except Exception:
            pass
        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                pass
            self._inst = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None

    @property
    def connected(self) -> bool:
        return self._inst is not None

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _write(self, cmd: str) -> None:
        if self._inst is None:
            raise RuntimeError("Not connected to PSU.")
        self._inst.write(cmd)

    def _query(self, cmd: str) -> str:
        if self._inst is None:
            raise RuntimeError("Not connected to PSU.")
        return self._inst.query(cmd).strip()

    def query_idn(self) -> str:
        """Send *IDN? and return the raw response string."""
        return self._query("*IDN?")

    def verify_idn(self) -> tuple[bool, str]:
        """Query *IDN? and check the model field against the driver's idn_model.

        Returns (match: bool, idn_response: str).
        If the driver has no idn_model set, returns (True, response) — check skipped.
        """
        if not self._idn_model:
            return True, ""
        try:
            response = self.query_idn()
        except Exception as exc:
            return False, f"(query failed: {exc})"
        # Response format: GW-INSTEK,PSW160-7.2,TW123456,01.00.20110101
        parts = [p.strip() for p in response.split(",")]
        actual_model = parts[1] if len(parts) >= 2 else response
        match = actual_model.upper() == self._idn_model.upper()
        return match, response

    def set_mode(self, mode: str) -> None:
        """Set operating mode. mode = 'cv' or 'cc'. Silently ignored if driver lacks the command."""
        cmd = self._cmds.get(f"mode_{mode.lower()}")
        if cmd:
            self._write(cmd)

    def set_voltage(self, volts: float) -> None:
        if volts < 0 or volts > self.max_voltage:
            raise ValueError(f"Voltage {volts} out of range (0–{self.max_voltage})")
        self._write(self._cmds["set_voltage"].format(v=f"{volts:.3f}"))

    def set_current(self, amps: float) -> None:
        if amps < 0 or amps > self.max_current:
            raise ValueError(f"Current {amps} out of range (0–{self.max_current})")
        self._write(self._cmds["set_current"].format(i=f"{amps:.3f}"))

    def output_on(self) -> None:
        self._write(self._cmds["output_on"])

    def output_off(self) -> None:
        self._write(self._cmds["output_off"])

    def measure_voltage(self) -> float:
        return float(self._query(self._cmds["measure_voltage"]))

    def measure_current(self) -> float:
        return float(self._query(self._cmds["measure_current"]))
