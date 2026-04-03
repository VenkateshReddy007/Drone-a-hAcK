"""QSHIELD simulation engine.

This module generates a deterministic packet stream that mixes legitimate
traffic with replay, spoofing, and timing-probe attacks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import random
from typing import Dict, List, Optional


@dataclass
class Packet:
    """Represents one RF packet observed on the classical QKD support channel."""

    sequence_id: int
    timestamp: str
    command: str
    auth_token: str
    signal_dbm: float
    frequency_mhz: float
    response_ms: float
    source_identity: str
    is_key_exchange_moment: bool
    packet_kind: str  # legitimate | replay | spoof | timing_probe
    replay_of_sequence: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class DroneSimulationEngine:
    """Builds a predictable stream of packets for demonstration purposes."""

    def __init__(self, seed: int = 515):
        self.rng = random.Random(seed)
        self.base_time = datetime(2026, 4, 2, 10, 0, 0)

        # Fixed attack injection points make the demo repeatable and judge-friendly.
        self.attack_plan = {
            12: "replay",
            23: "spoof",
            31: "timing_probe",
            44: "replay",
            52: "spoof",
            61: "timing_probe",
        }

    def _build_legitimate_token(self, sequence_id: int) -> str:
        token_core = f"{self.rng.getrandbits(32):08X}"
        return f"CTRL-{token_core}-{sequence_id % 100:02d}"

    def _is_key_exchange_slot(self, sequence_id: int) -> bool:
        return sequence_id % 20 == 0 or sequence_id in {31, 61}

    def _generate_legitimate_packet(self, sequence_id: int) -> Packet:
        """Create normal command traffic from the legitimate ground controller."""
        timestamp = self.base_time + timedelta(milliseconds=sequence_id * 75)
        command = self.rng.choice(
            [
                "ARM",
                "HOLD_ALT",
                "WAYPOINT_UPDATE",
                "STATUS_POLL",
                "NAV_CORRECT",
                "RETURN_HOME",
            ]
        )
        token = self._build_legitimate_token(sequence_id)
        signal_dbm = round(-58 + self.rng.uniform(-3.8, 3.8), 2)
        frequency_mhz = round(2450.0 + self.rng.uniform(-1.1, 1.1), 2)

        # Response time rises slightly during key exchange, even in healthy traffic.
        is_key_moment = self._is_key_exchange_slot(sequence_id)
        response_baseline = 14.0 if not is_key_moment else 20.0
        response_ms = round(response_baseline + self.rng.uniform(-2.0, 2.5), 2)

        return Packet(
            sequence_id=sequence_id,
            timestamp=timestamp.isoformat(timespec="milliseconds"),
            command=command,
            auth_token=token,
            signal_dbm=signal_dbm,
            frequency_mhz=frequency_mhz,
            response_ms=response_ms,
            source_identity="GCS_ALPHA",
            is_key_exchange_moment=is_key_moment,
            packet_kind="legitimate",
        )

    def _inject_replay_packet(self, sequence_id: int, history: Dict[int, Packet]) -> Packet:
        """Replay attack reuses a previously captured packet payload."""
        # Custom scenario plans may place replay attacks before fixed sequence IDs.
        # Select a safe historical source packet that always exists in history.
        preferred_sequence = 5 if sequence_id <= 20 else 21
        if preferred_sequence in history:
            target_sequence = preferred_sequence
        else:
            # Fall back to the most recent legitimate packet before this replay.
            target_sequence = max(history.keys())

        original = history[target_sequence]

        # Keep payload characteristics from original packet to emulate replay capture.
        replay_packet = Packet(
            sequence_id=sequence_id,
            timestamp=(self.base_time + timedelta(milliseconds=sequence_id * 75)).isoformat(
                timespec="milliseconds"
            ),
            command=original.command,
            auth_token=original.auth_token,
            signal_dbm=round(original.signal_dbm + self.rng.uniform(-0.4, 0.4), 2),
            frequency_mhz=original.frequency_mhz,
            response_ms=round(original.response_ms + self.rng.uniform(-0.6, 0.8), 2),
            source_identity=original.source_identity,
            is_key_exchange_moment=False,
            packet_kind="replay",
            replay_of_sequence=target_sequence,
        )
        return replay_packet

    def _inject_spoof_packet(self, sequence_id: int) -> Packet:
        """Spoofing attack uses forged identity, malformed token, and odd RF profile."""
        timestamp = self.base_time + timedelta(milliseconds=sequence_id * 75)
        return Packet(
            sequence_id=sequence_id,
            timestamp=timestamp.isoformat(timespec="milliseconds"),
            command=self.rng.choice(["DISARM", "OVERRIDE", "LAND_NOW"]),
            auth_token=f"XFAKE-{self.rng.getrandbits(24):06X}",
            signal_dbm=round(-41 + self.rng.uniform(-2.2, 2.2), 2),
            frequency_mhz=round(2453.2 + self.rng.uniform(-2.8, 2.8), 2),
            response_ms=round(11 + self.rng.uniform(-1.5, 1.5), 2),
            source_identity="ROGUE_TRANSMITTER",
            is_key_exchange_moment=False,
            packet_kind="spoof",
        )

    def _inject_timing_probe_packet(self, sequence_id: int) -> Packet:
        """Timing probe pushes abnormal response timing around key exchange moments."""
        timestamp = self.base_time + timedelta(milliseconds=sequence_id * 75)
        return Packet(
            sequence_id=sequence_id,
            timestamp=timestamp.isoformat(timespec="milliseconds"),
            command="STATUS_POLL",
            auth_token=self._build_legitimate_token(sequence_id),
            signal_dbm=round(-57 + self.rng.uniform(-2.0, 2.0), 2),
            frequency_mhz=round(2450.2 + self.rng.uniform(-1.0, 1.0), 2),
            response_ms=round(34 + self.rng.uniform(-2.5, 2.5), 2),
            source_identity="GCS_ALPHA",
            is_key_exchange_moment=True,
            packet_kind="timing_probe",
        )

    def generate_packets(self, total_packets: int = 70) -> List[Packet]:
        """Generate full deterministic scenario with planned attack packets."""
        packets: List[Packet] = []
        history_by_seq: Dict[int, Packet] = {}

        for sequence_id in range(1, total_packets + 1):
            attack_type = self.attack_plan.get(sequence_id)

            if attack_type == "replay" and history_by_seq:
                packet = self._inject_replay_packet(sequence_id, history_by_seq)
            elif attack_type == "spoof":
                packet = self._inject_spoof_packet(sequence_id)
            elif attack_type == "timing_probe":
                packet = self._inject_timing_probe_packet(sequence_id)
            else:
                packet = self._generate_legitimate_packet(sequence_id)

            packets.append(packet)

            # Track only legitimate packets for later replay references.
            if packet.packet_kind == "legitimate":
                history_by_seq[packet.sequence_id] = packet

        return packets


def get_simulated_packet_dicts(total_packets: int = 70, seed: int = 515) -> List[Dict[str, object]]:
    """Convenience helper to return packets as dictionaries."""
    engine = DroneSimulationEngine(seed=seed)
    return [packet.to_dict() for packet in engine.generate_packets(total_packets=total_packets)]
