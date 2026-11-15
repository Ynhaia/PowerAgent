"""离线合成实验：python -m scripts.evaluate_graduation。

标签仅用于计分；规则服务仅收到数据适配器。结果证明合成一致性，不代表现场准确率。
"""

import argparse
import csv
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import platform
from time import perf_counter

from backend.app.adapters.scenario_plant import SCENARIOS, ScenarioPlantDataAdapter
from backend.app.services.diagnosis_service import CondenserVacuumDiagnosisService


def run(seeds: int = 10) -> dict:
    rows = []
    durations = []
    for scenario in SCENARIOS:
        expected = "insufficient_data" if scenario in {"bad_quality", "sparse"} else scenario
        for seed in range(seeds):
            for window in (30, 60, 120):
                adapter = ScenarioPlantDataAdapter(scenario, seed)
                end = adapter.default_end_time()
                raw = adapter.get_telemetry("U01", CondenserVacuumDiagnosisService.CORE_TAGS, end - timedelta(minutes=window), end)
                pressure = raw["COND_ABS_PRESSURE"]
                # 基线只能表达“越限/不越限”；不能区分根因证据不足或不可用数据。
                baseline = "cw_flow_insufficient" if max(p.value for p in pressure) > 5.55 else "normal"
                # 消融：与当前规则相同的数值判据，但绕过质量/覆盖率门控。
                def slope(tag):
                    points = raw[tag]
                    elapsed = (points[-1].timestamp - points[0].timestamp).total_seconds() / 60
                    return (points[-1].value - points[0].value) / elapsed

                deviation = (min(p.value for p in raw["CW_FLOW"]) - 32900) / 32900 * 100
                score = 35 * (deviation <= -10) + 20 * (slope("CW_PUMP_A_CURRENT") < -0.12) + 15 * (slope("CW_PUMP_A_FREQ") < -0.008) + 15 * (slope("COND_ABS_PRESSURE") > 0.004)
                abnormal = max(p.value for p in pressure) > 5.55 or slope("COND_ABS_PRESSURE") > 0.004
                ablation = "normal" if not abnormal else "cw_flow_insufficient" if deviation <= -10 and score >= 60 else "inconclusive"
                start = perf_counter()
                result = CondenserVacuumDiagnosisService(adapter).diagnose("U01", window)
                durations.append((perf_counter() - start) * 1000)
                predicted = result.candidates[0].cause_code
                rows.append({"scenario": scenario, "seed": seed, "window_minutes": window, "expected": expected, "single_threshold": baseline, "without_quality_gate": ablation, "proposed": predicted})

    def metrics(column):
        labels = ["normal", "cw_flow_insufficient", "inconclusive", "insufficient_data"]
        matrix = {label: {prediction: 0 for prediction in labels} for label in labels}
        for row in rows:
            matrix[row["expected"]][row[column]] += 1
        scores = []
        for label in labels:
            tp = matrix[label][label]
            fp = sum(matrix[other][label] for other in labels if other != label)
            fn = sum(matrix[label][other] for other in labels if other != label)
            scores.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
        normal = [row for row in rows if row["expected"] == "normal"]
        bad = [row for row in rows if row["expected"] == "insufficient_data"]
        return {
            "decision_accuracy": sum(row[column] == row["expected"] for row in rows) / len(rows),
            "macro_f1": sum(scores) / len(scores),
            "normal_false_positive_rate": sum(row[column] != "normal" for row in normal) / len(normal),
            "unusable_data_rejection_rate": sum(row[column] == "insufficient_data" for row in bad) / len(bad),
            "confusion_matrix": matrix,
        }

    root = Path(__file__).resolve().parents[1]
    source_files = ["backend/app/adapters/scenario_plant.py", "backend/app/adapters/mock_plant.py", "backend/app/services/telemetry_service.py", "backend/app/services/diagnosis_service.py", "scripts/evaluate_graduation.py"]
    fingerprints = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in source_files}
    return {
        "data_kind": "synthetic_only", "generator_version": "COND-SYNTH-1.0", "rule_version": "COND-RULE-2.0",
        "sample_count": len(rows), "seed_range": [0, seeds - 1], "windows": [30, 60, 120],
        "python": platform.python_version(), "source_sha256": fingerprints,
        "limitations": "规则与生成器均由同一项目设计；同一轨迹窗口和种子扰动相关。不能估计真实电厂泛化准确率。未进行训练/测试随机划分，未进行大模型或 RAG 效果评测。",
        "metrics": {name: metrics(name) for name in ("single_threshold", "without_quality_gate", "proposed")},
        "local_rule_latency_ms": {"median": sorted(durations)[len(durations)//2], "p95": sorted(durations)[int(len(durations)*0.95)-1]},
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("docs/graduation/evaluation"))
    args = parser.parse_args()
    if not 1 <= args.seeds <= 100:
        parser.error("seeds 必须为 1 至 100")
    result = run(args.seeds)
    rows = result.pop("rows")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output / "predictions.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"sample_count": len(rows), "metrics": result["metrics"], "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
