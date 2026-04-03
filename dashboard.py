"""QSHIELD end-to-end demo runner.

Runs simulation, executes detection engine, renders dashboard, and prints
an operator-facing summary report.
"""

from __future__ import annotations

from collections import Counter

from qshield_engine import run_qshield_detection
from simulation_engine import DroneSimulationEngine
from visualizer import create_dashboard


def print_summary(result) -> None:
    """Print concise final report suitable for live demo narration."""
    print("\n" + "=" * 66)
    print("QSHIELD FINAL SUMMARY REPORT")
    print("=" * 66)
    print(f"Packets analyzed         : {result.total_packets}")
    print(f"Packets blocked          : {result.blocked_packets}")
    print(f"Detection rate           : {result.detection_rate:.2f}%")

    breakdown = Counter(alert.attack_type for alert in result.alerts)
    print("\nAttack breakdown:")
    for attack_type in ["replay", "spoof", "timing_side_channel"]:
        print(f"  - {attack_type:20s}: {breakdown.get(attack_type, 0)}")

    print("\nSample alerts:")
    if not result.alerts:
        print("  - No alerts generated.")
    else:
        for alert in result.alerts[:8]:
            print(
                f"  - Seq {alert.sequence_id:03d} | {alert.attack_type:20s} "
                f"| confidence={alert.confidence:.2f} | {alert.detail}"
            )

    status = "HARDENED" if result.blocked_packets > 0 else "AT RISK"
    print(f"\nSystem status            : {status}")
    print("Dashboard image          : qshield_dashboard.png")
    print("=" * 66 + "\n")


def main() -> None:
    """Single entry point for the complete QSHIELD simulation demo."""
    simulator = DroneSimulationEngine(seed=515)
    packets = simulator.generate_packets(total_packets=70)

    result = run_qshield_detection(packets)
    create_dashboard(result, output_path="qshield_dashboard.png")
    print_summary(result)


if __name__ == "__main__":
    main()
