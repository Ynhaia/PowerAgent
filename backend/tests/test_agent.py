import unittest

from backend.app.adapters.mock_plant import MockPlantDataAdapter
from backend.app.agent.graph import PowerAgentGraph
from backend.app.agent.planner import RuleBasedPlanner
from backend.app.domain.schemas import ChatRequest
from backend.app.services.knowledge_service import KnowledgeService
from backend.app.services.trace_service import InMemoryTraceService


class RuleBasedPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = RuleBasedPlanner()

    def test_extracts_diagnosis_unit_and_time_window(self) -> None:
        plan = self.planner.plan("分析 1 号机最近两小时凝汽器真空为什么下降，并给出处置建议")
        self.assertEqual(plan.task_type, "diagnose")
        self.assertEqual(plan.unit_id, "U01")
        self.assertEqual(plan.window_minutes, 120)
        self.assertIn("CW_FLOW", plan.tag_codes)

    def test_routes_alarm_question_to_alert_tool(self) -> None:
        plan = self.planner.plan("查询 1 号机最近 30 分钟的预警")
        self.assertEqual(plan.task_type, "alert")
        self.assertEqual(plan.window_minutes, 30)

    def test_routes_trend_question_to_trend_tool(self) -> None:
        plan = self.planner.plan("查看 1 号机最近 3 小时循环水流量趋势")
        self.assertEqual(plan.task_type, "trend")
        self.assertEqual(plan.window_minutes, 180)
        self.assertEqual(plan.tag_codes, ["CW_FLOW"])


class PowerAgentGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_service = InMemoryTraceService()
        self.graph = PowerAgentGraph(MockPlantDataAdapter(), self.trace_service, KnowledgeService.default())

    def test_diagnosis_runs_through_langgraph_and_records_trace(self) -> None:
        response = self.graph.invoke(
            ChatRequest(message="分析 1 号机最近两小时凝汽器真空为什么下降，并给出处置建议")
        )
        self.assertEqual(response.planner_source, "rule_fallback")
        self.assertEqual(response.plan.task_type, "diagnose")
        self.assertEqual(response.data["diagnosis"]["candidates"][0]["cause_code"], "cw_flow_insufficient")
        self.assertGreaterEqual(len(response.data["diagnosis"]["knowledge_citations"]), 1)
        self.assertEqual(response.tool_calls[1].tool_name, "rag_tool")
        event_names = [event.node_name for event in self.trace_service.get(response.trace_id)]
        self.assertEqual(event_names, ["graph_start", "plan", "diagnose", "rag", "response", "graph_end"])

    def test_trend_question_uses_trend_tool(self) -> None:
        response = self.graph.invoke(ChatRequest(message="查看 1 号机最近 3 小时循环水流量趋势"))
        self.assertEqual(response.plan.task_type, "trend")
        self.assertEqual(response.tool_calls[0].tool_name, "trend_tool")
        self.assertIn("trend", response.data)


if __name__ == "__main__":
    unittest.main()
