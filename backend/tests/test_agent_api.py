import unittest

from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.main import app


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_chat_returns_agent_plan_and_trace_can_be_replayed(self) -> None:
        response = self.client.post(
            "/api/v1/chat",
            json={"message": "分析 1 号机最近两小时凝汽器真空为什么下降，并给出处置建议"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["planner_source"], "rule_fallback")
        self.assertEqual(body["plan"]["task_type"], "diagnose")
        self.assertEqual(body["data"]["diagnosis"]["candidates"][0]["cause_code"], "cw_flow_insufficient")

        trace = self.client.get(f"/api/v1/traces/{body['trace_id']}")
        self.assertEqual(trace.status_code, 200)
        self.assertEqual([event["node_name"] for event in trace.json()["events"]], [
            "graph_start",
            "plan",
            "diagnose",
            "rag",
            "response",
            "graph_end",
        ])

    def test_chat_honors_explicit_window_without_changing_safe_route(self) -> None:
        response = self.client.post(
            "/api/v1/chat",
            json={"message": "分析凝汽器真空下降原因", "unit_id": "U01", "window_minutes": 60},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"]["window_minutes"], 60)
        self.assertEqual(response.json()["plan"]["task_type"], "diagnose")

    def test_health_reflects_local_llm_configuration_without_reading_key(self) -> None:
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["llm_enabled"], settings.llm_enabled)
        self.assertEqual(response.json()["database_configured"], settings.database_configured)
        self.assertEqual(response.json()["trace_store"], settings.trace_store)

    def test_knowledge_search_returns_versioned_citations(self) -> None:
        response = self.client.post(
            "/api/v1/knowledge/search",
            json={"query": "凝汽器真空下降时循环水流量不足的核查", "unit_id": "U01", "system": "condenser"},
        )
        self.assertEqual(response.status_code, 200)
        citations = response.json()["citations"]
        self.assertTrue(citations)
        self.assertEqual(citations[0]["document_id"], "SOP-COND-001")
        self.assertEqual(citations[0]["source_status"], "demo_verified")

    def test_monitoring_window_is_constrained_by_backend(self) -> None:
        response = self.client.get(
            "/api/v1/telemetry/trend?unit_id=U01&tags=COND_ABS_PRESSURE&window_minutes=60"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["analysis"]["COND_ABS_PRESSURE"]["point_count"], 61)



if __name__ == "__main__":
    unittest.main()
