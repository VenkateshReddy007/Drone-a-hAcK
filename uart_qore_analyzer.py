"""QSHIELD UART intelligence analyzer.

Processes encrypted UART CSV captures exported one byte per row.
No decryption is attempted. The tool analyzes only observable metadata:
packet timing, byte frequency, packet boundaries, repeated ciphertext,
and session structure.

Expected CSV fields:
name, type, start_time, duration, data

Usage:
    py -3 uart_qore_analyzer.py --input capture.csv --output-dir out
"""

from __future__ import annotations

import argparse
import math
import re
from itertools import combinations
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BYTE_DURATION = 0.00008
PACKET_GAP_THRESHOLD = BYTE_DURATION * 10.0
CHUNK_SIZE = 10_000
MAX_EXACT_PAIR_ROWS = 10_000
MAX_NEAR_PAIR_ROWS = 5_000
MAX_NEAR_GROUP_SIZE = 400

OUTPUT_DASHBOARD = "uart_dashboard.png"
OUTPUT_PACKETS = "packets_detected.csv"
OUTPUT_TIMING = "timing_anomalies.csv"
OUTPUT_REPORT = "uart_report.txt"

DARK_BG = "#0a0a1a"
AXIS_BG = "#101322"
GRID = "#2a3558"
TEXT = "#e8eef7"
ALERT = "#ff4d4d"
WARNING = "#ff9f1a"
SAFE = "#00d084"
CYAN = "#00d4ff"
HEARTBEAT = "#28d17c"
COMMAND = "#ff8c42"
TELEMETRY = "#4dd0ff"

EXPECTED_COLUMNS = ["name", "type", "start_time", "duration", "data"]
ALIASES = {
    "name": ["name", "frame_name", "packet_name", "label"],
    "type": ["type", "kind", "category", "packet_type"],
    "start_time": ["start_time", "starttime", "time", "timestamp", "ts", "capture_time"],
    "duration": ["duration", "duration_s", "byte_duration", "len_s", "interval"],
    "data": ["data", "payload", "byte", "hex", "value", "ciphertext", "raw", "bytes"],
}
HEX_RE = re.compile(r"^0x([0-9a-fA-F]{1,2})$")


def _normalize_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _detect_column_map(columns: Iterable[object]) -> dict[str, str]:
    normalized = {_normalize_name(column): str(column) for column in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            key = _normalize_name(alias)
            if key in normalized:
                mapping[canonical] = normalized[key]
                break
    return mapping


def _has_expected_fields(columns: Iterable[object]) -> bool:
    mapping = _detect_column_map(columns)
    return "start_time" in mapping and "data" in mapping


def _parse_hex_byte(value: object) -> int:
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return 0
    match = HEX_RE.match(text)
    if match:
        return int(match.group(1), 16)
    text = text.replace("0x", "").replace("0X", "").strip()
    if not text:
        return 0
    return int(text, 16) & 0xFF


def _bytes_entropy(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    counts = np.bincount(values.astype(np.uint8), minlength=256).astype(float)
    probs = counts[counts > 0] / counts.sum()
    return float(-(probs * np.log2(probs)).sum())


def _infer_packet_type(length: int) -> str:
    if 1 <= length <= 10:
        return "heartbeat"
    if 11 <= length <= 50:
        return "command"
    return "telemetry"


def _packet_color(length: int) -> str:
    packet_type = _infer_packet_type(length)
    if packet_type == "heartbeat":
        return HEARTBEAT
    if packet_type == "command":
        return COMMAND
    return TELEMETRY


def _read_csv_in_chunks(input_path: Path) -> pd.DataFrame:
    sample = pd.read_csv(input_path, nrows=5)
    header_mode = 0 if _has_expected_fields(sample.columns) else None
    if header_mode is None:
        print("[1/10] Header not trusted. Falling back to canonical UART column order.", flush=True)

    reader = pd.read_csv(input_path, chunksize=CHUNK_SIZE, header=header_mode, dtype=str)
    chunks: list[pd.DataFrame] = []

    for chunk_index, chunk in enumerate(reader, start=1):
        if header_mode is None:
            if chunk.shape[1] < 5:
                raise ValueError("Headerless UART CSV must contain at least five columns.")
            chunk = chunk.iloc[:, :5].copy()
            chunk.columns = EXPECTED_COLUMNS
        else:
            column_map = _detect_column_map(chunk.columns)
            missing = [key for key in ("start_time", "duration", "data") if key not in column_map]
            if missing:
                raise ValueError("Could not detect required columns: " + ", ".join(missing))
            rename_map = {column_map[key]: key for key in column_map}
            chunk = chunk.rename(columns=rename_map)
            for required in EXPECTED_COLUMNS:
                if required not in chunk.columns:
                    if required == "name":
                        chunk[required] = "Encrypted Data"
                    elif required == "type":
                        chunk[required] = "data"
                    else:
                        raise ValueError(f"Missing required column after rename: {required}")
            chunk = chunk[EXPECTED_COLUMNS].copy()

        chunk["start_time"] = pd.to_numeric(chunk["start_time"], errors="coerce")
        chunk["duration"] = pd.to_numeric(chunk["duration"], errors="coerce")
        chunk["data"] = chunk["data"].astype(str).str.strip()
        chunks.append(chunk)
        print(f"  loaded chunk {chunk_index} ({len(chunk):,} rows)", flush=True)

    if not chunks:
        raise ValueError("No rows were loaded from the UART CSV.")

    df = pd.concat(chunks, ignore_index=True)
    df = df.dropna(subset=["start_time", "duration", "data"])
    df = df.sort_values("start_time", kind="mergesort").reset_index(drop=True)
    return df


def _build_packet_table(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    byte_values = df["data"].map(_parse_hex_byte).to_numpy(dtype=np.uint8, copy=False)
    start_times = df["start_time"].to_numpy(dtype=float, copy=False)
    durations = df["duration"].to_numpy(dtype=float, copy=False)

    inter_byte_gaps = np.empty_like(start_times)
    inter_byte_gaps[0] = 0.0
    if len(start_times) > 1:
        inter_byte_gaps[1:] = np.diff(start_times)

    packet_breaks = inter_byte_gaps > PACKET_GAP_THRESHOLD
    packet_ids = np.cumsum(packet_breaks.astype(np.int32))

    working = pd.DataFrame(
        {
            "row_id": np.arange(len(df), dtype=np.int32),
            "start_time": start_times,
            "duration": durations,
            "byte_value": byte_values,
            "inter_byte_gap": inter_byte_gaps,
            "packet_id": packet_ids,
        }
    )

    grouped = working.groupby("packet_id", sort=True)
    packets = grouped.agg(
        start_time=("start_time", "min"),
        last_byte_time=("start_time", "max"),
        length_bytes=("byte_value", "size"),
        packet_entropy=("byte_value", lambda s: _bytes_entropy(s.to_numpy(dtype=np.uint8, copy=False))),
    ).reset_index()
    packets["end_time"] = packets["last_byte_time"] + BYTE_DURATION
    packets.drop(columns=["last_byte_time"], inplace=True)

    gap_before = np.zeros(len(packets), dtype=float)
    if len(packets) > 1:
        gap_before[1:] = np.maximum(
            0.0,
            packets["start_time"].to_numpy(dtype=float)[1:] - packets["end_time"].to_numpy(dtype=float)[:-1],
        )
    packets["gap_before"] = gap_before
    packets["packet_hex"] = grouped["byte_value"].apply(
        lambda s: bytes(s.to_numpy(dtype=np.uint8, copy=False)).hex().upper()
    ).reset_index(drop=True)
    packets["duration_s"] = packets["end_time"] - packets["start_time"]
    packets["inferred_type"] = packets["length_bytes"].map(_infer_packet_type)
    packets["session_break"] = packets["gap_before"] > max(0.002, PACKET_GAP_THRESHOLD * 5.0)
    packets["session_id"] = packets["session_break"].cumsum().astype(int)

    return working, packets


def _compute_byte_stats(byte_values: np.ndarray) -> tuple[np.ndarray, float]:
    counts = np.bincount(byte_values.astype(np.uint8), minlength=256)
    entropy = _bytes_entropy(byte_values.astype(np.uint8, copy=False))
    return counts, entropy


def _identify_timing_anomalies(packets: pd.DataFrame) -> tuple[pd.DataFrame, float, float, float, float]:
    gaps = packets["gap_before"].to_numpy(dtype=float, copy=False)
    normal_gaps = gaps[gaps > 0]
    if normal_gaps.size == 0:
        normal_gaps = np.array([BYTE_DURATION], dtype=float)

    average_gap = float(normal_gaps.mean())
    median_gap = float(np.median(normal_gaps))
    key_exchange_threshold = max(5.0 * average_gap, 5.0 * median_gap, PACKET_GAP_THRESHOLD)
    replay_fast_threshold = max(0.5 * average_gap, BYTE_DURATION * 2.0)

    events: list[dict[str, object]] = []

    key_mask = packets["gap_before"] > key_exchange_threshold
    for row in packets.loc[key_mask, ["packet_id", "session_id", "start_time", "gap_before", "length_bytes"]].itertuples(index=False):
        events.append(
            {
                "event_type": "KEY_EXCHANGE_CANDIDATE",
                "packet_id": int(row.packet_id),
                "session_id": int(row.session_id),
                "start_time": float(row.start_time),
                "gap_before": float(row.gap_before),
                "threshold": float(key_exchange_threshold),
                "length_bytes": int(row.length_bytes),
                "reason": "gap exceeded 5x normal packet gap",
            }
        )

    fast_gap = (packets["gap_before"] > 0) & (packets["gap_before"] < replay_fast_threshold)
    groups = fast_gap.ne(fast_gap.shift(fill_value=False)).cumsum()
    burst_sizes = fast_gap.groupby(groups).transform("sum")
    replay_mask = fast_gap & (burst_sizes >= 3)
    for row in packets.loc[replay_mask, ["packet_id", "session_id", "start_time", "gap_before", "length_bytes"]].itertuples(index=False):
        events.append(
            {
                "event_type": "BURST_REPLAY_CANDIDATE",
                "packet_id": int(row.packet_id),
                "session_id": int(row.session_id),
                "start_time": float(row.start_time),
                "gap_before": float(row.gap_before),
                "threshold": float(replay_fast_threshold),
                "length_bytes": int(row.length_bytes),
                "reason": "burst of packets faster than normal",
            }
        )

    anomalies = pd.DataFrame.from_records(events)
    if anomalies.empty:
        anomalies = pd.DataFrame(
            columns=["event_type", "packet_id", "session_id", "start_time", "gap_before", "threshold", "length_bytes", "reason"]
        )
    return anomalies, average_gap, median_gap, key_exchange_threshold, replay_fast_threshold


def _packet_distribution(packets: pd.DataFrame) -> dict[str, float]:
    labels = packets["length_bytes"].map(_infer_packet_type)
    percentages = labels.value_counts(normalize=True).reindex(["heartbeat", "command", "telemetry"], fill_value=0.0) * 100.0
    return percentages.to_dict()


def _detect_repeated_packets(packets: pd.DataFrame, fast_mode: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    exact_rows: list[dict[str, object]] = []
    near_rows: list[dict[str, object]] = []
    exact_row_count = 0
    near_row_count = 0

    for packet_hex, group in packets.groupby("packet_hex", sort=False):
        if len(group) < 2 or not packet_hex:
            continue
        ids = group["packet_id"].to_numpy(dtype=int)
        # Keep representative exact matches without materializing all O(n^2) pairs.
        sample_size = 10 if fast_mode else 24
        sample_ids = ids[: min(len(ids), sample_size)]
        for left, right in combinations(sample_ids, 2):
            exact_rows.append(
                {
                    "packet_a": int(left),
                    "packet_b": int(right),
                    "packet_id_a": int(left),
                    "packet_id_b": int(right),
                    "distance": 0,
                    "packet_hex": packet_hex,
                }
            )
            exact_row_count += 1
            if exact_row_count >= MAX_EXACT_PAIR_ROWS:
                break
        if exact_row_count >= MAX_EXACT_PAIR_ROWS:
            break

    if fast_mode:
        exact_matches = pd.DataFrame.from_records(exact_rows)
        near_matches = pd.DataFrame(columns=["packet_a", "packet_b", "packet_id_a", "packet_id_b", "distance", "packet_hex"])
        if exact_matches.empty:
            exact_matches = pd.DataFrame(columns=["packet_a", "packet_b", "packet_id_a", "packet_id_b", "distance", "packet_hex"])
        return exact_matches, near_matches

    for length, group in packets.groupby("length_bytes", sort=False):
        if near_row_count >= MAX_NEAR_PAIR_ROWS:
            break
        if len(group) < 2 or length < 2:
            continue
        if len(group) > MAX_NEAR_GROUP_SIZE:
            group = group.sort_values("packet_id").head(MAX_NEAR_GROUP_SIZE)

        payloads = [bytes.fromhex(value) for value in group["packet_hex"].tolist()]
        packet_ids = group["packet_id"].to_numpy(dtype=int)
        buckets: dict[tuple[bytes, bytes, int], list[int]] = {}

        for index, payload in enumerate(payloads):
            prefix = payload[:2]
            suffix = payload[-2:] if len(payload) >= 2 else payload
            checksum = sum(payload) % 256
            key = (prefix, suffix, checksum)
            buckets.setdefault(key, []).append(index)

        for indices in buckets.values():
            if near_row_count >= MAX_NEAR_PAIR_ROWS:
                break
            if len(indices) < 2:
                continue
            for i, j in combinations(indices, 2):
                left = np.frombuffer(payloads[i], dtype=np.uint8)
                right = np.frombuffer(payloads[j], dtype=np.uint8)
                if left.shape != right.shape:
                    continue
                distance = int(np.count_nonzero(left != right))
                if 1 <= distance <= 2:
                    near_rows.append(
                        {
                            "packet_a": int(packet_ids[i]),
                            "packet_b": int(packet_ids[j]),
                            "packet_id_a": int(packet_ids[i]),
                            "packet_id_b": int(packet_ids[j]),
                            "distance": distance,
                            "packet_hex": f"{group.iloc[i]['packet_hex']}|{group.iloc[j]['packet_hex']}",
                        }
                    )
                    near_row_count += 1
                    if near_row_count >= MAX_NEAR_PAIR_ROWS:
                        break
            if near_row_count >= MAX_NEAR_PAIR_ROWS:
                break

    exact_matches = pd.DataFrame.from_records(exact_rows)
    near_matches = pd.DataFrame.from_records(near_rows)
    if exact_matches.empty:
        exact_matches = pd.DataFrame(columns=["packet_a", "packet_b", "packet_id_a", "packet_id_b", "distance", "packet_hex"])
    if near_matches.empty:
        near_matches = pd.DataFrame(columns=["packet_a", "packet_b", "packet_id_a", "packet_id_b", "distance", "packet_hex"])
    return exact_matches, near_matches


def _reconstruct_sessions(packets: pd.DataFrame, average_gap: float, key_exchange_threshold: float) -> tuple[pd.DataFrame, str, float]:
    session_gap_threshold = max(key_exchange_threshold * 2.0, average_gap * 10.0, 0.002)
    session_breaks = packets["gap_before"] > session_gap_threshold
    session_id = session_breaks.cumsum().astype(int)
    sessions = packets.copy()
    sessions["session_id"] = session_id

    summary = sessions.groupby("session_id").agg(
        session_start_time=("start_time", "min"),
        session_end_time=("end_time", "max"),
        packet_count=("packet_id", "count"),
        avg_packet_length=("length_bytes", "mean"),
    ).reset_index()
    summary["session_duration_s"] = summary["session_end_time"] - summary["session_start_time"]

    ascii_timeline = " ".join(
        f"[{int(row.packet_id)}:{int(row.length_bytes)}B]{'|' if row.gap_before > session_gap_threshold else '-'}"
        for row in sessions.head(24).itertuples(index=False)
    )
    rtt_candidates = sessions.loc[sessions["gap_before"] <= average_gap * 1.5, "gap_before"].to_numpy(dtype=float)
    rtt_estimate = float(rtt_candidates.mean()) if rtt_candidates.size else float(average_gap)
    return summary, ascii_timeline, rtt_estimate


def _score_attack_vectors(
    packets: pd.DataFrame,
    anomalies: pd.DataFrame,
    exact_matches: pd.DataFrame,
    near_matches: pd.DataFrame,
    entropy: float,
    key_exchange_threshold: float,
    average_gap: float,
) -> list[dict[str, object]]:
    session_count = max(1, int(packets["session_id"].nunique()))
    distinct_lengths = max(1, packets["length_bytes"].nunique())
    type_spread = packets["inferred_type"].nunique()
    key_gap_ratio = float((packets["gap_before"] > key_exchange_threshold).mean())
    entropy_score = min(10.0, entropy / 0.8)

    vectors = [
        (
            "Replay Attack",
            min(10.0, 2.0 + len(exact_matches) / 50.0 + len(near_matches) / 100.0),
            "Packet boundaries are clear enough to replay if repeated ciphertext exists." if len(exact_matches) > 0 else "Packet boundaries are visible, but exact repeats were not dominant.",
        ),
        (
            "Timing Side Channel",
            min(10.0, 2.0 + key_gap_ratio * 12.0 + (2.0 if len(anomalies) > 0 else 0.0)),
            "Long gaps and burst edges expose timing behavior." if key_gap_ratio > 0 else "Timing regularity is limited, reducing side-channel confidence.",
        ),
        (
            "Traffic Analysis",
            min(10.0, 3.0 + (2.5 if distinct_lengths >= 3 else 0.0) + (2.0 if type_spread >= 2 else 0.0)),
            "Packet-size classes separate heartbeat, command, and telemetry behavior." if distinct_lengths >= 3 else "Packet lengths are less differentiated than expected.",
        ),
        (
            "Session Hijacking",
            min(10.0, 2.0 + 10.0 / session_count + (2.0 if len(anomalies) > 0 else 0.0)),
            "Session boundaries are identifiable from timing gaps." if session_count > 1 else "Session boundaries are weakly defined, reducing injection leverage.",
        ),
        (
            "Nonce Reuse",
            10.0 if len(exact_matches) > 0 else min(5.0, len(near_matches) / 20.0),
            "Repeated ciphertext blocks were found, consistent with nonce reuse." if len(exact_matches) > 0 else "No exact repeated ciphertext blocks were confirmed.",
        ),
        (
            "Key Exchange Exposure",
            min(10.0, 2.0 + key_gap_ratio * 15.0 + (1.0 if entropy_score < 7.5 else 0.0)),
            "Periodic pauses likely expose key-rotation moments." if key_gap_ratio > 0.05 else "Key exchange moments are not strongly exposed by timing.",
        ),
    ]

    return [{"vector": name, "score": round(score, 1), "justification": justification} for name, score, justification in vectors]


def _build_dashboard(
    working: pd.DataFrame,
    packets: pd.DataFrame,
    counts: np.ndarray,
    timing_anomalies: pd.DataFrame,
    scores: list[dict[str, object]],
    key_exchange_threshold: float,
    replay_fast_threshold: float,
    output_path: Path,
) -> None:
    if len(working) > 60_000:
        working = working.iloc[:: max(1, len(working) // 60_000)].copy()
    if len(packets) > 15_000:
        packets = packets.iloc[:: max(1, len(packets) // 15_000)].copy()

    plt.style.use("dark_background")
    fig, axes = plt.subplots(3, 2, figsize=(24, 16), dpi=150)
    fig.patch.set_facecolor(DARK_BG)
    axes = axes.flatten()

    for ax in axes:
        if ax.name != "polar":
            ax.set_facecolor(AXIS_BG)
            ax.grid(True, color=GRID, linestyle="--", alpha=0.35, linewidth=0.6)
            for spine in ax.spines.values():
                spine.set_color(GRID)
            ax.tick_params(colors=TEXT, labelsize=9)

    gap_colors = np.where(
        working["inter_byte_gap"].to_numpy(dtype=float) > key_exchange_threshold,
        ALERT,
        np.where(working["inter_byte_gap"].to_numpy(dtype=float) > PACKET_GAP_THRESHOLD, WARNING, CYAN),
    )
    axes[0].scatter(working["start_time"], working["byte_value"], c=gap_colors, s=4, alpha=0.8)
    anomaly_ids = set(timing_anomalies["packet_id"].tolist()) if not timing_anomalies.empty else set()
    anomaly_mask = working["packet_id"].isin(anomaly_ids)
    axes[0].scatter(working.loc[anomaly_mask, "start_time"], working.loc[anomaly_mask, "byte_value"], c=ALERT, s=12, alpha=0.95)
    axes[0].set_title("Byte Arrival Timeline", color=TEXT, fontweight="bold")
    axes[0].set_xlabel("Start Time (s)", color=TEXT)
    axes[0].set_ylabel("Byte Value", color=TEXT)

    expected = len(working) / 256.0
    axes[1].bar(np.arange(256), counts, color=CYAN, edgecolor="#000000", linewidth=0.2)
    axes[1].axhline(expected, color=ALERT, linestyle="--", linewidth=1.4)
    axes[1].set_title("CIPHERTEXT ENTROPY ANALYSIS", color=TEXT, fontweight="bold")
    axes[1].set_xlabel("Byte Value (0x00-0xFF)", color=TEXT)
    axes[1].set_ylabel("Frequency", color=TEXT)

    packet_colors = packets["length_bytes"].map(_packet_color)
    axes[2].bar(packets["length_bytes"], np.ones(len(packets)), color=packet_colors, width=0.85)
    axes[2].set_title("Packet Size Histogram", color=TEXT, fontweight="bold")
    axes[2].set_xlabel("Packet Length (bytes)", color=TEXT)
    axes[2].set_ylabel("Count", color=TEXT)

    axes[3].plot(packets["packet_id"], packets["gap_before"], color=CYAN, linewidth=1.5)
    axes[3].axhline(key_exchange_threshold, color=ALERT, linestyle="--", linewidth=1.4)
    key_mask = packets["gap_before"] > key_exchange_threshold
    replay_mask = (packets["gap_before"] > 0) & (packets["gap_before"] < replay_fast_threshold)
    axes[3].scatter(packets.loc[key_mask, "packet_id"], packets.loc[key_mask, "gap_before"], marker="^", color=ALERT, s=50, label="Key Exchange Candidate")
    axes[3].scatter(packets.loc[replay_mask, "packet_id"], packets.loc[replay_mask, "gap_before"], marker="v", color=WARNING, s=40, label="Replay Candidate")
    axes[3].set_title("Inter-packet Gap Timeline", color=TEXT, fontweight="bold")
    axes[3].set_xlabel("Packet ID", color=TEXT)
    axes[3].set_ylabel("Gap Before Packet (s)", color=TEXT)
    axes[3].legend(facecolor=AXIS_BG, edgecolor=GRID, fontsize=8)

    axes[4].plot(working["start_time"], np.arange(1, len(working) + 1), color=SAFE, linewidth=1.4)
    axes[4].set_title("Cumulative Byte Count Over Time", color=TEXT, fontweight="bold")
    axes[4].set_xlabel("Start Time (s)", color=TEXT)
    axes[4].set_ylabel("Cumulative Bytes", color=TEXT)

    axes[5].remove()
    radar = fig.add_subplot(3, 2, 6, polar=True)
    radar.set_facecolor(AXIS_BG)
    radar_labels = [item["vector"] for item in scores]
    radar_values = [float(item["score"]) for item in scores]
    radar_values += radar_values[:1]
    angles = np.linspace(0, 2 * np.pi, len(radar_labels), endpoint=False).tolist()
    angles += angles[:1]
    radar.plot(angles, radar_values, color=CYAN, linewidth=2.0)
    radar.fill(angles, radar_values, color=ALERT, alpha=0.20)
    radar.set_xticks(angles[:-1])
    radar.set_xticklabels(radar_labels, color=TEXT, fontsize=8)
    radar.set_yticklabels([])
    radar.set_title("Attack Vector Radar", color=TEXT, fontweight="bold")
    radar.grid(color=GRID, alpha=0.5)

    fig.suptitle(
        "QSHIELD UART INTELLIGENCE REPORT | QNu Qore Drone Comms | 515 Army Base Workshop 2026",
        color=TEXT,
        fontsize=16,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def _format_report(
    total_bytes: int,
    total_duration: float,
    estimated_baud: float,
    bytes_per_second: float,
    packets: pd.DataFrame,
    entropy: float,
    top_common: list[tuple[int, int]],
    top_rare: list[tuple[int, int]],
    packet_dist: dict[str, float],
    timing_anomalies: pd.DataFrame,
    ascii_timeline: str,
    rtt_estimate: float,
    scores: list[dict[str, object]],
    exact_matches: pd.DataFrame,
    near_matches: pd.DataFrame,
    average_gap: float,
    median_gap: float,
    key_exchange_threshold: float,
) -> str:
    lines: list[str] = []
    lines += ["EXECUTIVE SUMMARY", "-" * 18]
    lines.append(
        f"We analyzed {total_bytes:,} encrypted UART bytes across {len(packets):,} detected packets and found observable timing structure without decrypting payloads."
    )
    lines.append(
        "The capture contains packet-boundary gaps, burst clusters, and repeated-ciphertext tests that may indicate replay, session pauses, or key exchange moments."
    )
    lines.append(
        f"Entropy is {entropy:.3f} bits per byte, which is close to ideal ciphertext behavior, but timing and packet-size metadata still expose operational patterns."
    )
    lines.append("")

    lines += ["COMMUNICATION PROFILE", "-" * 22]
    lines.append(f"Baud rate: {estimated_baud:,.0f} baud")
    lines.append(f"Total bytes: {total_bytes:,}")
    lines.append(f"Total duration: {total_duration:.6f} s")
    lines.append(f"Packet count: {len(packets):,}")
    lines.append(f"Bytes per second: {bytes_per_second:,.2f}")
    lines.append("")

    lines += ["CRYPTOGRAPHIC ASSESSMENT", "-" * 25]
    lines.append(f"Shannon entropy: {entropy:.3f} bits/byte")
    lines.append(f"Average normal packet gap: {average_gap:.6f} s")
    lines.append(f"Median normal packet gap: {median_gap:.6f} s")
    lines.append(f"Key exchange candidate threshold: {key_exchange_threshold:.6f} s")
    lines.append(f"Top 5 most common bytes: {', '.join([f'0x{value:02X} ({count})' for value, count in top_common])}")
    lines.append(f"Top 5 least common bytes: {', '.join([f'0x{value:02X} ({count})' for value, count in top_rare])}")
    lines.append("")

    lines += ["INFERRED OPERATIONAL INTELLIGENCE", "-" * 34]
    lines.append(f"Heartbeat/ACK traffic: {packet_dist.get('heartbeat', 0.0):.1f}%")
    lines.append(f"Command traffic: {packet_dist.get('command', 0.0):.1f}%")
    lines.append(f"Telemetry traffic: {packet_dist.get('telemetry', 0.0):.1f}%")
    lines.append("The packet-size mix suggests a control channel carrying short acknowledgments, medium command frames, and longer telemetry bursts.")
    lines.append(f"ASCII flow sketch: {ascii_timeline}")
    lines.append(f"Estimated request-response RTT: {rtt_estimate:.6f} s")
    lines.append("")

    lines += ["TIMING SIDE-CHANNEL FINDINGS", "-" * 29]
    if timing_anomalies.empty:
        lines.append("No strong timing anomalies were detected.")
    else:
        for row in timing_anomalies.head(12).itertuples(index=False):
            lines.append(f"- {row.event_type} at {row.start_time:.6f}s | gap {row.gap_before:.6f}s | packet {row.packet_id}")
    lines.append("")

    lines += ["IDENTIFIED VULNERABILITIES", "-" * 26]
    for item in sorted(scores, key=lambda entry: entry["score"], reverse=True):
        severity = "HIGH" if item["score"] >= 7.0 else "MEDIUM" if item["score"] >= 4.0 else "LOW"
        lines.append(f"- {item['vector']} [{severity}] - {item['justification']}")
    if len(exact_matches) > 0:
        lines.append(f"- Nonce reuse / exact ciphertext repetition [HIGH] - {len(exact_matches):,} exact repeated packet matches were detected.")
    else:
        lines.append("- Nonce reuse / exact ciphertext repetition [LOW] - no exact repeated packet matches were confirmed.")
    if len(near_matches) > 0:
        lines.append(f"- Near-identical ciphertext blocks [MEDIUM] - {len(near_matches):,} pairs differ by only 1-2 bytes.")
    lines.append("")

    lines += ["DEFENCE RECOMMENDATIONS", "-" * 23]
    recommendations = [
        "Use unique nonces and reject any repeated ciphertext fingerprint immediately.",
        "Introduce authenticated session counters so replayed packets can be rejected at the UART gateway.",
        "Add randomized timing padding around key exchange operations to reduce side-channel visibility.",
        "Enforce command sequence validation and packet-length sanity checks before processing control frames.",
        "Continuously monitor entropy, duplicate ciphertext, and burst timing to detect protocol misuse early.",
    ]
    for recommendation in recommendations:
        lines.append(f"- {recommendation}")

    return "\n".join(lines)


def analyze_uart_capture(input_path: Path, output_dir: Path, fast_mode: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/10] Loading UART CSV in 10,000-row chunks...", flush=True)
    raw = _read_csv_in_chunks(input_path)

    print("[2/10] Parsing one-byte hex values and deriving packet boundaries...", flush=True)
    working, packets = _build_packet_table(raw)
    total_bytes = len(working)
    total_duration = float(working["start_time"].iloc[-1] - working["start_time"].iloc[0] + BYTE_DURATION) if len(working) > 1 else BYTE_DURATION
    estimated_baud = 10.0 / BYTE_DURATION
    bytes_per_second = float(total_bytes / total_duration) if total_duration > 0 else 0.0
    print(f"  total bytes: {total_bytes:,}", flush=True)
    print(f"  total duration: {total_duration:.6f} s", flush=True)
    print(f"  estimated baud rate: {estimated_baud:,.0f}", flush=True)
    print(f"  bytes per second: {bytes_per_second:,.2f}", flush=True)

    print("[3/10] Building detected packet table...", flush=True)
    packets_path = output_dir / OUTPUT_PACKETS
    packets.to_csv(packets_path, index=False)
    print("  first 20 detected packets:", flush=True)
    print(packets[["packet_id", "start_time", "end_time", "gap_before", "length_bytes", "session_id", "inferred_type"]].head(20).to_string(index=False), flush=True)

    print("[4/10] Running byte frequency and entropy analysis...", flush=True)
    byte_values = working["byte_value"].to_numpy(dtype=np.uint8, copy=False)
    counts, entropy = _compute_byte_stats(byte_values)
    top_common = [(int(index), int(value)) for index, value in sorted(enumerate(counts), key=lambda item: item[1], reverse=True)[:5]]
    top_rare = [(int(index), int(value)) for index, value in sorted(enumerate(counts), key=lambda item: item[1])[:5]]
    print(f"  Shannon entropy: {entropy:.4f} bits/byte", flush=True)
    print("  Top 5 common bytes:", ", ".join([f"0x{value:02X}={count}" for value, count in top_common]), flush=True)
    print("  Top 5 least common bytes:", ", ".join([f"0x{value:02X}={count}" for value, count in top_rare]), flush=True)

    print("[5/10] Identifying timing side-channel candidates...", flush=True)
    timing_anomalies, average_gap, median_gap, key_exchange_threshold, replay_fast_threshold = _identify_timing_anomalies(packets)
    timing_path = output_dir / OUTPUT_TIMING
    timing_anomalies.to_csv(timing_path, index=False)
    print(f"  average normal gap: {average_gap:.6f} s", flush=True)
    print(f"  median normal gap: {median_gap:.6f} s", flush=True)
    print(f"  key exchange threshold: {key_exchange_threshold:.6f} s", flush=True)
    print(f"  timing anomaly rows: {len(timing_anomalies):,}", flush=True)

    print("[6/10] Analyzing packet-size patterns...", flush=True)
    packet_dist = _packet_distribution(packets)
    for label in ("heartbeat", "command", "telemetry"):
        print(f"  {label}: {packet_dist.get(label, 0.0):.1f}%", flush=True)

    print("[7/10] Detecting repeated and near-identical ciphertext packets...", flush=True)
    exact_matches, near_matches = _detect_repeated_packets(packets, fast_mode=fast_mode)
    print(f"  exact match pairs: {len(exact_matches):,}", flush=True)
    print(f"  near-identical pairs: {len(near_matches):,}", flush=True)

    print("[8/10] Reconstructing sessions and flow timeline...", flush=True)
    session_summary, ascii_timeline, rtt_estimate = _reconstruct_sessions(packets, average_gap, key_exchange_threshold)
    session_start = float(packets["start_time"].min())
    session_end = float(packets["end_time"].max())
    session_duration = session_end - session_start
    print(f"  session start: {session_start:.6f} s", flush=True)
    print(f"  session end: {session_end:.6f} s", flush=True)
    print(f"  session duration: {session_duration:.6f} s", flush=True)
    print(f"  estimated RTT: {rtt_estimate:.6f} s", flush=True)
    print(f"  ASCII timeline: {ascii_timeline}", flush=True)

    print("[9/10] Scoring attack vectors and rendering dashboard...", flush=True)
    scores = _score_attack_vectors(
        packets=packets,
        anomalies=timing_anomalies,
        exact_matches=exact_matches,
        near_matches=near_matches,
        entropy=entropy,
        key_exchange_threshold=key_exchange_threshold,
        average_gap=average_gap,
    )
    dashboard_path = output_dir / OUTPUT_DASHBOARD
    _build_dashboard(working, packets, counts, timing_anomalies, scores, key_exchange_threshold, replay_fast_threshold, dashboard_path)
    print(f"  dashboard saved to: {dashboard_path}", flush=True)

    print("[10/10] Writing intelligence report...", flush=True)
    report_text = _format_report(
        total_bytes=total_bytes,
        total_duration=total_duration,
        estimated_baud=estimated_baud,
        bytes_per_second=bytes_per_second,
        packets=packets,
        entropy=entropy,
        top_common=top_common,
        top_rare=top_rare,
        packet_dist=packet_dist,
        timing_anomalies=timing_anomalies,
        ascii_timeline=ascii_timeline,
        rtt_estimate=rtt_estimate,
        scores=scores,
        exact_matches=exact_matches,
        near_matches=near_matches,
        average_gap=average_gap,
        median_gap=median_gap,
        key_exchange_threshold=key_exchange_threshold,
    )
    report_path = output_dir / OUTPUT_REPORT
    report_path.write_text(report_text, encoding="utf-8")

    print(f"  packets saved to: {packets_path}", flush=True)
    print(f"  timing anomalies saved to: {timing_path}", flush=True)
    print(f"  report saved to: {report_path}", flush=True)
    print("\n=== UART INTELLIGENCE REPORT PREVIEW ===", flush=True)
    for line in report_text.splitlines()[:60]:
        print(line, flush=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze encrypted UART CSV captures without decrypting payloads.")
    parser.add_argument("--input", required=True, help="Path to the UART CSV file")
    parser.add_argument("--output-dir", default=".", help="Directory for output artifacts")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    analyze_uart_capture(Path(args.input), Path(args.output_dir))


if __name__ == "__main__":
    main()
