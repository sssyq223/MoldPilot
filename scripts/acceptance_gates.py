"""Create or inspect the local FR-118 acceptance evidence file.

This script does not mark anything accepted by itself. It creates a template
that must be filled by the implementation/business owner with evidence
references before `query_operations_readiness_context` will treat a gate as
confirmed.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values


GATES = [
    ("deployment_topology", "部署方式与隔离边界", "确认 API、前端、数据库、Redis、对象存储、Worker 的部署边界与回滚方式，并在目标环境演练。"),
    ("user_scale", "用户规模", "登记目标用户数、并发峰值、部门范围和权限矩阵样本，并纳入压测。"),
    ("response_time", "响应时间", "分别测试只读工具、人工确认、附件上传、模型工具循环、前端首屏和错误恢复响应时间。"),
    ("availability", "可用性", "确认可用性口径、健康检查、进程守护、告警、故障切换和人工降级预案。"),
    ("backup_frequency", "备份频率", "确认全量/增量备份频率、保留周期、备份加密、备份介质和责任人。"),
    ("restore_objective", "恢复目标", "在隔离环境恢复数据库、附件对象、模型配置和迁移版本，记录 RTO/RPO。"),
    ("log_retention", "日志保留期限", "确认日志种类、脱敏规则、保留期限、检索方式和删除策略。"),
    ("production_storage", "生产附件存储", "验证版本保留、权限隔离、原件哈希校验、下载撤权、备份恢复和防病毒/OCR 边界。"),
    ("model_operations", "模型运行边界", "验证模型供应商、模型名、上下文窗口、超时、工具循环、强制压缩和失败回执。"),
]


def _configured_path(env_file: str) -> Path:
    value = dotenv_values(env_file).get("MOLD_ACCEPTANCE_EVIDENCE_FILE") or ".local/acceptance-gates.json"
    return Path(value)


def template(environment: str, confirmed_by: str = "") -> dict:
    return {
        "schema_version": 1,
        "environment": environment,
        "confirmed_by": confirmed_by,
        "confirmed_at": "",
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "instructions": "把某个 gate 的 confirmed 改为 true 前，必须填写 confirmed_by、confirmed_at 或顶层 confirmed_by/confirmed_at，并提供至少一条 evidence_refs。",
        "gates": {
            key: {
                "name": name,
                "confirmed": False,
                "required_test": required_test,
                "evidence_refs": [],
                "notes": "",
            }
            for key, name, required_test in GATES
        },
    }


def inspect(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False, "confirmed": [], "invalid_confirmed": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    gates = data.get("gates") if isinstance(data, dict) else {}
    confirmed: list[str] = []
    invalid: list[str] = []
    for key, _name, _required in GATES:
        item = gates.get(key) if isinstance(gates, dict) else None
        if not isinstance(item, dict) or not item.get("confirmed"):
            continue
        confirmed_by = item.get("confirmed_by") or data.get("confirmed_by")
        confirmed_at = item.get("confirmed_at") or data.get("confirmed_at")
        evidence_refs = item.get("evidence_refs")
        if confirmed_by and confirmed_at and isinstance(evidence_refs, list) and evidence_refs:
            confirmed.append(key)
        else:
            invalid.append(key)
    return {
        "path": str(path),
        "exists": True,
        "environment": data.get("environment") if isinstance(data, dict) else None,
        "confirmed": confirmed,
        "invalid_confirmed": invalid,
        "remaining": [key for key, _name, _required in GATES if key not in confirmed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or inspect a MoldPilot acceptance evidence file.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--path", default="", help="Override evidence file path. Defaults to MOLD_ACCEPTANCE_EVIDENCE_FILE.")
    parser.add_argument("--environment", default="local-delivery")
    parser.add_argument("--confirmed-by", default="")
    parser.add_argument("--write-template", action="store_true", help="Write a template if the target file does not exist.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing file when used with --write-template.")
    args = parser.parse_args()

    path = Path(args.path) if args.path else _configured_path(args.env_file)
    if args.write_template:
        if path.exists() and not args.force:
            raise SystemExit(f"Refusing to overwrite existing evidence file: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(template(args.environment, args.confirmed_by), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"written": str(path), **inspect(path)}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(inspect(path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
