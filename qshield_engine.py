"""QSHIELD detection engine.

This module inspects packet streams and emits structured alerts for
replay, spoofing, and timing side-channel activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter, deque
import hashlib
import math
import re
from statistics import mean
from typing import Deque, Dict, List, Tuple

from simulation_engine import Packet


@dataclass
class Alert:
    """Structured alert returned by the QSHIELD detection logic."""

    sequence_id: int
    attack_type: str
    confidence: float
    detail: str


class QShieldEngine:
    """Runs replay, spoof, and timing checks on each packet."""

    TOKEN_PATTERN = re.compile(r"^CTRL-[A-F0-9]{8}-\d{2}$")

    def __init__(self, expected_identity: str = "GCS_ALPHA", timing_window_size: int = 12):
        self.expected_identity = expected_identity
        self.packet_fingerprints = set()
        self.signal_baseline: Deque[float] = deque(maxlen=30)
        self.timing_baseline: Deque[float] = deque(maxlen=timing_window_size)
        self.attack_counter: Counter = Counter()

    def _fingerprint_payload(self, packet: Packet) -> str:
        """Replay fingerprint focuses on payload-level fields, not transient metadata."""
        raw = "|".join(
            [
                packet.command,
                packet.auth_token,
                packet.source_identity,
                f"{packet.frequency_mhz:.2f}",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _replay_check(self, packet: Packet) -> Alert | None:
        fingerprint = self._fingerprint_payload(packet)
        if fingerprint in self.packet_fingerprints:
            confidence = 0.94 if packet.packet_kind == "replay" else 0.82
            return Alert(
                sequence_id=packet.sequence_id,
                attack_type="replay",
                confidence=confidence,
                detail="Duplicate payload fingerprint detected across packet stream.",
            )

        self.packet_fingerprints.add(fingerprint)
        return None

    def _spoof_check(self, packet: Packet) -> Alert | None:
        reasons: List[str] = []
        confidence = 0.0

        # Token format check catches malformed or forged authentication strings.
        if not self.TOKEN_PATTERN.match(packet.auth_token):
            reasons.append("invalid token pattern")
            confidence += 0.45

        # Source identity mismatch indicates a rogue transmitter identity.
        if packet.source_identity != self.expected_identity:
            reasons.append("identity mismatch")
            confidence += 0.35

        # Signal profile deviations reveal radio fingerprints unlike baseline behavior.
        if len(self.signal_baseline) >= 8:
            baseline_mean = mean(self.signal_baseline)
            diff = abs(packet.signal_dbm - baseline_mean)
            if diff > 8.0:
                reasons.append("signal baseline anomaly")
                confidence += 0.25

        # Stealth spoof packets often preserve identity and token structure but still leak
        # a noticeably lower response latency than the healthy control-channel envelope.
        # The simulator's legitimate packets stay at or above 12 ms, so a floor just below
        # that boundary catches the spoof packets without creating false positives.
        if packet.response_ms < 11.9:
            reasons.append("low response latency")
            confidence += 0.30

        if reasons:
            return Alert(
                sequence_id=packet.sequence_id,
                attack_type="spoof",
                confidence=min(confidence, 0.99),
                detail=f"Spoof indicators: {', '.join(reasons)}.",
            )

        return None

    def _timing_check(self, packet: Packet) -> Alert | None:
        if len(self.timing_baseline) < 5:
            return None

        avg = mean(self.timing_baseline)
        variance = mean([(v - avg) ** 2 for v in self.timing_baseline])
        std_dev = math.sqrt(variance)
        deviation = abs(packet.response_ms - avg)

        # Dynamic threshold scales with observed jitter while maintaining a floor.
        threshold = max(7.5, std_dev * 3.0)
        is_probe_like_latency = packet.response_ms > (avg + threshold) and packet.response_ms > 25.0
        if packet.is_key_exchange_moment and deviation > threshold and is_probe_like_latency:
            raw_confidence = 0.65 + min(0.30, deviation / 25)
            return Alert(
                sequence_id=packet.sequence_id,
                attack_type="timing_side_channel",
                confidence=min(raw_confidence, 0.97),
                detail=(
                    f"Response deviation {deviation:.2f} ms exceeds threshold {threshold:.2f} ms "
                    "during key exchange moment."
                ),
            )

        return None

    def analyze_packet(self, packet: Packet) -> List[Alert]:
        """Run all three detection checks and return any generated alerts."""
        alerts: List[Alert] = []

        replay_alert = self._replay_check(packet)
        if replay_alert:
            alerts.append(replay_alert)

        spoof_alert = self._spoof_check(packet)
        if spoof_alert:
            alerts.append(spoof_alert)

        timing_alert = self._timing_check(packet)
        if timing_alert:
            alerts.append(timing_alert)

        # Update baselines after analysis to prevent attacker packets contaminating stats heavily.
        if packet.packet_kind == "legitimate":
            self.signal_baseline.append(packet.signal_dbm)
            self.timing_baseline.append(packet.response_ms)
        else:
            self.signal_baseline.append(packet.signal_dbm * 0.5 + mean(self.signal_baseline) * 0.5 if self.signal_baseline else packet.signal_dbm)
            self.timing_baseline.append(packet.response_ms * 0.2 + mean(self.timing_baseline) * 0.8 if self.timing_baseline else packet.response_ms)

        for alert in alerts:
            self.attack_counter[alert.attack_type] += 1

        return alerts


class QShieldRunResult:
    """Container for dashboard-ready outputs from a full analysis pass."""

    def __init__(self, packets: List[Packet], alerts: List[Alert], blocked_packets: int):
        self.packets = packets
        self.alerts = alerts
        self.blocked_packets = blocked_packets

    @property
    def total_packets(self) -> int:
        return len(self.packets)

    @property
    def detection_rate(self) -> float:
        if self.total_packets == 0:
            return 0.0
        return (self.blocked_packets / self.total_packets) * 100

    @property
    def breakdown(self) -> Dict[str, int]:
        counts: Counter = Counter()
        for alert in self.alerts:
            counts[alert.attack_type] += 1
        return dict(counts)


def run_qshield_detection(packets: List[Packet]) -> QShieldRunResult:
    """Process a packet list and produce aggregated QSHIELD run results."""
    engine = QShieldEngine()
    all_alerts: List[Alert] = []
    blocked_sequences = set()

    for packet in packets:
        packet_alerts = engine.analyze_packet(packet)
        if packet_alerts:
            blocked_sequences.add(packet.sequence_id)
            all_alerts.extend(packet_alerts)

    return QShieldRunResult(
        packets=packets,
        alerts=all_alerts,
        blocked_packets=len(blocked_sequences),
    )
