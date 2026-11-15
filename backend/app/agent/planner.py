"""意图规划器：优先云端 DeepSeek，失败或未配置时安全降级到规则规划。

规划器只能提出结构化查询计划；测点白名单、时间上限、权限与实际工具调用仍由
后端服务控制。不会让模型直接生成 SQL、网关请求或控制命令。
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from ..core.config import Settings
from ..domain.schemas import TaskPlan


class PlanningError(RuntimeError):
    """云端规划返回不满足契约或不可用。"""


class Planner(Protocol):
    source: str

    def plan(self, question: str, requested_unit_id: str | None = None) -> TaskPlan:
        """把用户问题转换成受约束的 TaskPlan。"""


class RuleBasedPlanner:
    """无 Key 或云端异常时的确定性降级规划器。"""

    source = "rule_fallback"
    _core_tags = ["COND_ABS_PRESSURE", "CW_FLOW", "CW_PUMP_A_CURRENT", "CW_PUMP_A_FREQ"]
    _tag_aliases = {
        "真空": "COND_ABS_PRESSURE",
        "绝对压力": "COND_ABS_PRESSURE",
        "凝汽器": "COND_ABS_PRESSURE",
        "循环水流量": "CW_FLOW",
        "循环水泵电流": "CW_PUMP_A_CURRENT",
        "循环水泵频率": "CW_PUMP_A_FREQ",
        "抽气器": "AIR_EXTRACTOR_PRESSURE",
        "负荷": "UNIT_LOAD_MW",
    }

    def plan(self, question: str, requested_unit_id: str | None = None) -> TaskPlan:
        normalized = question.strip()
        unit_id = requested_unit_id or self._unit_id(normalized)
        window_minutes = self._window_minutes(normalized)
        tag_codes = self._tag_codes(normalized)

        if any(token in normalized for token in ("为什么", "原因", "诊断", "处置", "建议", "故障")):
            return TaskPlan(
                task_type="diagnose",
                unit_id=unit_id,
                tag_codes=self._core_tags,
                window_minutes=window_minutes,
                include_alerts=True,
                reason="问题包含故障原因、诊断或处置诉求，执行凝汽器真空专题诊断。",
            )
        if any(token in normalized for token in ("告警", "预警", "报警")):
            return TaskPlan(
                task_type="alert",
                unit_id=unit_id,
                tag_codes=tag_codes or ["COND_ABS_PRESSURE"],
                window_minutes=window_minutes,
                include_alerts=True,
                reason="问题聚焦告警或预警信息。",
            )
        if any(token in normalized for token in ("趋势", "曲线", "变化", "走势", "对比", "分析")):
            return TaskPlan(
                task_type="trend",
                unit_id=unit_id,
                tag_codes=tag_codes or ["COND_ABS_PRESSURE", "CW_FLOW"],
                window_minutes=window_minutes,
                reason="问题要求查看趋势、变化或时段分析。",
            )
        return TaskPlan(
            task_type="query",
            unit_id=unit_id,
            tag_codes=tag_codes or ["COND_ABS_PRESSURE"],
            window_minutes=window_minutes,
            reason="默认执行结构化测点查询。",
        )

    @staticmethod
    def _unit_id(question: str) -> str:
        matched = re.search(r"([1-9]\d*)\s*号机", question)
        return f"U{int(matched.group(1)):02d}" if matched else "U01"

    @staticmethod
    def _window_minutes(question: str) -> int:
        hour = re.search(r"(?:最近|过去|近)?\s*(\d+)\s*小时", question)
        if hour:
            return min(max(int(hour.group(1)) * 60, 30), 1440)
        minute = re.search(r"(?:最近|过去|近)?\s*(\d+)\s*分钟", question)
        if minute:
            return min(max(int(minute.group(1)), 30), 1440)
        if "今天" in question:
            return 720
        return 120

    def _tag_codes(self, question: str) -> list[str]:
        found: list[str] = []
        for alias, code in self._tag_aliases.items():
            if alias in question and code not in found:
                found.append(code)
        return found


class DeepSeekPlanner:
    """通过 OpenAI 兼容接口调用 DeepSeek 的结构化规划器。"""

    source = "deepseek"

    def __init__(self, config: Settings) -> None:
        if not config.llm_api_key:
            raise ValueError("POWERAGENT_LLM_API_KEY 未配置")
        self.config = config

    def plan(self, question: str, requested_unit_id: str | None = None) -> TaskPlan:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise PlanningError("未安装 openai 依赖") from exc

        client = OpenAI(api_key=self.config.llm_api_key, base_url=self.config.llm_base_url)
        system = """你是 PowerAgent 的任务规划器，只负责把电厂监盘问题转为 JSON。
允许 task_type 仅为 query、trend、alert、diagnose。仅允许 U01 机组和下列测点：
COND_ABS_PRESSURE、CW_FLOW、CW_PUMP_A_CURRENT、CW_PUMP_A_FREQ、AIR_EXTRACTOR_PRESSURE、UNIT_LOAD_MW。
window_minutes 必须为 30 到 1440 的整数。不要输出 SQL、控制命令、Markdown 或额外字段。
问题涉及故障原因、处置或建议时，选择 diagnose；告警/预警选择 alert；趋势/曲线/变化选择 trend。"""
        try:
            completion = client.chat.completions.create(
                model=self.config.llm_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"question": question, "requested_unit_id": requested_unit_id}, ensure_ascii=False
                        ),
                    },
                ],
            )
            content = completion.choices[0].message.content
            if not content:
                raise PlanningError("DeepSeek 未返回规划内容")
            plan = TaskPlan.model_validate_json(content)
        except PlanningError:
            raise
        except Exception as exc:
            raise PlanningError("DeepSeek 规划调用失败或返回不符合 TaskPlan 契约") from exc
        return plan


class ResilientPlanner:
    """DeepSeek 优先；无 Key、超时或契约错误时使用规则规划器。"""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.fallback = RuleBasedPlanner()
        self.primary: DeepSeekPlanner | None = DeepSeekPlanner(config) if config.llm_enabled else None

    def plan(self, question: str, requested_unit_id: str | None = None) -> tuple[TaskPlan, str]:
        if self.primary is not None:
            try:
                return self.primary.plan(question, requested_unit_id), self.primary.source
            except PlanningError:
                pass
        return self.fallback.plan(question, requested_unit_id), self.fallback.source
