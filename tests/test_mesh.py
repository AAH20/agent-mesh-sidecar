import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agentmesh.core import AgentMeshSidecar, GENESIS_HASH


class TestAgentMeshSidecar(unittest.TestCase):
    def setUp(self):
        self.mesh = AgentMeshSidecar()
        # Register specialist agents
        self.mesh.register_agent('sql_specialist_1', ['sql_query', 'table_mutate'], max_concurrency=2)
        self.mesh.register_agent('sql_specialist_2', ['sql_query'], max_concurrency=5)
        self.mesh.register_agent('k8s_deployer', ['k8s_scale', 'k8s_apply'], max_concurrency=3)

    def test_capability_discovery_and_least_loaded_routing(self):
        # Route 1st sql_query task -> routes to specialist
        target1, receipt1 = self.mesh.discover_and_route('planner_agent', 'sql_query')
        self.assertIn(target1, ['sql_specialist_1', 'sql_specialist_2'])
        self.assertEqual(receipt1.status, 'ROUTED_A2A_HANDSHAKE_SUCCESS')
        self.assertNotEqual(receipt1.signature_hash, GENESIS_HASH)

        # Route k8s task
        target_k8s, receipt_k8s = self.mesh.discover_and_route('planner_agent', 'k8s_scale')
        self.assertEqual(target_k8s, 'k8s_deployer')

        # Verify trace ledger chain integrity
        is_valid, err = self.mesh.ledger.verify_chain_integrity()
        self.assertTrue(is_valid, f'Mesh ledger broken: {err}')

    def test_missing_capability_rejection(self):
        # Request non-existent capability
        target, receipt = self.mesh.discover_and_route('planner_agent', 'quantum_annealing')
        self.assertIsNone(target)
        self.assertEqual(receipt.status, 'REJECTED_NO_AVAILABLE_CAPABILITY_PEER')

    def test_circuit_breaker_isolation(self):
        # Trigger 3 consecutive failures on sql_specialist_1
        self.mesh.release_task('sql_specialist_1', success=False)
        self.mesh.release_task('sql_specialist_1', success=False)
        self.mesh.release_task('sql_specialist_1', success=False)

        # sql_specialist_1 must now be quarantined; all queries route to sql_specialist_2
        for _ in range(3):
            target, _ = self.mesh.discover_and_route('planner_agent', 'sql_query')
            self.assertEqual(target, 'sql_specialist_2')


if __name__ == '__main__':
    unittest.main()
