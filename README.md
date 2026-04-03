# QSHIELD Drone RF Security Demo

## Overview

QSHIELD is a tactical RF security evaluation framework for drone command-and-control systems. This Streamlit dashboard demonstrates quantum-safe detection of replay attacks, spoofing, and timing side-channel exploits.

## Features

- **Real-time packet simulation** with deterministic seeding
- **Three attack vectors:** replay, spoof, timing side-channel
- **QSHIELD defense** with baseline comparison
- **AI analyst Q&A** powered by Google Gemini (with local fallback)
- **Performance metrics:** precision, recall, latency overhead

## Local Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/VenkateshReddy007/Drone-a-hAcK.git
   cd Drone-a-hAcK
   ```

2. Create a `.env` file (copy from `.env.example`):
   ```bash
   cp .env.example .env
   ```

3. Add your Gemini API key to `.env`:
   ```
   GEMINI_API_KEY=your_key_here
   GEMINI_MODEL=gemini-2.5-flash
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

5. Run the app:
   ```bash
   streamlit run streamlit_app.py
   ```

The app will open at `http://localhost:8503`

## Architecture

- **streamlit_app.py** - Main UI and Gemini integration
- **qshield_engine.py** - Detection logic (replay, spoof, timing)
- **simulation_engine.py** - Packet generation with attack scenarios
- **visualizer.py** - Dashboard charts and metrics
- **dashboard.py** - Legacy dashboard component

## Gemini Integration

The analyst Q&A uses Google's Gemini API to answer questions about the current run. If no API key is provided, the app falls back to deterministic local analysis.

### Streamlit Cloud Secrets

To enable Gemini on the live deployment:
1. Go to app settings → Secrets
2. Add:
   ```
   GEMINI_API_KEY = "your_key"
   GEMINI_MODEL = "gemini-2.5-flash"
   ```
3. Save and reboot

## Army Drone Hackathon

Built for the Army Drone Hackathon with focus on quantum-safe RF security.
