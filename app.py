"""
app.py — Main CustomTkinter application window.
"""

import csv
import json
import os
import glob
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import serial.tools.list_ports

from psu_driver import PSUDriver
from cycle_runner import CycleRunner
from live_plot import LivePlot

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "profiles")
DRIVERS_DIR = os.path.join(os.path.dirname(__file__), "drivers")

os.makedirs(PROFILES_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_driver_names() -> dict[str, str]:
    """Returns {model_code: path} — e.g. {'PSW160-7.2': '/path/PSW160-7.2.json'}"""
    result = {}
    for path in glob.glob(os.path.join(DRIVERS_DIR, "*.json")):
        try:
            with open(path) as f:
                d = json.load(f)
            result[d["model"]] = path
        except Exception:
            pass
    return result


def _list_com_ports() -> list[str]:
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return ports if ports else ["(none)"]


def _read_psu_limits(path: str) -> tuple[float, float]:
    """Returns (max_voltage, max_current) from a driver JSON file."""
    try:
        with open(path) as f:
            d = json.load(f)
        return float(d["max_voltage"]), float(d["max_current"])
    except Exception:
        return 9999.0, 9999.0


# ---------------------------------------------------------------------------
# Step row widget
# ---------------------------------------------------------------------------

class StepRow(ctk.CTkFrame):
    COLS = ["#", "Voltage (V)", "Current (A)", "Ramp (s)", "Dwell (s)"]
    WIDTHS = [24, 62, 62, 54, 54]

    # Class-level PSU limits — updated by App when the PSU selection changes
    _cls_max_voltage: float = 9999.0
    _cls_max_current: float = 9999.0

    # Optional callback(msg: str) to surface validation warnings in the editor
    _cls_warning_callback = None

    @classmethod
    def set_psu_limits(cls, max_v: float, max_i: float):
        cls._cls_max_voltage = max_v
        cls._cls_max_current = max_i

    @classmethod
    def set_warning_callback(cls, cb):
        cls._cls_warning_callback = cb

    def __init__(self, master, index: int, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._index_label = ctk.CTkLabel(self, text=str(index + 1), width=self.WIDTHS[0],
                                          anchor="center", font=("Consolas", 12))
        self._index_label.grid(row=0, column=0, padx=1)

        self._entries: list[ctk.CTkEntry] = []
        # Default current is "MAX" so it auto-fills from the PSU profile
        defaults = ["12.0", "MAX", "2.0", "10.0"]
        for col, (w, val) in enumerate(zip(self.WIDTHS[1:], defaults), start=1):
            e = ctk.CTkEntry(self, width=w, justify="center", font=("Consolas", 12))
            e.insert(0, val)
            e.grid(row=0, column=col, padx=1)
            self._entries.append(e)

        self._setup_validation()

    def _setup_validation(self):
        self._entries[0].bind("<FocusOut>", self._on_voltage_focusout)
        self._entries[1].bind("<FocusOut>", self._on_current_focusout)

    def _on_voltage_focusout(self, _event=None):
        e = self._entries[0]
        try:
            val = float(e.get())
        except ValueError:
            return
        if val > StepRow._cls_max_voltage:
            e.configure(border_color="#ff4444")
            if StepRow._cls_warning_callback:
                StepRow._cls_warning_callback(
                    f"Voltage {val} V exceeds PSU max of {StepRow._cls_max_voltage} V")
        else:
            e.configure(border_color=["#979DA2", "#565B5E"])
            if StepRow._cls_warning_callback:
                StepRow._cls_warning_callback("")

    def _on_current_focusout(self, _event=None):
        e = self._entries[1]
        raw = e.get().strip().upper()
        if raw == "MAX":
            e.configure(border_color=["#979DA2", "#565B5E"])
            if StepRow._cls_warning_callback:
                StepRow._cls_warning_callback("")
            return
        try:
            val = float(raw)
        except ValueError:
            return
        if val > StepRow._cls_max_current:
            e.configure(border_color="#ff4444")
            if StepRow._cls_warning_callback:
                StepRow._cls_warning_callback(
                    f"Current {val} A exceeds PSU max of {StepRow._cls_max_current} A")
        else:
            e.configure(border_color=["#979DA2", "#565B5E"])
            if StepRow._cls_warning_callback:
                StepRow._cls_warning_callback("")

    def revalidate(self):
        """Re-run border-colour validation after PSU limits change."""
        self._on_voltage_focusout()
        self._on_current_focusout()

    def set_index(self, n: int):
        self._index_label.configure(text=str(n + 1))

    def get_values(self) -> dict:
        """Return step values as floats; 'MAX' current is resolved to the PSU max."""
        keys = ["voltage", "current", "ramp", "dwell"]
        result = {}
        for k, e in zip(keys, self._entries):
            raw = e.get().strip()
            if k == "current" and raw.upper() == "MAX":
                result[k] = StepRow._cls_max_current
            else:
                result[k] = float(raw)
        return result

    def get_raw_values(self) -> dict:
        """Like get_values() but preserves 'MAX' as a string for profile saving."""
        keys = ["voltage", "current", "ramp", "dwell"]
        result = {}
        for k, e in zip(keys, self._entries):
            raw = e.get().strip()
            if k == "current" and raw.upper() == "MAX":
                result[k] = "MAX"
            else:
                result[k] = float(raw)
        return result

    def set_values(self, d: dict):
        keys = ["voltage", "current", "ramp", "dwell"]
        for key, e in zip(keys, self._entries):
            val = d.get(key, "0")
            e.delete(0, "end")
            if key == "current" and str(val).upper() == "MAX":
                e.insert(0, "MAX")
            else:
                e.insert(0, str(val))

    def highlight(self, active: bool):
        color = "#1a3a5c" if active else "transparent"
        self.configure(fg_color=color)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PSUPilot")
        self.geometry("1200x720")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._driver: PSUDriver | None = None
        self._runner: CycleRunner | None = None
        self._driver_map = _load_driver_names()

        # Pending measurement from runner thread
        self._pending_measure: tuple[float, float] | None = None
        self._measure_lock = threading.Lock()
        self._plot_started = False

        self._build_ui()

        # Load limits for the default-selected PSU and wire up the warning callback
        self._load_psu_limits(self._psu_var.get())
        StepRow.set_warning_callback(self._show_editor_warning)

        self._plot_refresh_loop()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self._build_toolbar()
        self._build_editor()
        self._build_monitor()
        self._build_controls()

    # --- Toolbar ---
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, height=48, corner_radius=0)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")

        ctk.CTkLabel(bar, text="PSU:").pack(side="left", padx=(12, 4))
        driver_names = list(self._driver_map.keys()) or ["(no drivers)"]
        self._psu_var = ctk.StringVar(value=driver_names[0])
        self._psu_combo = ctk.CTkComboBox(bar, values=driver_names,
                                           variable=self._psu_var, width=220,
                                           command=self._on_psu_changed)
        self._psu_combo.pack(side="left", padx=4)

        ctk.CTkLabel(bar, text="COM:").pack(side="left", padx=(16, 4))
        ports = _list_com_ports()
        self._port_var = ctk.StringVar(value=ports[0])
        self._port_combo = ctk.CTkComboBox(bar, values=ports,
                                            variable=self._port_var, width=100)
        self._port_combo.pack(side="left", padx=4)

        self._refresh_btn = ctk.CTkButton(bar, text="⟳", width=32,
                                           command=self._refresh_ports)
        self._refresh_btn.pack(side="left", padx=2)

        self._connect_btn = ctk.CTkButton(bar, text="Connect", width=90,
                                           command=self._toggle_connect)
        self._connect_btn.pack(side="left", padx=(8, 4))

        self._status_label = ctk.CTkLabel(bar, text="Disconnected",
                                           text_color="#ff5555")
        self._status_label.pack(side="left", padx=8)

        # PSU limits display — updated whenever the PSU combo changes
        self._psu_limits_label = ctk.CTkLabel(bar, text="",
                                               text_color="#888888",
                                               font=("Consolas", 11))
        self._psu_limits_label.pack(side="left", padx=(16, 4))

        self._mode = "cv"  # always CVHS — controlled by UI ramp logic

    # --- Cycle editor ---
    def _build_editor(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="CYCLE EDITOR", font=("", 13, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        # Column headers
        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.grid(row=1, column=0, sticky="ew", padx=4)
        for col, (label, w) in enumerate(zip(StepRow.COLS, StepRow.WIDTHS)):
            ctk.CTkLabel(hdr, text=label, width=w, anchor="center",
                         font=("", 11, "bold"), text_color="#888888").grid(
                row=0, column=col, padx=1)

        # Scrollable step list
        self._step_scroll = ctk.CTkScrollableFrame(frame, width=290, height=320)
        self._step_scroll.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        frame.grid_rowconfigure(2, weight=1)

        self._step_rows: list[StepRow] = []
        # Add two default steps
        self._add_step()
        self._add_step()
        self._step_rows[1].set_values({"voltage": 0.0, "current": "MAX",
                                        "ramp": 1.0, "dwell": 5.0})

        # Buttons
        btn_bar = ctk.CTkFrame(frame, fg_color="transparent")
        btn_bar.grid(row=3, column=0, sticky="w", padx=4, pady=(0, 2))
        ctk.CTkButton(btn_bar, text="+ Add", width=70,
                      command=self._add_step).pack(side="left", padx=2)
        ctk.CTkButton(btn_bar, text="Remove", width=70,
                      command=self._remove_step).pack(side="left", padx=2)
        ctk.CTkButton(btn_bar, text="↑", width=36,
                      command=self._move_up).pack(side="left", padx=2)
        ctk.CTkButton(btn_bar, text="↓", width=36,
                      command=self._move_down).pack(side="left", padx=2)

        # Validation warning label
        self._editor_warning_label = ctk.CTkLabel(
            frame, text="", text_color="#ff4444",
            font=("Consolas", 11), anchor="w")
        self._editor_warning_label.grid(row=4, column=0, sticky="ew",
                                         padx=8, pady=(0, 4))

    # --- Live monitor ---
    def _build_monitor(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr, text="LIVE MONITOR", font=("", 13, "bold")).grid(
            row=0, column=0, sticky="w")

        self._sweep_btn = ctk.CTkButton(hdr, text="Sweep", width=65,
                                         fg_color="#1a5c8a", hover_color="#1e6fa8",
                                         command=lambda: self._set_graph_mode("sweep"))
        self._sweep_btn.grid(row=0, column=1, padx=(4, 2))
        self._full_btn = ctk.CTkButton(hdr, text="Full", width=65,
                                        fg_color="#333333", hover_color="#444444",
                                        command=lambda: self._set_graph_mode("full"))
        self._full_btn.grid(row=0, column=2, padx=(2, 4))
        ctk.CTkButton(hdr, text="Export CSV", width=90,
                      fg_color="#2d4a2d", hover_color="#3a6a3a",
                      command=self._export_csv).grid(row=0, column=3, padx=(4, 0))

        self._plot = LivePlot(frame)
        self._plot.grid(row=1, column=0, sticky="nsew", padx=0, pady=2)

        readout = ctk.CTkFrame(frame, fg_color="transparent")
        readout.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))

        self._v_label = ctk.CTkLabel(readout, text="V:  ---  V",
                                      font=("Consolas", 22, "bold"),
                                      text_color="#00d4ff")
        self._v_label.pack(anchor="w")
        self._i_label = ctk.CTkLabel(readout, text="I:  ---  A",
                                      font=("Consolas", 22, "bold"),
                                      text_color="#ff9900")
        self._i_label.pack(anchor="w")

    # --- Controls bar ---
    def _build_controls(self):
        bar = ctk.CTkFrame(self, height=62, corner_radius=0)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")

        # Left side: two rows, both left-aligned
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", padx=12, pady=6)

        # Row 1 — loops group, tightly packed
        loops_row = ctk.CTkFrame(left, fg_color="transparent")
        loops_row.pack(anchor="w")
        ctk.CTkLabel(loops_row, text="Loops:").pack(side="left", padx=(0, 8))
        self._loops_entry = ctk.CTkEntry(loops_row, width=52, justify="center")
        self._loops_entry.insert(0, "1")
        self._loops_entry.pack(side="left")
        ctk.CTkLabel(loops_row, text="(0 = ∞)", text_color="#888888",
                     font=("", 11)).pack(side="left", padx=(10, 0))

        # Row 2 — fixed-width progress labels, locked so nothing shifts
        prog_row = ctk.CTkFrame(left, fg_color="transparent")
        prog_row.pack(anchor="w", pady=(3, 0))
        self._step_label  = ctk.CTkLabel(prog_row, text="Step: –/–",  width=110,
                                          anchor="w", font=("Consolas", 12))
        self._loop_label  = ctk.CTkLabel(prog_row, text="Loop: –/–",  width=130,
                                          anchor="w", font=("Consolas", 12))
        self._dwell_label = ctk.CTkLabel(prog_row, text="Dwell: –",   width=160,
                                          anchor="w", font=("Consolas", 12))
        self._step_label.pack(side="left")
        self._loop_label.pack(side="left")
        self._dwell_label.pack(side="left")

        # Middle: run controls
        mid = ctk.CTkFrame(bar, fg_color="transparent")
        mid.pack(side="left", padx=20, pady=8)

        self._run_btn = ctk.CTkButton(mid, text="▶ Run", width=90,
                                       fg_color="#2d6a2d", hover_color="#3a8a3a",
                                       command=self._run)
        self._run_btn.grid(row=0, column=0, padx=4)

        self._pause_btn = ctk.CTkButton(mid, text="⏸ Pause", width=90,
                                         fg_color="#5a5a00", hover_color="#7a7a00",
                                         command=self._pause, state="disabled")
        self._pause_btn.grid(row=0, column=1, padx=4)

        self._stop_btn = ctk.CTkButton(mid, text="⏹ Stop", width=90,
                                        fg_color="#6a1a1a", hover_color="#8a2a2a",
                                        command=self._stop, state="disabled")
        self._stop_btn.grid(row=0, column=2, padx=4)

        # Right: save/load
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=12, pady=8)

        ctk.CTkButton(right, text="💾 Save Profile", width=130,
                      command=self._save_profile).pack(pady=2)
        ctk.CTkButton(right, text="📂 Load Profile", width=130,
                      command=self._load_profile).pack(pady=2)

    # -----------------------------------------------------------------------
    # Port helpers
    # -----------------------------------------------------------------------

    def _refresh_ports(self):
        ports = _list_com_ports()
        self._port_combo.configure(values=ports)
        self._port_var.set(ports[0])

    def _set_graph_mode(self, mode: str):
        self._plot.set_mode(mode)
        active   = "#1a5c8a"
        inactive = "#333333"
        active_h   = "#1e6fa8"
        inactive_h = "#444444"
        self._sweep_btn.configure(
            fg_color=active   if mode == "sweep" else inactive,
            hover_color=active_h if mode == "sweep" else inactive_h)
        self._full_btn.configure(
            fg_color=active   if mode == "full" else inactive,
            hover_color=active_h if mode == "full" else inactive_h)

    # -----------------------------------------------------------------------
    # PSU selection / limits
    # -----------------------------------------------------------------------

    def _on_psu_changed(self, value: str):
        self._load_psu_limits(value)
        # Re-validate all existing step entries against the new limits
        for row in self._step_rows:
            row.revalidate()

    def _load_psu_limits(self, psu_name: str):
        path = self._driver_map.get(psu_name)
        if path:
            max_v, max_i = _read_psu_limits(path)
        else:
            max_v, max_i = 9999.0, 9999.0
        StepRow.set_psu_limits(max_v, max_i)
        # Update toolbar label
        v_str = f"{max_v:.1f}" if max_v < 9999 else "—"
        i_str = f"{max_i:.1f}" if max_i < 9999 else "—"
        self._psu_limits_label.configure(text=f"Max: {v_str} V / {i_str} A")

    # -----------------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------------

    def _toggle_connect(self):
        if self._driver and self._driver.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        driver_name = self._psu_var.get()
        path = self._driver_map.get(driver_name)
        if not path:
            messagebox.showerror("Error", "No driver found for selected PSU.")
            return
        port = self._port_var.get()
        if port == "(none)":
            messagebox.showerror("Error", "No COM port selected.")
            return
        try:
            self._driver = PSUDriver(path)
            self._driver.connect(port)
            self._status_label.configure(
                text=f"{self._driver.full_name}  |  {port}",
                text_color="#55ff55")
            self._connect_btn.configure(text="Disconnect")
        except Exception as exc:
            self._driver = None
            messagebox.showerror("Connection Error", str(exc))

    def _disconnect(self):
        if self._runner and self._runner.is_running():
            self._stop()
        if self._driver:
            self._driver.disconnect()
            self._driver = None
        self._status_label.configure(text="Disconnected", text_color="#ff5555")
        self._connect_btn.configure(text="Connect")

    # -----------------------------------------------------------------------
    # Step editor operations
    # -----------------------------------------------------------------------

    def _add_step(self):
        row = StepRow(self._step_scroll, index=len(self._step_rows))
        row.pack(fill="x", pady=1)
        self._step_rows.append(row)

    def _remove_step(self):
        if len(self._step_rows) <= 1:
            return
        row = self._step_rows.pop()
        row.destroy()

    def _move_up(self):
        if len(self._step_rows) < 2:
            return
        self._swap_step_values(-2, -1)

    def _move_down(self):
        if len(self._step_rows) < 2:
            return
        self._swap_step_values(-1, -2)

    def _swap_step_values(self, a: int, b: int):
        va = self._step_rows[a].get_raw_values()
        vb = self._step_rows[b].get_raw_values()
        self._step_rows[a].set_values(vb)
        self._step_rows[b].set_values(va)

    def _show_editor_warning(self, msg: str):
        self._editor_warning_label.configure(text=msg)

    def _get_steps(self) -> list[dict] | None:
        steps = []
        max_v = StepRow._cls_max_voltage
        max_i = StepRow._cls_max_current
        for idx, row in enumerate(self._step_rows):
            try:
                s = row.get_values()
            except ValueError as exc:
                messagebox.showerror("Invalid input", f"Step {idx + 1}: {exc}")
                return None
            if s["voltage"] > max_v:
                messagebox.showerror("Out of range",
                                     f"Step {idx+1}: voltage {s['voltage']} V exceeds "
                                     f"PSU max of {max_v} V")
                return None
            if s["current"] > max_i:
                messagebox.showerror("Out of range",
                                     f"Step {idx+1}: current {s['current']} A exceeds "
                                     f"PSU max of {max_i} A")
                return None
            steps.append(s)
        return steps

    # -----------------------------------------------------------------------
    # Run / Pause / Stop
    # -----------------------------------------------------------------------

    def _run(self):
        if not self._driver or not self._driver.connected:
            messagebox.showerror("Error", "Not connected to PSU.")
            return
        steps = self._get_steps()
        if steps is None:
            return
        try:
            loops = int(self._loops_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid loop count.")
            return

        self._driver.set_mode(self._mode)  # re-apply selected mode before run
        self._plot.reset()       # clear previous run data
        self._plot_started = False  # block samples until first dwell begins
        self._set_run_state(True)
        self._total_steps = len(steps)
        self._total_loops = loops

        self._runner = CycleRunner(
            psu=self._driver,
            steps=steps,
            loops=loops,
            on_step=self._cb_step,
            on_dwell_tick=self._cb_dwell,
            on_measure=self._cb_measure,
            on_loop_complete=self._cb_loop,
            on_done=self._cb_done,
            on_error=self._cb_error,
        )
        self._runner.start()

    def _pause(self):
        if self._runner is None:
            return
        if self._pause_btn.cget("text") == "⏸ Pause":
            self._runner.pause()
            self._pause_btn.configure(text="▶ Resume")
        else:
            self._runner.resume()
            self._pause_btn.configure(text="⏸ Pause")

    def _stop(self):
        if self._runner:
            self._runner.stop()

    def _set_run_state(self, running: bool):
        state_run = "disabled" if running else "normal"
        state_ctrl = "normal" if running else "disabled"
        self._run_btn.configure(state=state_run)
        self._pause_btn.configure(state=state_ctrl, text="⏸ Pause")
        self._stop_btn.configure(state=state_ctrl)

    # -----------------------------------------------------------------------
    # Runner callbacks (called from background thread → schedule via after)
    # -----------------------------------------------------------------------

    def _cb_step(self, step_idx: int, loop_idx: int):
        self.after(0, lambda: self._update_progress(step_idx, loop_idx, None))
        self.after(0, lambda: self._highlight_step(step_idx))

    def _cb_dwell(self, remaining: float):
        if not self._plot_started:
            with self._measure_lock:
                self._pending_measure = None   # discard ramp samples
                self._plot_started = True      # set inside lock — atomic with the flush
        self.after(0, lambda r=remaining: self._update_dwell(r))

    def _cb_measure(self, v: float, i: float):
        with self._measure_lock:
            self._pending_measure = (v, i)

    def _cb_loop(self, loop_idx: int):
        pass  # progress updates already handled

    def _cb_done(self):
        self.after(0, self._on_run_finished)

    def _cb_error(self, msg: str):
        self.after(0, lambda m=msg: messagebox.showerror("PSU Error", m))

    # -----------------------------------------------------------------------
    # GUI updates from callbacks
    # -----------------------------------------------------------------------

    def _update_progress(self, step_idx: int, loop_idx: int, dwell_remaining):
        n_steps = self._total_steps
        n_loops = self._total_loops
        self._step_label.configure(text=f"Step: {step_idx + 1}/{n_steps}")
        self._loop_label.configure(text=f"Loop: {loop_idx}/{'∞' if n_loops == 0 else n_loops}")
        if dwell_remaining is not None:
            self._dwell_label.configure(text=f"Dwell: {dwell_remaining:.0f}s left")

    def _update_dwell(self, remaining: float):
        self._dwell_label.configure(text=f"Dwell: {remaining:.0f}s left")

    def _highlight_step(self, active_idx: int):
        for i, row in enumerate(self._step_rows):
            row.highlight(i == active_idx)

    def _on_run_finished(self):
        self._set_run_state(False)
        self._step_label.configure(text="Step: –/–")
        self._loop_label.configure(text="Loop: –/–")
        self._dwell_label.configure(text="Dwell: –")
        for row in self._step_rows:
            row.highlight(False)

    # -----------------------------------------------------------------------
    # Plot refresh loop (runs on main thread via after)
    # -----------------------------------------------------------------------

    def _plot_refresh_loop(self):
        with self._measure_lock:
            sample = self._pending_measure if self._plot_started else None
            self._pending_measure = None

        if sample is not None:
            v, i = sample
            self._plot.add_sample(v, i)
            self._v_label.configure(text=f"V:  {v:7.3f}  V")
            self._i_label.configure(text=f"I:  {i:7.3f}  A")
            self._plot.refresh()

        self.after(400, self._plot_refresh_loop)

    # -----------------------------------------------------------------------
    # Save / Load profiles
    # -----------------------------------------------------------------------

    def _export_csv(self):
        times, voltages, currents = self._plot.get_data()
        if not times:
            messagebox.showinfo("Export CSV", "No data to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Time (s)", "Voltage (V)", "Current (A)"])
                for t, v, i in zip(times, voltages, currents):
                    writer.writerow([f"{t:.3f}", f"{v:.4f}", f"{i:.4f}"])
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    def _save_profile(self):
        steps = []
        for idx, row in enumerate(self._step_rows):
            try:
                s = row.get_raw_values()
            except ValueError as exc:
                messagebox.showerror("Invalid input", f"Step {idx + 1}: {exc}")
                return
            steps.append(s)

        try:
            loops = int(self._loops_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid loop count.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")],
            initialdir=PROFILES_DIR,
        )
        if not path:
            return
        try:
            profile = {"loops": loops, "steps": steps}
            with open(path, "w") as f:
                json.dump(profile, f, indent=2)
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))

    def _load_profile(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")],
            initialdir=PROFILES_DIR,
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)

            # Support both old format (bare list) and new format (dict with loops/steps)
            if isinstance(data, list):
                steps = data
                loops = 1
            else:
                steps = data.get("steps", [])
                loops = data.get("loops", 1)

            # Rebuild step rows
            while self._step_rows:
                self._step_rows.pop().destroy()
            for step in steps:
                self._add_step()
                self._step_rows[-1].set_values(step)

            # Restore loop count
            self._loops_entry.delete(0, "end")
            self._loops_entry.insert(0, str(loops))

        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))

    # -----------------------------------------------------------------------
    # Close
    # -----------------------------------------------------------------------

    def _on_close(self):
        if self._runner and self._runner.is_running():
            self._stop()
        if self._driver:
            self._driver.disconnect()
        self.destroy()
