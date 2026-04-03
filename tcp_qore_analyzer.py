"""TCP dataset analyzer for quantum-safe drone/GCS traffic.

Features:
- Loads CSV, PCAP, or PCAPNG input
- Infers packet structure columns from unknown CSV schemas
- Analyzes timing and structural patterns for anomalies
- Flags potential replay sequences
- Generates a dark themed matplotlib dashboard
- Writes a vulnerability report and CSV artifacts

Usage:
    py -3 tcp_qore_analyzer.py --input dataset.csv --output-dir out
    py -3 tcp_qore_analyzer.py --input capture.pcap --output-dir out
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


CANDIDATE_COLUMNS = {
    "timestamp": [
        "timestamp",
        "time",
        "ts",
        "epoch",
        "frame.time_epoch",
        "frame.time",
        "capture_time",
    ],
    "src_ip": ["src_ip", "source", "src", "ip.src", "source_ip", "ipv4_src", "ipv6_src"],
    "dst_ip": ["dst_ip", "destination", "dst", "ip.dst", "dest_ip", "ipv4_dst", "ipv6_dst"],
    "src_port": ["src_port", "sport", "tcp.srcport", "source_port", "srcport"],
    "dst_port": ["dst_port", "dport", "tcp.dstport", "destination_port", "dstport"],
    "seq": ["seq", "tcp.seq", "sequence", "sequence_number", "tcp_seq"],
    "ack": ["ack", "tcp.ack", "acknowledgment", "ack_number", "tcp_ack"],
    "flags": ["flags", "tcp.flags", "flag", "tcp_flag"],
    "packet_len": [
        "packet_len",
        "length",
        "len",
        "frame.len",
        "tcp.len",
        "payload_len",
        "size",
        "packet_size",
    ],
    "payload": ["payload", "data", "tcp.payload", "raw", "payload_hex", "hex_payload"],
    "protocol": ["protocol", "proto", "transport", "ip.proto", "layer4"],
}


REQUIRED_OUTPUT_FILES = {
    "dashboard": "tcp_dark_dashboard.png",
    "report": "vulnerability_report.txt",
    "anomalies": "statistical_anomalies.csv",
    "replays": "replay_candidates.csv",
    "summary": "analysis_summary.json",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]
    return df


def _find_column(df: pd.DataFrame, semantic_name: str) -> str | None:
    candidates = CANDIDATE_COLUMNS.get(semantic_name, [])
    lower_map = {col.lower(): col for col in df.columns}

    for candidate in candidates:
        if candidate in lower_map:
            return lower_map[candidate]

    for col in df.columns:
        cleaned = col.replace(" ", "").replace("_", "")
        for candidate in candidates:
            if candidate.replace("_", "").replace(".", "") in cleaned.replace(".", ""):
                return col

    return None


def _hash_payload(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text or text.lower() == "nan":
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _coerce_timestamp(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() >= 0.7:
        return numeric

    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return parsed.astype("int64") / 1e9


def _read_csv_dataset(path: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    raw = pd.read_csv(path, sep=None, engine="python", low_memory=False)
    raw = _normalize_columns(raw)

    mapped: dict[str, str] = {}
    for semantic in CANDIDATE_COLUMNS:
        found = _find_column(raw, semantic)
        if found:
            mapped[semantic] = found

    if "protocol" in mapped:
        protocol_series = raw[mapped["protocol"]].astype(str).str.lower()
        tcp_mask = protocol_series.str.contains("tcp") | protocol_series.str.contains("6")
        if tcp_mask.any():
            raw = raw.loc[tcp_mask].copy()

    out = pd.DataFrame(index=raw.index)

    if "timestamp" in mapped:
        out["timestamp"] = _coerce_timestamp(raw[mapped["timestamp"]])
    else:
        out["timestamp"] = np.arange(len(raw), dtype=float)

    out["src_ip"] = raw[mapped["src_ip"]].astype(str) if "src_ip" in mapped else "unknown_src"
    out["dst_ip"] = raw[mapped["dst_ip"]].astype(str) if "dst_ip" in mapped else "unknown_dst"

    out["src_port"] = pd.to_numeric(raw[mapped["src_port"]], errors="coerce").fillna(-1).astype(int) if "src_port" in mapped else -1
    out["dst_port"] = pd.to_numeric(raw[mapped["dst_port"]], errors="coerce").fillna(-1).astype(int) if "dst_port" in mapped else -1

    out["seq"] = pd.to_numeric(raw[mapped["seq"]], errors="coerce").fillna(-1).astype(np.int64) if "seq" in mapped else -1
    out["ack"] = pd.to_numeric(raw[mapped["ack"]], errors="coerce").fillna(-1).astype(np.int64) if "ack" in mapped else -1

    out["flags"] = raw[mapped["flags"]].astype(str) if "flags" in mapped else ""

    if "packet_len" in mapped:
        out["packet_len"] = pd.to_numeric(raw[mapped["packet_len"]], errors="coerce").fillna(0).astype(float)
    else:
        out["packet_len"] = 0.0

    if "payload" in mapped:
        out["payload_hash"] = raw[mapped["payload"]].map(_hash_payload)
    else:
        out["payload_hash"] = ""

    out["source_format"] = "csv"

    inferred = {semantic: mapped.get(semantic, "<not_found>") for semantic in CANDIDATE_COLUMNS}
    return out, inferred


def _read_pcap_dataset(path: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    try:
        from scapy.all import IP, IPv6, TCP, rdpcap  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "PCAP parsing requires scapy. Install with: pip install scapy"
        ) from exc

    packets = rdpcap(str(path))
    rows: list[dict[str, object]] = []

    for pkt in packets:
        if TCP not in pkt:
            continue

        if IP in pkt:
            src_ip = pkt[IP].src
            dst_ip = pkt[IP].dst
        elif IPv6 in pkt:
            src_ip = pkt[IPv6].src
            dst_ip = pkt[IPv6].dst
        else:
            src_ip = "unknown_src"
            dst_ip = "unknown_dst"

        tcp = pkt[TCP]
        payload_bytes = bytes(tcp.payload) if tcp.payload is not None else b""

        rows.append(
            {
                "timestamp": float(pkt.time),
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "src_port": int(getattr(tcp, "sport", -1)),
                "dst_port": int(getattr(tcp, "dport", -1)),
                "seq": int(getattr(tcp, "seq", -1)),
                "ack": int(getattr(tcp, "ack", -1)),
                "flags": str(getattr(tcp, "flags", "")),
                "packet_len": float(len(payload_bytes)),
                "payload_hash": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else "",
                "source_format": "pcap",
            }
        )

    if not rows:
        raise ValueError("No TCP packets found in PCAP/PCAPNG input.")

    inferred = {
        "timestamp": "pcap:packet.time",
        "src_ip": "pcap:ip.src",
        "dst_ip": "pcap:ip.dst",
        "src_port": "pcap:tcp.sport",
        "dst_port": "pcap:tcp.dport",
        "seq": "pcap:tcp.seq",
        "ack": "pcap:tcp.ack",
        "flags": "pcap:tcp.flags",
        "packet_len": "pcap:len(tcp.payload)",
        "payload": "pcap:sha256(tcp.payload)",
        "protocol": "pcap:TCP only",
    }

    return pd.DataFrame(rows), inferred


def load_dataset(path: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix in {".pcap", ".pcapng"}:
        return _read_pcap_dataset(path)

    return _read_csv_dataset(path)


def _robust_zscore(series: pd.Series) -> pd.Series:
    median = series.median()
    mad = (series - median).abs().median()
    if pd.isna(mad) or mad == 0:
        return pd.Series(np.zeros(len(series)), index=series.index, dtype=float)
    return (series - median) / (1.4826 * mad)


def detect_statistical_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy().sort_values("timestamp").reset_index(drop=True)
    data["iat_ms"] = data["timestamp"].diff().fillna(0).clip(lower=0) * 1000.0

    data["z_iat"] = _robust_zscore(data["iat_ms"])
    data["z_size"] = _robust_zscore(data["packet_len"])

    iat_anomaly = data["z_iat"].abs() >= 4.0
    size_anomaly = data["z_size"].abs() >= 4.0

    data["is_anomaly"] = iat_anomaly | size_anomaly
    data["anomaly_reason"] = np.select(
        [iat_anomaly & size_anomaly, iat_anomaly, size_anomaly],
        ["timing_and_size", "timing", "size"],
        default="none",
    )

    return data


def detect_replay_sequences(df: pd.DataFrame, min_gap_seconds: float = 0.03) -> pd.DataFrame:
    data = df.copy().sort_values("timestamp").reset_index(drop=True)

    data["flow"] = (
        data["src_ip"].astype(str)
        + ":"
        + data["src_port"].astype(str)
        + "->"
        + data["dst_ip"].astype(str)
        + ":"
        + data["dst_port"].astype(str)
    )

    fallback_hash = (
        data["flow"].astype(str)
        + "|"
        + data["seq"].astype(str)
        + "|"
        + data["ack"].astype(str)
        + "|"
        + data["packet_len"].round(3).astype(str)
    )
    payload_or_fallback = np.where(data["payload_hash"].astype(str).str.len() > 0, data["payload_hash"], fallback_hash)

    data["replay_fingerprint"] = (
        data["flow"].astype(str)
        + "|"
        + data["seq"].astype(str)
        + "|"
        + data["ack"].astype(str)
        + "|"
        + pd.Series(payload_or_fallback, index=data.index).astype(str)
    )

    grouped = data.groupby("replay_fingerprint", dropna=False)
    rows: list[dict[str, object]] = []

    for fingerprint, group in grouped:
        if len(group) < 2:
            continue

        timestamps = group["timestamp"].sort_values().to_numpy()
        gaps = np.diff(timestamps)
        if len(gaps) == 0 or float(np.max(gaps)) < min_gap_seconds:
            continue

        first = group.iloc[0]
        rows.append(
            {
                "flow": first["flow"],
                "sequence": int(first["seq"]),
                "ack": int(first["ack"]),
                "packet_len": float(first["packet_len"]),
                "occurrences": int(len(group)),
                "first_seen": float(group["timestamp"].min()),
                "last_seen": float(group["timestamp"].max()),
                "max_gap_s": float(np.max(gaps)),
                "fingerprint": fingerprint,
            }
        )

    replay_df = pd.DataFrame(rows)
    if replay_df.empty:
        return replay_df

    return replay_df.sort_values(["occurrences", "max_gap_s"], ascending=[False, False]).reset_index(drop=True)


def _flow_volume(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    flow_counts = (
        df.assign(
            flow=df["src_ip"].astype(str)
            + ":"
            + df["src_port"].astype(str)
            + "->"
            + df["dst_ip"].astype(str)
            + ":"
            + df["dst_port"].astype(str)
        )
        .groupby("flow", as_index=False)
        .size()
        .rename(columns={"size": "packets"})
        .sort_values("packets", ascending=False)
        .head(top_n)
    )
    return flow_counts


def build_dashboard(anomaly_df: pd.DataFrame, replay_df: pd.DataFrame, output_path: Path) -> None:
    plt.style.use("dark_background")

    fig, axes = plt.subplots(2, 2, figsize=(16, 10), dpi=120)
    fig.patch.set_facecolor("#0d1117")

    for ax in axes.flatten():
        ax.set_facecolor("#0d1117")
        ax.grid(alpha=0.1, color="white", linestyle="--", linewidth=0.7)
        for spine in ax.spines.values():
            spine.set_color("#1e3a5f")

    # Panel 1: inter-arrival timing with anomalies
    x = np.arange(len(anomaly_df))
    axes[0, 0].plot(x, anomaly_df["iat_ms"], color="#00d4ff", linewidth=1.4, label="IAT (ms)")
    anomalous = anomaly_df["is_anomaly"]
    axes[0, 0].scatter(x[anomalous], anomaly_df.loc[anomalous, "iat_ms"], color="#ff3333", s=20, label="Anomaly")
    axes[0, 0].set_title("Timing Pattern and Anomalies", color="#ff9933", fontsize=12, fontweight="bold")
    axes[0, 0].set_xlabel("Packet Index")
    axes[0, 0].set_ylabel("Inter-arrival Time (ms)")
    axes[0, 0].legend(loc="upper right", frameon=False)

    # Panel 2: packet size distribution
    axes[0, 1].hist(anomaly_df["packet_len"].clip(lower=0), bins=40, color="#138808", alpha=0.8, edgecolor="#1e3a5f")
    axes[0, 1].set_title("Packet Length Distribution", color="#ff9933", fontsize=12, fontweight="bold")
    axes[0, 1].set_xlabel("Packet Length")
    axes[0, 1].set_ylabel("Frequency")

    # Panel 3: top flows
    flows = _flow_volume(anomaly_df)
    if not flows.empty:
        axes[1, 0].barh(flows["flow"].astype(str), flows["packets"], color="#ff9933", alpha=0.9)
        axes[1, 0].invert_yaxis()
    axes[1, 0].set_title("Top TCP Flows", color="#ff9933", fontsize=12, fontweight="bold")
    axes[1, 0].set_xlabel("Packet Count")

    # Panel 4: replay candidates over time
    if replay_df.empty:
        axes[1, 1].text(
            0.5,
            0.5,
            "No replay candidates detected",
            ha="center",
            va="center",
            color="#00ff88",
            fontsize=11,
            transform=axes[1, 1].transAxes,
        )
        axes[1, 1].set_xticks([])
        axes[1, 1].set_yticks([])
    else:
        axes[1, 1].scatter(replay_df["first_seen"], replay_df["occurrences"], color="#ff3333", s=50, alpha=0.9)
        axes[1, 1].set_xlabel("First Seen Timestamp")
        axes[1, 1].set_ylabel("Occurrences")
    axes[1, 1].set_title("Potential Replay Sequences", color="#ff9933", fontsize=12, fontweight="bold")

    fig.suptitle(
        "QSHIELD TCP Forensics Dashboard",
        color="#e8e8e8",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def _risk_level(anomaly_rate: float, replay_count: int) -> str:
    if replay_count >= 3 or anomaly_rate >= 0.2:
        return "HIGH"
    if replay_count >= 1 or anomaly_rate >= 0.08:
        return "MEDIUM"
    return "LOW"


def write_report(
    df: pd.DataFrame,
    anomalies: pd.DataFrame,
    replay_df: pd.DataFrame,
    inferred_columns: dict[str, str],
    output_path: Path,
) -> dict[str, object]:
    anomaly_events = anomalies.loc[anomalies["is_anomaly"]]
    anomaly_rate = float(len(anomaly_events)) / max(1, len(anomalies))
    replay_count = 0 if replay_df.empty else int(len(replay_df))

    summary: dict[str, object] = {
        "packet_count": int(len(df)),
        "dataset_format": str(df["source_format"].iloc[0]) if not df.empty else "unknown",
        "time_start": float(df["timestamp"].min()) if not df.empty else 0.0,
        "time_end": float(df["timestamp"].max()) if not df.empty else 0.0,
        "anomaly_count": int(len(anomaly_events)),
        "anomaly_rate": round(anomaly_rate, 6),
        "replay_candidates": replay_count,
        "risk_level": _risk_level(anomaly_rate=anomaly_rate, replay_count=replay_count),
        "top_flags": anomalies["flags"].astype(str).value_counts().head(8).to_dict(),
        "column_mapping": inferred_columns,
    }

    findings: list[str] = []
    if replay_count > 0:
        findings.append(f"Detected {replay_count} potential replay sequence fingerprints.")
    else:
        findings.append("No high-confidence replay fingerprints were found.")

    if anomaly_rate >= 0.15:
        findings.append("Timing/size anomalies are elevated; investigate retransmission bursts or active probing windows.")
    elif anomaly_rate >= 0.05:
        findings.append("Moderate anomaly rate observed; correlate with expected mission activity windows.")
    else:
        findings.append("Anomaly rate is low and within expected statistical variation.")

    flow_count = int(
        anomalies.assign(flow=anomalies["src_ip"].astype(str) + ":" + anomalies["src_port"].astype(str) + "->" + anomalies["dst_ip"].astype(str) + ":" + anomalies["dst_port"].astype(str))["flow"].nunique()
    )
    findings.append(f"Observed {flow_count} unique TCP flows in the capture.")

    lines = [
        "QSHIELD TCP VULNERABILITY REPORT",
        "================================",
        "",
        f"Dataset format: {summary['dataset_format']}",
        f"Total packets analyzed: {summary['packet_count']}",
        f"Capture time range (epoch): {summary['time_start']:.6f} -> {summary['time_end']:.6f}",
        f"Anomalies: {summary['anomaly_count']} ({summary['anomaly_rate']:.2%})",
        f"Replay candidates: {summary['replay_candidates']}",
        f"Overall risk level: {summary['risk_level']}",
        "",
        "Inferred field mapping:",
    ]

    for key, value in inferred_columns.items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "Key findings:"])
    lines.extend([f"- {item}" for item in findings])

    if not replay_df.empty:
        top_replay = replay_df.head(5)
        lines.extend(["", "Top replay candidates:"])
        for _, row in top_replay.iterrows():
            lines.append(
                f"- flow={row['flow']} seq={int(row['sequence'])} ack={int(row['ack'])} "
                f"occurrences={int(row['occurrences'])} max_gap={float(row['max_gap_s']):.6f}s"
            )

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return summary


def save_artifacts(
    output_dir: Path,
    anomalies: pd.DataFrame,
    replay_df: pd.DataFrame,
    summary: dict[str, object],
) -> None:
    anomalies_out = anomalies.loc[anomalies["is_anomaly"]].copy()
    anomalies_out.to_csv(output_dir / REQUIRED_OUTPUT_FILES["anomalies"], index=False)

    if replay_df.empty:
        pd.DataFrame(
            columns=[
                "flow",
                "sequence",
                "ack",
                "packet_len",
                "occurrences",
                "first_seen",
                "last_seen",
                "max_gap_s",
                "fingerprint",
            ]
        ).to_csv(output_dir / REQUIRED_OUTPUT_FILES["replays"], index=False)
    else:
        replay_df.to_csv(output_dir / REQUIRED_OUTPUT_FILES["replays"], index=False)

    (output_dir / REQUIRED_OUTPUT_FILES["summary"]).write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


def run(input_path: Path, output_dir: Path) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    df, inferred = load_dataset(input_path)

    # Standard cleanup for downstream analysis.
    df = df.dropna(subset=["timestamp"]).copy()
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    if df.empty:
        raise ValueError("Dataset contains no valid TCP rows after parsing and cleanup.")

    anomalies = detect_statistical_anomalies(df)
    replay_df = detect_replay_sequences(df)

    dashboard_path = output_dir / REQUIRED_OUTPUT_FILES["dashboard"]
    report_path = output_dir / REQUIRED_OUTPUT_FILES["report"]

    build_dashboard(anomalies, replay_df, dashboard_path)
    summary = write_report(df, anomalies, replay_df, inferred, report_path)
    save_artifacts(output_dir, anomalies, replay_df, summary)

    print("Analysis complete.")
    print(f"- Dashboard: {dashboard_path}")
    print(f"- Report: {report_path}")
    print(f"- Anomalies CSV: {output_dir / REQUIRED_OUTPUT_FILES['anomalies']}")
    print(f"- Replay CSV: {output_dir / REQUIRED_OUTPUT_FILES['replays']}")
    print(f"- Summary JSON: {output_dir / REQUIRED_OUTPUT_FILES['summary']}")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze TCP captures (CSV/PCAP) for anomalies and replay patterns."
    )
    parser.add_argument("--input", required=True, help="Path to CSV, PCAP, or PCAPNG file")
    parser.add_argument(
        "--output-dir",
        default="qshield_tcp_analysis",
        help="Directory where dashboard/report/artifacts will be saved",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run(input_path=Path(args.input), output_dir=Path(args.output_dir))


if __name__ == "__main__":
    main()
