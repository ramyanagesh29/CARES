# CARES: Cognitive Assistance and Response Emergency System for Visually Impaired Individuals

## Project Overview

**CARES** is a research-oriented emergency response and cognitive assistance platform designed for visually impaired individuals. It monitors real-time physiological indicators, identifies cognitive-risk distress situations, and adaptively escalates emergency response actions for guardian intervention.

---

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env  (then edit .env)
python -m backend.server    # open http://127.0.0.1:8000
python -m pytest -v         # run the tests
```

Full beginner guide: `docs/CARES_SETUP_GUIDE.md`. Hardware flow: `docs/CARES_HARDWARE_INTEGRATION.md`. Audit and test report: `docs/CARES_AUDIT_AND_TEST_REPORT.md`.

---

## Fixed Project Requirements

### Problem Statement
> *"Existing assistive systems for visually impaired individuals focus mainly on physical safety and basic health monitoring but lack real-time cognitive support during abnormal situations."*

### Objectives
1. **Real-time Cognitive-Risk Monitoring**: To monitor physiological conditions in real time and identify abnormal cognitive-risk situations in visually impaired individuals.
2. **Adaptive Emergency Response**: To develop an adaptive emergency response mechanism that performs intelligent alert escalation based on physiological condition severity.
3. **Guardian Intervention & Support**: To enable immediate guardian intervention by sharing live location and supporting real-time emotional assistance during emergencies.

### Intended Methodology Pipeline
```
Physiological / Wearable Sensing
  │
  ▼
Communication
  │
  ▼
Mobile Application
  │
  ▼
Cognitive Risk Analysis Module
  │
  ▼
LOW / MEDIUM / HIGH Risk State
  │
  ▼
Adaptive Response
  │
  ▼
Guardian Support
  │
  ▼
Live Location
  │
  ▼
Communication / Emotional Assistance
  │
  ▼
Emergency Assistance
  │
  ▼
Incident Logging
```

---

## Architecture & Directory Structure

```
CARES/
├── engine/
│   ├── __init__.py         # Engine package initialization and exports
│   ├── models.py           # Validated physiological sample & result models
│   ├── baseline.py         # Personal baseline estimator
│   ├── features.py         # Deterministic temporal feature extractor
│   ├── risk_engine.py      # CARES Adaptive Cognitive-Risk Decision Engine
│   ├── escalation.py       # Adaptive escalation state machine
│   └── config.py           # Centralized configuration parameters
│
├── simulation/
│   ├── __init__.py         # Simulation package exports
│   ├── generator.py        # Synthetic physiological stream scenario generator
│   └── replay.py           # Stream replay engine for test verification
│
├── evaluation/
│   ├── __init__.py         # Evaluation package exports
│   ├── baselines.py        # Comparative naive static threshold baselines
│   └── metrics.py          # Research evaluation metrics (FPR, latency, churn)
│
├── guardian/
│   ├── __init__.py         # Guardian package exports
│   └── actions.py          # Guardian action software contract & mapping layer
│
├── ui/                     # UI components (Reserved for future milestone)
│
├── data/
│   └── scenarios/          # JSON benchmark scenario data files
│       ├── normal_resting.json
│       ├── transient_spike.json
│       ├── sustained_panic.json
│       └── stress_and_recovery.json
│
├── tests/                  # Pytest verification suite
│   ├── test_baseline.py
│   ├── test_features.py
│   ├── test_risk_engine.py
│   ├── test_escalation.py
│   ├── test_guardian_actions.py
│   ├── test_simulation_eval.py
│   └── test_invalid_input.py
│
├── results/                # Evaluation output artifacts directory
├── docs/                   # Documentation and research specifications
├── requirements.txt        # Python dependency manifest
└── README.md               # Project documentation
```

---

## Decision Engine Pipeline

The core technical contribution of CARES is the **Adaptive Cognitive-Risk Decision Engine**. Rather than relying on static, population-wide thresholds (`if heart_rate > threshold: HIGH`), the CARES engine reasons over temporal physiological behavior using a multi-stage deterministic pipeline:

```
Sample Input ➔ Baseline Estimation ➔ Feature Extraction ➔ Continuous Scoring ➔ State Machine ➔ Explainable Output & Actions
```

### 1. Personal Baseline Estimation (`engine/baseline.py`)
- Derives individualized reference parameters (mean, median, standard deviation) from an initial calibration window rather than universal constants.
- Assumes the initial reference window represents a resting/non-stress baseline.
- Supports slow adaptive drift updating during confirmed resting/LOW risk periods.

### 2. Temporal Feature Extraction (`engine/features.py`)
Deterministic extraction of five core temporal metrics:
1. **Absolute Deviation**: $Dev = HR_{current} - HR_{baseline}$ (bpm)
2. **Percentage Deviation**: $PctDev = \frac{HR_{current} - HR_{baseline}}{HR_{baseline}} \times 100\%$
3. **Short-Term Trend / Rate of Change**: Slope $\frac{\Delta HR}{\Delta t}$ over a sliding time window (bpm/sec).
4. **Abnormality Persistence**: Temporal duration (seconds) and sample count of sustained deviation above threshold.
5. **Recovery Behavior**: Detection of negative rate of change ($\frac{\Delta HR}{\Delta t} \le \text{threshold}_{rec}$) while returning toward baseline.

### 3. Continuous Risk Scoring & Evidence Confidence (`engine/risk_engine.py`)
- Computes a continuous risk score ($0.0 - 100.0$) combining deviation magnitude, percentage deviation, and trend acceleration.
- Evaluates evidence confidence ($0.0 - 1.0$) based on sample buffer depth, baseline readiness, and persistence stability.

### 4. Adaptive Escalation State Machine (`engine/escalation.py`)
Prevents false alarms caused by single transient noisy samples by enforcing temporal persistence and confidence hysteresis:
- **LOW $\rightarrow$ MEDIUM**: Requires continuous candidate elevation for $\ge 3$ consecutive samples.
- **MEDIUM $\rightarrow$ HIGH**: Requires continuous severe elevation for $\ge 5$ consecutive samples AND evidence confidence $\ge 0.6$.
- **Transient Immunity**: Single isolated noisy spikes are filtered out without escalating to HIGH.
- **De-escalation (HIGH $\rightarrow$ MEDIUM $\rightarrow$ LOW)**: Requires systematic evidence of returning to normal baseline bounds over $\ge 5$ consecutive samples or active recovery trend.

### 5. Explainable Reason Codes & Narrative
Every decision produces explicit, human-readable explanations accompanied by structured reason codes:
- `BASELINE_DEVIATION`
- `HIGH_PERCENTAGE_DEVIATION`
- `RISING_TREND`
- `PERSISTENT_ABNORMALITY`
- `RAPID_CHANGE`
- `RECOVERY_DETECTED`
- `ESCALATION_CONFIRMED`
- `DEESCALATION_CONFIRMED`
- `STABLE_BASELINE`
- `BASELINE_CALIBRATING`

---

## Guardian Action Contract

The engine output maps directly to software action commands defined in `guardian/actions.py`:

| Risk Level | Mapped Software Action Commands |
| :--- | :--- |
| **LOW** | `CONTINUE_MONITORING` |
| **MEDIUM** | `USER_WARNING`, `CONTINUE_MONITORING` |
| **HIGH** | `EMERGENCY_ALERT`, `GUARDIAN_NOTIFICATION`, `LOCATION_SHARE`, `GUARDIAN_COMMUNICATION`, `INCIDENT_LOG` |

---

## Hardware Role, Demo and Validation Status

> **IMPORTANT NOTICE ON CLINICAL & HARDWARE STATUS:**  
> CARES is a research prototype for physiological-risk assessment and emergency escalation. It is **not** a medically certified diagnostic or emergency-response system.  
> All thresholds in `engine/config.py` are algorithm and simulation parameters for software verification and research prototyping.

**What the hardware contributes.** The ESP32 + NEO-6M GPS device supplies **GPS location and a timestamp** to the backend (`POST /api/hardware/ingest`, authenticated with `X-Device-Token`). The dashboard shows the latest fix, and HIGH-risk incidents and guardian SMS messages include it **only when it is recent** (default: no older than 5 minutes) and belongs to the same user. Otherwise the message says the location is unavailable. A MAX30102 heart-rate/SpO2 reading may optionally accompany a payload; it is not required.

**What the HIGH-risk demo is.** The built-in demo (`/api/demo/*`, dashboard "Start demo") streams clearly labelled synthetic or WESAD-derived samples through the normal CARES engine. Its guardian notification is **MOCK (no real SMS)** unless a real-SMS demonstration is explicitly enabled and requested. See `SMS_SETUP.md`.

**Verification status.** The software is covered by the automated test suite (mocked SMS/network only). The ESP32 firmware, GPIO/GPS wiring, Wi-Fi and device-token behaviour on the physical device have **not** been verified in this repository: the firmware source is not included. See `docs/CARES_HARDWARE_INTEGRATION.md`.

---

## Running Verification Tests

To execute the test suite (no real SMS is ever sent by the tests):

```bash
python -m pytest -v
```

To run a deterministic example through the full decision engine:

```bash
python -c "
from engine import CARESDecisionEngine, PhysiologicalSample

engine = CARESDecisionEngine()
# Calibrate baseline
for t in range(30):
    engine.process_sample(PhysiologicalSample(timestamp=float(t), heart_rate_bpm=70.0))

# Elevated panic sample
sample = PhysiologicalSample(timestamp=30.0, heart_rate_bpm=115.0)
output = engine.process_sample(sample)
print(output.to_dict())
"
```

---

## Limitations & Future Integration Roadmap

### Current Limitations
1. **Software-in-the-Loop Input**: Risk demonstrations use synthetic or replayed WESAD-derived input, always labelled as such.
2. **Single Physiological Signal**: Currently models heart rate (bpm) as the primary sensor stream (extensible via `additional_metrics`).
3. **Hardware not verified here**: GPS/timestamp ingestion is implemented and software-tested on the server side; the device firmware and wiring still require physical verification (`docs/CARES_HARDWARE_INTEGRATION.md`).
4. **SMS delivery**: TextBee acceptance is not handset delivery; delivery is only claimed when TextBee reports it.

### Future Roadmap (Post Milestone 1)
- **Physical hardware verification**: end-to-end checks of the ESP32 GPS/timestamp path (checklist in `docs/CARES_HARDWARE_INTEGRATION.md`) and, optionally, continuous PPG sensing.
- **Multimodal Sensing**: Integration of Electrodermal Activity (EDA / Skin Conductance) and IMU motion data.
- **Mobile UI & Live Guardian Portal**: Real-time Flutter / React Native user interface consuming the JSON `EngineOutput` contracts.
