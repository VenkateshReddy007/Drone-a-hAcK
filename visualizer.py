"""QSHIELD dark-theme dashboard generator."""

from __future__ import annotations

from collections import Counter
import math
from typing import List

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Wedge, Circle
from matplotlib import patheffects

from simulation_engine import Packet
from qshield_engine import Alert, QShieldRunResult


BG = "#0a0a1a"
SAFE = "#00ff88"
ATTACK = "#ff4444"
WARN = "#ff8800"
GRID = "#1b2b2f"


def _theme_setup() -> None:
    """Configure a high-contrast dark style suitable for command-center presentation."""
    plt.style.use("dark_background")
    plt.rcParams.update(
        {
            "figure.facecolor": BG,
            "axes.facecolor": "#101024",
            "axes.edgecolor": "#2a3a3f",
            "axes.labelcolor": "#cfd8dd",
            "xtick.color": "#b9c7cc",
            "ytick.color": "#b9c7cc",
            "text.color": "#e4f0ea",
            "axes.titleweight": "bold",
            "font.size": 10,
        }
    )


def _style_panel(ax, title: str) -> None:
    """Apply consistent tactical panel styling and radar-like grid lines."""
    ax.set_title(title, loc="left", fontsize=11, color="#dff5ea", pad=10)
    for spine in ax.spines.values():
        spine.set_color("#2a3a3f")
        spine.set_linewidth(1.0)
    ax.grid(True, which="major", color=GRID, linestyle="-", alpha=0.35, linewidth=0.8)
    ax.minorticks_on()
    ax.grid(True, which="minor", color=GRID, linestyle=":", alpha=0.2, linewidth=0.6)


def _draw_header(fig) -> None:
    """Render a large stencil-style title strip across the top."""
    logo = "QSHIELD // CLASSICAL CHANNEL DEFENSE"
    txt = fig.text(
        0.02,
        0.965,
        logo,
        color=SAFE,
        fontsize=27,
        family="monospace",
        fontweight="bold",
        va="top",
        ha="left",
    )
    txt.set_path_effects(
        [
            patheffects.withStroke(linewidth=5, foreground="#02140d", alpha=0.95),
        ]
    )
    fig.text(
        0.02,
        0.934,
        "TACTICAL CYBER OPERATIONS CENTER // QKD CLASSICAL-LAYER PROTECTION",
        color="#9fc2b3",
        fontsize=10,
        family="monospace",
        ha="left",
        va="top",
    )


def _draw_threat_gauge(ax, threat_score: float) -> None:
    """Draw a speedometer-like semi-circular threat gauge."""
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_facecolor("#101024")

    bands = [
        (180, 120, SAFE),
        (120, 60, WARN),
        (60, 0, ATTACK),
    ]
    for start, end, color in bands:
        ax.add_patch(Wedge((0, 0), 1.0, end, start, width=0.2, facecolor=color, alpha=0.85))

    # Outer ring and tick marks make the gauge feel instrument-grade.
    ax.add_patch(Wedge((0, 0), 1.05, 0, 180, width=0.02, facecolor="#4d5f65", alpha=0.9))
    for value in range(0, 101, 10):
        angle = 180 - value * 1.8
        rad = math.radians(angle)
        inner = 0.78 if value % 20 else 0.74
        outer = 0.98
        x1, y1 = inner * math.cos(rad), inner * math.sin(rad)
        x2, y2 = outer * math.cos(rad), outer * math.sin(rad)
        ax.plot([x1, x2], [y1, y2], color="#cfd8dd", linewidth=1.2 if value % 20 == 0 else 0.8)
        if value % 20 == 0:
            lx, ly = 0.66 * math.cos(rad), 0.66 * math.sin(rad)
            ax.text(lx, ly, str(value), color="#b7c6cc", fontsize=9, ha="center", va="center")

    # Needle maps 0..100 threat score to 180..0 degree arc.
    angle = 180 - (max(0.0, min(100.0, threat_score)) * 1.8)
    needle_x = 0.77 * math.cos(math.radians(angle))
    needle_y = 0.77 * math.sin(math.radians(angle))
    ax.plot([0, needle_x], [0, needle_y], color="#f6f7f8", linewidth=3.2, zorder=5)
    ax.add_patch(Circle((0, 0), 0.06, color="#f6f7f8", zorder=6))
    ax.add_patch(Circle((0, 0), 0.03, color="#2c3f4a", zorder=7))

    level = "SAFE" if threat_score < 35 else "GUARDED" if threat_score < 70 else "CRITICAL"
    level_color = SAFE if level == "SAFE" else WARN if level == "GUARDED" else ATTACK
    ax.text(0, -0.12, f"THREAT INDEX {threat_score:.1f}", ha="center", va="center", fontsize=11, weight="bold")
    ax.text(0, -0.23, level, ha="center", va="center", fontsize=10, color=level_color, weight="bold")
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-0.28, 1.1)


def _draw_live_packet_feed(ax, packets: List[Packet], alerts: List[Alert]) -> None:
    """Render a scrolling-style right-side feed with the latest 10 packets."""
    ax.axis("off")
    ax.set_facecolor("#0f1024")

    attack_sequences = {alert.sequence_id for alert in alerts}
    recent_packets = packets[-10:]

    ax.text(
        0.03,
        0.97,
        "LIVE PACKET FEED",
        color="#dff5ea",
        fontsize=12,
        family="monospace",
        weight="bold",
        ha="left",
        va="top",
    )
    ax.text(
        0.03,
        0.925,
        "LAST 10 FRAMES // REAL-TIME MONITOR",
        color="#8cab9f",
        fontsize=8,
        family="monospace",
        ha="left",
        va="top",
    )

    y = 0.86
    step = 0.078
    for idx, packet in enumerate(recent_packets):
        is_attack = packet.sequence_id in attack_sequences
        is_warning = packet.is_key_exchange_moment and not is_attack
        row_color = ATTACK if is_attack else WARN if is_warning else SAFE
        alpha = 0.95 - ((len(recent_packets) - 1 - idx) * 0.06)
        alpha = max(0.35, alpha)

        stamp = packet.timestamp.split("T")[-1]
        line = (
            f"[{packet.sequence_id:03d}] {packet.source_identity:<15.15s} "
            f"{packet.command:<13.13s} {packet.response_ms:>5.1f}ms"
        )
        ax.text(
            0.03,
            y,
            line,
            color=row_color,
            alpha=alpha,
            fontsize=8.6,
            family="monospace",
            ha="left",
            va="top",
        )
        ax.text(
            0.96,
            y,
            stamp,
            color="#9bb0bb",
            alpha=alpha,
            fontsize=7.3,
            family="monospace",
            ha="right",
            va="top",
        )
        y -= step

    ax.text(0.03, 0.06, "SCROLL: ACTIVE", color="#8cab9f", fontsize=8, family="monospace")
    ax.text(0.68, 0.06, "LINK: STABLE", color=SAFE, fontsize=8, family="monospace")


def create_dashboard(result: QShieldRunResult, output_path: str = "qshield_dashboard.png") -> None:
    """Generate and save a command-center style QSHIELD dashboard image."""
    _theme_setup()

    packets: List[Packet] = result.packets
    alerts: List[Alert] = result.alerts

    fig = plt.figure(figsize=(18, 10), dpi=120)
    fig.patch.set_facecolor(BG)
    _draw_header(fig)
    gs = GridSpec(12, 12, figure=fig, hspace=0.9, wspace=0.6)

    # Left main panel: signal timeline with attack markers.
    ax_signal = fig.add_subplot(gs[1:6, 0:8])
    x = list(range(1, len(packets) + 1))
    signal_values = [packet.signal_dbm for packet in packets]
    ax_signal.plot(x, signal_values, color=SAFE, linewidth=2.1, label="Secure Signal")

    alerts_by_seq = Counter(alert.sequence_id for alert in alerts)
    attack_x = sorted(alerts_by_seq.keys())
    attack_y = [signal_values[i - 1] for i in attack_x]
    if attack_x:
        ax_signal.scatter(
            attack_x,
            attack_y,
            color=ATTACK,
            marker="X",
            s=120,
            label="Hostile Event",
            zorder=3,
        )

    _style_panel(ax_signal, "SIGNAL INTEGRITY TIMELINE")
    ax_signal.set_xlabel("Packet Index")
    ax_signal.set_ylabel("Signal Strength (dBm)")
    ax_signal.set_ylim(min(signal_values) - 2, max(signal_values) + 2)
    ax_signal.legend(loc="upper right")

    # Center-bottom panel: timing bars for key exchange windows.
    ax_timing = fig.add_subplot(gs[6:10, 0:4])
    key_packets = [packet for packet in packets if packet.is_key_exchange_moment]
    if key_packets:
        kx = [packet.sequence_id for packet in key_packets]
        ky = [packet.response_ms for packet in key_packets]
        colors = [ATTACK if packet.packet_kind == "timing_probe" else WARN for packet in key_packets]
        ax_timing.bar(kx, ky, color=colors, alpha=0.9)
    _style_panel(ax_timing, "TIMING SIDE-CHANNEL WINDOW")
    ax_timing.set_xlabel("Sequence ID")
    ax_timing.set_ylabel("Response Time (ms)")

    # Center-bottom panel: speedometer threat gauge.
    ax_gauge = fig.add_subplot(gs[6:10, 4:8])
    threat_score = min(100.0, result.detection_rate * 0.8 + len(alerts) * 1.5)
    _draw_threat_gauge(ax_gauge, threat_score)
    ax_gauge.set_title("THREAT POSTURE", fontsize=11, color="#dff5ea", loc="left", pad=6)

    # Bottom-left panel: attack-type breakdown.
    ax_breakdown = fig.add_subplot(gs[10:12, 0:5])
    breakdown = result.breakdown
    labels = ["replay", "spoof", "timing_side_channel"]
    values = [breakdown.get(label, 0) for label in labels]
    bar_colors = [WARN, ATTACK, "#ff6655"]
    ax_breakdown.bar(labels, values, color=bar_colors, alpha=0.9)
    _style_panel(ax_breakdown, "ATTACK BREAKDOWN")
    ax_breakdown.set_ylabel("Alert Count")

    # Bottom-middle panel: stats summary.
    ax_stats = fig.add_subplot(gs[10:12, 5:8])
    ax_stats.axis("off")
    ax_stats.set_facecolor("#101024")
    system_status = "HARDENED" if result.detection_rate >= 8.0 else "MONITORING"
    stats_lines = [
        "QSHIELD RUNTIME STATUS",
        f"Packets analyzed : {result.total_packets}",
        f"Attacks blocked  : {result.blocked_packets}",
        f"Detection rate   : {result.detection_rate:.2f}%",
        f"Total alerts     : {len(alerts)}",
        f"System status    : {system_status}",
    ]
    ax_stats.text(
        0.02,
        0.95,
        "\n".join(stats_lines),
        ha="left",
        va="top",
        fontsize=10.5,
        family="monospace",
        color="#d7e6dd",
        bbox=dict(facecolor="#0f1426", edgecolor="#2a3a4a", boxstyle="round,pad=0.6"),
    )

    # Right column: live packet feed spanning most of the screen height.
    ax_feed = fig.add_subplot(gs[1:12, 8:12])
    _draw_live_packet_feed(ax_feed, packets, alerts)

    # Subtle global radar guide lines over the dashboard background.
    for y in [0.19, 0.41, 0.63, 0.85]:
        fig.lines.append(plt.Line2D([0.01, 0.99], [y, y], transform=fig.transFigure, color=GRID, alpha=0.12, linewidth=0.8))

    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
