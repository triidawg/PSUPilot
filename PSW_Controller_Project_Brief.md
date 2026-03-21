# PSW160 Controller — Project Brief
*Prepared for handoff to Claude Code*

---

## Project Summary

A desktop GUI application for Windows that allows easy programming of the **GW Instek PSW160-7.2** programmable power supply. The app lets users visually build voltage/current cycle sequences with ramp times and dwell times, run them, monitor live output, and save/load profiles. It is also designed from the start to support **multiple PSU models** via a JSON driver system.

---

## Tech Stack

| Component | Choice |
|---|---|
| Language | Python |
| GUI framework | CustomTkinter |
| Instrument communication | pyvisa-py (no NI-VISA needed) |
| Live plotting | matplotlib (embedded in GUI) |
| Profile/driver storage | JSON files |
| Connection | USB → virtual COM port (Windows) |

**Required packages:** `customtkinter`, `pyvisa-py`, `matplotlib`

---

## Core Features

### 1. Cycle Editor
- Table-based step editor with columns: Step №, Voltage (V), Current limit (A), Ramp time (s), Dwell time (s)
- Add / remove / reorder steps (up/down buttons)
- Input validation against PSU limits

### 2. Ramping
- The PSW160 has no native ramp command
- Simulate ramps in software by sending many small incremental voltage steps over the ramp duration
- Aim for ~50 steps per ramp for smooth appearance

### 3. Loop / Repeat Control
- User can set number of loop repetitions (1 to ∞)
- Loop counter visible during run

### 4. Live Monitor Panel
- Real-time voltage and current plot (matplotlib, updates ~2–5 Hz)
- Large numeric readouts for current V and I
- Plot scrolls to show last N seconds of data

### 5. Run Controls
- ▶ Run, ⏸ Pause, ⏹ Stop buttons
- Progress indicator: current step, loop number, time remaining in dwell
- Output is turned OFF safely on stop or on app close

### 6. Save / Load Profiles
- Cycle steps saved as `.json` files
- User picks filename via standard file dialog
- Simple human-readable format (list of step objects)

---

## PSU Driver System (multi-PSU support)

Each supported PSU has its own JSON driver file in a `drivers/` folder. The app loads the selected driver at startup and uses its commands throughout — no hardcoded SCPI strings in the main app logic.

### Example driver file: `drivers/gw_instek_psw160.json`

```json
{
  "name": "GW Instek PSW160-7.2",
  "max_voltage": 160.0,
  "max_current": 7.2,
  "connection": "USB",
  "termination": "\n",
  "commands": {
    "set_voltage":       "VOLT {v}",
    "set_current":       "CURR {i}",
    "output_on":         "OUTP ON",
    "output_off":        "OUTP OFF",
    "measure_voltage":   "MEAS:VOLT?",
    "measure_current":   "MEAS:CURR?"
  }
}
```

- Adding a new PSU = create a new driver JSON file, no code changes needed
- App scans `drivers/` folder at startup and populates a PSU selector dropdown
- Commands with `{v}` or `{i}` placeholders are formatted at runtime

---

## UI Layout

```
┌────────────────────────────────────────────────────────┐
│  PSU: [GW Instek PSW160-7.2 ▼]  COM: [COM3 ▼] [Connect]│
├───────────────────────────┬────────────────────────────┤
│  CYCLE EDITOR             │  LIVE MONITOR              │
│                           │                            │
│  # │  V  │  A  │Ramp│Dwell│  [matplotlib plot]         │
│  1 │12.0 │ 2.0 │ 2s │ 10s │                            │
│  2 │24.0 │ 1.5 │ 5s │ 30s │  V: 23.4 V                 │
│  3 │ 0.0 │ 0.0 │ 1s │  5s │  I:  1.48 A                │
│                           │                            │
│ [+Add][Remove][↑][↓]      │                            │
├───────────────────────────┴────────────────────────────┤
│  Loops: [3]   Step: 2/3   Loop: 1/3   Dwell: 12s left  │
│  [▶ Run]  [⏸ Pause]  [⏹ Stop]                          │
│  [💾 Save Profile]   [📂 Load Profile]                  │
└────────────────────────────────────────────────────────┘
```

---

## File Structure

```
psw_controller/
├── main.py                  # Entry point
├── app.py                   # Main CustomTkinter window
├── cycle_runner.py          # Step sequencer, ramp logic, loop control
├── psu_driver.py            # Loads driver JSON, wraps pyvisa communication
├── live_plot.py             # Matplotlib embed, data buffer, update loop
├── drivers/
│   └── gw_instek_psw160.json
├── profiles/                # User-saved cycle profiles go here
└── requirements.txt
```

---

## Safety Requirements

- Output must be turned OFF (`OUTP OFF`) when: Stop is pressed, an error occurs, app window is closed
- Validate all voltage/current values against driver's `max_voltage` / `max_current` before sending
- Catch `pyvisa` communication errors gracefully — show error message, do not crash
- Never send a new command while a previous one is still executing

---

## Out of Scope (for now)

- CSV data logging (can be added later)
- Mac / Linux support
- Remote/network control of the app itself
- Automated test sequences / pass-fail logic

---

## Notes for Claude Code

- Use `pyvisa-py` backend, NOT NI-VISA (user does not have NI-VISA installed)
- COM port list should be auto-detected and shown in a dropdown
- CustomTkinter supports both light and dark mode — default to dark
- The matplotlib plot should be embedded using `FigureCanvasTkAgg`
- Ramp logic should run in a background thread to keep the UI responsive
- Use `threading.Event` for pause/stop signalling to the runner thread

