"""生成 PowerAgent U01 模拟诊断报告，供本地演示和文档渲染校验使用。"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.app.adapters.mock_plant import MockPlantDataAdapter
from backend.app.services.knowledge_service import KnowledgeService
from backend.app.services.report_service import CondenserVacuumReportService


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 U01 凝汽器真空下降模拟诊断报告")
    parser.add_argument("--output", type=Path, default=Path("reports/PowerAgent_U01_condenser_vacuum_demo.docx"))
    args = parser.parse_args()
    service = CondenserVacuumReportService(MockPlantDataAdapter(), KnowledgeService.default())
    report = service.generate("U01", 120)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(report.content)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
