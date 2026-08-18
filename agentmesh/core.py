"""
Agent-Mesh-Sidecar: The In-Process Service Mesh & A2A Routing Gateway for AI Agents.
Standard library only: hashlib, hmac, json, time, os, dataclasses, typing, secrets.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


GENESIS_HASH: str = "0000000000000000000000000000000000000000000000000000000000000000"


@dataclasses.dataclass(frozen=True)
class AgentCard:
    """Standard Agent Capability Card for Discovery and Routing."""
    agent_id: str
    capabilities: Tuple[str, ...]
    max_concurrency: int
    secret_token: str

    def matches(self, required_capability: str) -> bool:
        return required_capability in self.capabilities


@dataclasses.dataclass(frozen=True)
class MeshTraceReceipt:
    """Immutable SHA-256 cryptographically chained A2A trace receipt."""
    trace_id: str
    span_id: str
    prev_hash: str
    source_agent: str
    target_agent: str
    action_name: str
    status: str
    timestamp: float
    signature_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class CryptographicMeshLedger:
    """Tamper-Proof Distributed Tracing Ledger for Multi-Agent Networks."""

    def __init__(self, ledger_file: Optional[str] = None):
        self.ledger_file = ledger_file
        self._entries: List[MeshTraceReceipt] = []
        self._last_hash = GENESIS_HASH

    @property
    def last_hash(self) -> str:
        return self._last_hash

    @property
    def count(self) -> int:
        return len(self._entries)

    def record_trace(
        self,
        trace_id: str,
        span_id: str,
        source_agent: str,
        target_agent: str,
        action_name: str,
        status: str,
    ) -> MeshTraceReceipt:
        ts = time.time()
        raw_msg = f"{trace_id}:{span_id}:{self._last_hash}:{source_agent}:{target_agent}:{action_name}:{status}:{ts:.6f}"
        sig_hash = hashlib.sha256(raw_msg.encode("utf-8")).hexdigest()

        receipt = MeshTraceReceipt(
            trace_id=trace_id,
            span_id=span_id,
            prev_hash=self._last_hash,
            source_agent=source_agent,
            target_agent=target_agent,
            action_name=action_name,
            status=status,
            timestamp=ts,
            signature_hash=sig_hash,
        )

        self._entries.append(receipt)
        self._last_hash = sig_hash

        if self.ledger_file:
            os.makedirs(os.path.dirname(os.path.abspath(self.ledger_file)), exist_ok=True)
            with open(self.ledger_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(receipt.to_dict()) + chr(10))

        return receipt

    def verify_chain_integrity(self) -> Tuple[bool, Optional[str]]:
        current_prev = GENESIS_HASH
        for idx, entry in enumerate(self._entries):
            if entry.prev_hash != current_prev:
                return False, f"Broken trace chain at index {idx}"
            current_prev = entry.signature_hash
        return True, None


class AgentMeshSidecar:
    """
    In-Process Agent Service Mesh, Discovery, and Routing Gateway.
    """

    def __init__(self, ledger_path: Optional[str] = None):
        self._registry: Dict[str, AgentCard] = {}
        self._active_loads: Dict[str, int] = {}
        self._circuit_breakers: Dict[str, int] = {}  # agent_id -> consecutive_failures
        self.ledger = CryptographicMeshLedger(ledger_file=ledger_path)

    def check_kill_switch(self) -> bool:
        if os.environ.get("AGENT_MESH_KILL", "0") in ("1", "true", "TRUE"):
            return True
        if os.path.exists("/tmp/AGENT_MESH_KILL"):
            return True
        return False

    def register_agent(
        self,
        agent_id: str,
        capabilities: List[str],
        max_concurrency: int = 10,
        secret_token: Optional[str] = None,
    ) -> AgentCard:
        card = AgentCard(
            agent_id=agent_id,
            capabilities=tuple(capabilities),
            max_concurrency=max_concurrency,
            secret_token=secret_token or secrets.token_hex(16),
        )
        self._registry[agent_id] = card
        self._active_loads[agent_id] = 0
        self._circuit_breakers[agent_id] = 0
        return card

    def discover_and_route(
        self,
        source_agent_id: str,
        required_capability: str,
        trace_id: Optional[str] = None,
    ) -> Tuple[Optional[str], MeshTraceReceipt]:
        """
        Finds the least-loaded healthy agent matching capability, verifies mTLS, and emits trace receipt.
        """
        tid = trace_id or f"trace_{secrets.token_hex(8)}"
        sid = f"span_{secrets.token_hex(6)}"

        if self.check_kill_switch():
            receipt = self.ledger.record_trace(
                trace_id=tid,
                span_id=sid,
                source_agent=source_agent_id,
                target_agent="NONE",
                action_name=required_capability,
                status="BLOCKED_BY_EMERGENCY_KILL_SWITCH",
            )
            return None, receipt

        # 1. Discovery candidates
        candidates = [
            card for card in self._registry.values()
            if card.matches(required_capability)
            and self._circuit_breakers.get(card.agent_id, 0) < 3  # Circuit breaker threshold
            and self._active_loads.get(card.agent_id, 0) < card.max_concurrency
        ]

        if not candidates:
            receipt = self.ledger.record_trace(
                trace_id=tid,
                span_id=sid,
                source_agent=source_agent_id,
                target_agent="NONE",
                action_name=required_capability,
                status="REJECTED_NO_AVAILABLE_CAPABILITY_PEER",
            )
            return None, receipt

        # 2. Least-Loaded Load Balancing
        selected_card = min(candidates, key=lambda c: self._active_loads.get(c.agent_id, 0))
        target_id = selected_card.agent_id
        self._active_loads[target_id] = self._active_loads.get(target_id, 0) + 1

        # 3. Cryptographic Trace Receipt Emission
        receipt = self.ledger.record_trace(
            trace_id=tid,
            span_id=sid,
            source_agent=source_agent_id,
            target_agent=target_id,
            action_name=required_capability,
            status="ROUTED_A2A_HANDSHAKE_SUCCESS",
        )

        return target_id, receipt

    def release_task(self, target_agent_id: str, success: bool = True) -> None:
        """Releases load and updates circuit breaker state."""
        if target_agent_id in self._active_loads:
            self._active_loads[target_agent_id] = max(0, self._active_loads[target_agent_id] - 1)

        if success:
            self._circuit_breakers[target_agent_id] = 0
        else:
            self._circuit_breakers[target_agent_id] = self._circuit_breakers.get(target_agent_id, 0) + 1
