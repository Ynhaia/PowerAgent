"""PowerAgent 第三阶段 LangGraph 工作流。

图中每个节点只调用经过后端约束的工具服务。LLM 仅用于 plan_node 的结构化任务规划；
它不能直接访问数据库、网关、知识库、文件系统或任何电厂控制系统。diagnose 节点会在
规则诊断后调用受控的本地 RAG 服务补充来源，RAG 不能覆盖根因或安全规则。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from ..adapters.contracts import PlantDataAdapter
from ..core.config import Settings, settings
from ..domain.schemas import ChatRequest, ChatResponse, TaskPlan, ToolCallSummary
from ..services.diagnosis_service import CondenserVacuumDiagnosisService
from ..services.knowledge_service import KnowledgeService
from ..services.telemetry_service import TelemetryService
from ..services.trace_service import TraceService
from .planner import ResilientPlanner
from .state import AgentState


class PowerAgentGraph:
    """封装 LangGraph 的运行、工具调用与 Trace 记录。"""

    def __init__(
        self,
        adapter: PlantDataAdapter,
        trace_service: TraceService,
        knowledge_service: KnowledgeService,
        config: Settings = settings,
    ) -> None:
        self.adapter = adapter
        self.trace_service = trace_service
        self.planner = ResilientPlanner(config)
        self.telemetry = TelemetryService(adapter)
        self.diagnosis = CondenserVacuumDiagnosisService(adapter)
        self.knowledge_service = knowledge_service
        self.graph = self._build_graph()

    def invoke(self, request: ChatRequest) -> ChatResponse:
        request_id = str(uuid4())
        trace_id = str(uuid4())
        session_id = request.session_id or str(uuid4())
        state: AgentState = {
            "request_id": request_id,
            "trace_id": trace_id,
            "session_id": session_id,
            "question": request.message,
            "input_mode": request.input_mode,
            "requested_unit_id": request.unit_id,
            "requested_window_minutes": request.window_minutes,
            "tool_calls": [],
            "tool_data": {},
        }
        self._trace(state, "graph_start", "started", {"input_mode": request.input_mode})
        result = self.graph.invoke(state)
        plan = TaskPlan.model_validate(result["plan"])
        tool_calls = [ToolCallSummary.model_validate(item) for item in result.get("tool_calls", [])]
        response = ChatResponse(
            request_id=request_id,
            trace_id=trace_id,
            session_id=session_id,
            planner_source=result["planner_source"],
            plan=plan,
            answer=result["answer"],
            tool_calls=tool_calls,
            data=result.get("tool_data", {}),
            safety_notice=result.get("safety_notice"),
        )
        self._trace(state, "graph_end", "succeeded", {"task_type": plan.task_type})
        return response

    def _build_graph(self):
        workflow = StateGraph(AgentState)
        workflow.add_node("plan", self._plan_node)
        workflow.add_node("query", self._query_node)
        workflow.add_node("trend", self._trend_node)
        workflow.add_node("alert", self._alert_node)
        workflow.add_node("diagnose", self._diagnose_node)
        workflow.add_node("response", self._response_node)
        workflow.add_edge(START, "plan")
        workflow.add_conditional_edges(
            "plan",
            self._route,
            {"query": "query", "trend": "trend", "alert": "alert", "diagnose": "diagnose"},
        )
        for node in ("query", "trend", "alert", "diagnose"):
            workflow.add_edge(node, "response")
        workflow.add_edge("response", END)
        return workflow.compile()

    def _plan_node(self, state: AgentState) -> dict[str, object]:
        plan, source = self.planner.plan(state["question"], state.get("requested_unit_id"))
        if state.get("requested_window_minutes") is not None:
            plan = plan.model_copy(update={"window_minutes": state["requested_window_minutes"]})
        self._validate_plan(plan)
        self._trace(
            state,
            "plan",
            "succeeded",
            {"planner_source": source, "task_type": plan.task_type, "unit_id": plan.unit_id},
        )
        return {"plan": plan.model_dump(), "planner_source": source}

    def _validate_plan(self, plan: TaskPlan) -> None:
        """在调用工具之前强制执行机组和测点白名单校验。"""
        known_units = {unit.unit_id for unit in self.adapter.list_units()}
        if plan.unit_id not in known_units:
            raise ValueError(f"当前数据源无权访问或不存在机组：{plan.unit_id}")
        known_tags = set(self.adapter.list_tag_codes())
        unknown_tags = sorted(set(plan.tag_codes) - known_tags)
        if unknown_tags:
            raise ValueError(f"执行计划含未定义测点：{', '.join(unknown_tags)}")

    @staticmethod
    def _route(state: AgentState) -> str:
        return str(state["plan"]["task_type"])

    def _query_node(self, state: AgentState) -> dict[str, object]:
        plan = TaskPlan.model_validate(state["plan"])
        trend = self._read_trend(plan)
        latest = {
            series.tag_code: series.points[-1].model_dump(mode="json")
            for series in trend.series
            if series.points
        }
        tool_call = self._tool_call("telemetry_tool", "succeeded", f"返回 {len(trend.series)} 个测点的最新值。")
        self._trace(state, "query", "succeeded", {"tag_codes": plan.tag_codes, "point_count": sum(len(s.points) for s in trend.series)})
        return {"tool_calls": [*state["tool_calls"], tool_call], "tool_data": {"latest": latest}}

    def _trend_node(self, state: AgentState) -> dict[str, object]:
        plan = TaskPlan.model_validate(state["plan"])
        trend = self._read_trend(plan)
        tool_call = self._tool_call("trend_tool", "succeeded", f"生成 {len(trend.series)} 条趋势序列。")
        self._trace(state, "trend", "succeeded", {"tag_codes": plan.tag_codes, "window_minutes": plan.window_minutes})
        return {
            "tool_calls": [*state["tool_calls"], tool_call],
            "tool_data": {"trend": trend.model_dump(mode="json")},
        }

    def _alert_node(self, state: AgentState) -> dict[str, object]:
        plan = TaskPlan.model_validate(state["plan"])
        end_time = self.adapter.default_end_time()
        start_time = end_time - timedelta(minutes=plan.window_minutes)
        alerts = self.adapter.get_alerts(plan.unit_id, start_time, end_time)
        tool_call = self._tool_call("alert_tool", "succeeded", f"返回 {len(alerts)} 条预警事件。")
        self._trace(state, "alert", "succeeded", {"alert_count": len(alerts), "window_minutes": plan.window_minutes})
        return {
            "tool_calls": [*state["tool_calls"], tool_call],
            "tool_data": {"alerts": [alert.model_dump(mode="json") for alert in alerts]},
        }

    def _diagnose_node(self, state: AgentState) -> dict[str, object]:
        plan = TaskPlan.model_validate(state["plan"])
        diagnosis = self.diagnosis.diagnose(plan.unit_id, plan.window_minutes)
        primary = diagnosis.candidates[0]
        citations = self.knowledge_service.search(
            query=f"{state['question']} {primary.cause_name} {primary.cause_code}",
            unit_id=plan.unit_id,
            system="condenser",
            top_k=3,
        ) if diagnosis.result_status == "candidate_found" else []
        tool_call = self._tool_call("condenser_vacuum_diagnosis_tool", "succeeded", "完成凝汽器真空下降专题规则诊断。")
        rag_tool_call = self._tool_call(
            "rag_tool",
            "succeeded" if citations else "skipped",
            f"检索到 {len(citations)} 条版本化知识依据。" if citations else "未检索到适用的知识依据，不生成规程性结论。",
        )
        self._trace(
            state,
            "diagnose",
            "succeeded",
            {"risk_level": diagnosis.risk_level, "primary_cause": primary.cause_code},
        )
        self._trace(
            state,
            "rag",
            "succeeded" if citations else "blocked",
            {"citation_count": len(citations), "document_ids": [citation.document_id for citation in citations]},
        )
        diagnosis_data = diagnosis.model_dump(mode="json")
        diagnosis_data["trace_id"] = state["trace_id"]
        diagnosis_data["knowledge_citations"] = [citation.model_dump() for citation in citations]
        return {
            "tool_calls": [*state["tool_calls"], tool_call, rag_tool_call],
            "tool_data": {"diagnosis": diagnosis_data},
            "safety_notice": diagnosis.safety_notice,
        }

    def _response_node(self, state: AgentState) -> dict[str, object]:
        plan = TaskPlan.model_validate(state["plan"])
        data = state.get("tool_data", {})
        if plan.task_type == "diagnose":
            diagnosis = data["diagnosis"]
            primary = diagnosis["candidates"][0]
            answer = (
                f"已完成 {plan.unit_id} 最近 {plan.window_minutes} 分钟的凝汽器真空专题诊断。"
                f"风险等级为 {diagnosis['risk_level']}；诊断结果为“{primary['cause_name']}”，"
                f"规则评分 {primary['rule_score']}，规则支持度 {primary['confidence']:.0%}（非故障概率）。"
                f"建议先{primary['verification_steps'][0]}"
            )
            citations = diagnosis.get("knowledge_citations", [])
            if citations:
                source = citations[0]
                answer += f"知识依据：{source['title']}（{source['version']}，{source['section_path']}，来源状态：{source['source_status']}）。"
            else:
                answer += "未返回知识依据，不生成规程性处置结论。"
        elif plan.task_type == "alert":
            alerts = data["alerts"]
            answer = f"已查询 {plan.unit_id} 最近 {plan.window_minutes} 分钟预警，共发现 {len(alerts)} 条关联事件。"
        elif plan.task_type == "trend":
            trend = data["trend"]
            tags = "、".join(series["tag_name"] for series in trend["series"])
            answer = f"已生成 {plan.unit_id} 最近 {plan.window_minutes} 分钟的 {tags} 趋势分析。"
        else:
            latest = data["latest"]
            names = "、".join(latest.keys())
            answer = f"已查询 {plan.unit_id} 的最新测点数据：{names}。"
        self._trace(state, "response", "succeeded", {"task_type": plan.task_type})
        return {"answer": answer}

    def _read_trend(self, plan: TaskPlan):
        end_time = self.adapter.default_end_time()
        start_time = end_time - timedelta(minutes=plan.window_minutes)
        return self.telemetry.trend(plan.unit_id, plan.tag_codes, start_time, end_time)

    @staticmethod
    def _tool_call(tool_name: str, status: str, summary: str) -> dict[str, str]:
        return {"tool_name": tool_name, "status": status, "summary": summary}

    def _trace(
        self,
        state: AgentState,
        node_name: str,
        status: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.trace_service.append(
            trace_id=state["trace_id"],
            request_id=state["request_id"],
            session_id=state["session_id"],
            node_name=node_name,
            status=status,
            payload=payload,
        )
