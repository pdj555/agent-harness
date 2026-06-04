from __future__ import annotations

from pathlib import Path

from agent_harness.store import load_packet, write_packet


def test_write_packet_saves_run_and_latest(tmp_path: Path) -> None:
    packet = {"run_id": "run_test", "value": 1}

    path = write_packet(packet, tmp_path)

    assert path == tmp_path.resolve() / "run_test.json"
    assert load_packet(path) == packet
    assert load_packet(tmp_path / "latest.json") == packet
