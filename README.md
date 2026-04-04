# QSHIELD - Quantum-Safe Drone Communications Defense Console

QSHIELD is a military-style cyber operations console built for drone command-and-control security evaluation under active attack conditions. It combines deterministic threat simulation, replay/spoof/timing detection, stress-matrix validation, and encrypted traffic intelligence workflows for both TCP and UART captures.

Live Deployment: https://drone-a-hackgit-og9bik7i8xgnuf7eqeappgv.streamlit.app

## Why This Project Exists

Modern drone links face two parallel risks:

- Real-time protocol attacks (replay, spoofing, timing side-channel probing)
- Future cryptographic risk from Harvest-Now-Decrypt-Later strategies

QSHIELD addresses both by combining operational anomaly detection with a quantum-safe security narrative and explainable mission scoring.

## Core Capabilities

- Deterministic RF packet simulation with repeatable attack injection
- Detection engine for replay, spoof, and timing side-channel anomalies
- Scenario Stress Matrix with PASS/FAIL thresholds across multiple hostile profiles
- Judge-facing command center UI with military-themed dashboards and explainability
- Encrypted traffic intelligence analysis for uploaded datasets
- Track A TCP analysis (CSV and PCAP/PCAPNG)
- Track A Encrypted UART analysis (chunked ingest, packet boundary detection, timing and entropy intelligence)
- Analyst assistant with Gemini support and robust local fallback mode

## Live Demo

- App URL: https://drone-a-hackgit-og9bik7i8xgnuf7eqeappgv.streamlit.app
- Recommended browser: Chrome/Edge latest
- For best demo quality, use the default seed first, then switch profiles to show dynamic behavior

## Project Architecture

### 1. Presentation Layer

- `streamlit_app.py`
   - Main command-center interface
   - Stress matrix, threat cards, explainability panels
   - Track A TCP and UART upload workflows
   - Artifact downloads (briefs, logs, reports)

### 2. Simulation Layer

- `simulation_engine.py`
   - Packet model (`Packet` dataclass)
   - Deterministic traffic generation (`DroneSimulationEngine`)
   - Replay/spoof/timing probe attack injectors

### 3. Detection Layer

- `qshield_engine.py`
   - `QShieldEngine` with three primary checks:
      - Replay fingerprint matching and evasive replay signatures
      - Spoof detection via token, identity, RF baseline, and latency heuristics
      - Timing side-channel detection using rolling deviation around key-exchange moments

### 4. Visual Intelligence Layer

- `visualizer.py`
   - Composite dashboard rendering for mission brief visuals

### 5. Forensic Analysis Tools

- `tcp_qore_analyzer.py`
   - Dataset parsing (CSV/PCAP)
   - Replay and statistical anomaly discovery
- `uart_qore_analyzer.py`
   - Chunked UART byte-stream analysis for encrypted captures
   - Packet segmentation via inter-byte gap thresholds
   - Timing anomaly labeling:
      - `KEY_EXCHANGE_CANDIDATE`
      - `BURST_REPLAY_CANDIDATE`
   - Attack vector scoring and 6-panel dashboard generation

## UART Intelligence Workflow (Track A)

The UART analyzer is designed for encrypted one-byte capture streams and metadata-only intelligence.

### Input Shape

Expected CSV format:

`name, type, start_time, duration, data`

### Packet Classification Buckets

All packets are classified into one of the following size groups:

- `1` byte: single byte ACK
- `2-5` bytes: short command frame
- `6-15` bytes: standard command with parameters
- `16-50` bytes: telemetry burst
- `51-100` bytes: extended telemetry
- `100+` bytes: large data transfer or firmware fragment

### UART Artifacts Generated

- `uart_dashboard.png`
- `packets_detected.csv`
- `timing_anomalies.csv`
- `uart_report.txt`

## Stress Matrix Logic

The app auto-runs hardened scenarios:

- `stealth_spoof`
- `burst_replay`
- `timing_heavy_probe`
- `mixed_swarm`

Each scenario is judged with threshold gates on precision, recall, and false positives, then combined into mission confidence scoring.

## Local Setup

1. Clone repository

```bash
git clone https://github.com/VenkateshReddy007/Drone-a-hAcK.git
cd Drone-a-hAcK
```

2. Create environment file

```bash
copy .env.example .env
```

3. Configure optional Gemini keys in `.env`

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
```

4. Install dependencies

```bash
pip install -r requirements.txt
```

5. Run app

```bash
py -3 -m streamlit run streamlit_app.py --server.port 8501
```

## Streamlit Cloud Notes

- Upload handling and runner tuning are configured in `.streamlit/config.toml`
- If Gemini is required in cloud runtime, set secrets in app settings:

```toml
GEMINI_API_KEY = "your_key"
GEMINI_MODEL = "gemini-2.5-flash"
```

## Security and Secrets

- Never commit real API keys
- Keep `.env` local-only
- Use Streamlit Cloud secrets for production deployment

## Tech Stack

- Python 3
- Streamlit
- Pandas
- NumPy
- Altair
- Matplotlib
- Scapy

## Hackathon Positioning

Built for Army drone-security demonstrations where judges need:

- Clear attack modeling
- Explainable defensive logic
- Reproducible metrics
- Operationally styled intelligence outputs

QSHIELD was engineered to satisfy all four in one deployable console.
