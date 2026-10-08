"""Import the ERP approval catalog into MoldPilot-owned BPM definitions.

This command is idempotent and only writes MoldPilot's workflow_definition
table.  It never reads or writes the ERP database and never calls an ERP
approval endpoint.  Imported definitions remain DRAFT until an administrator
maps the project roles and publishes them.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from domain_packs.mold import bpm  # noqa: E402
from domain_packs.mold.models import WorkflowDefinition  # noqa: E402
from domain_packs.mold.erp.core.erp_workflow_templates import templates  # noqa: E402


def migrate(*, dry_run=False):
    imported = []
    with SessionLocal.begin() as db:
        for item in templates():
            config = item["config"]
            bpm.validate(config)
            xml = bpm.compile_bpmn(config)
            current = db.scalar(select(WorkflowDefinition).where(
                WorkflowDefinition.process_key == item["process_key"],
                WorkflowDefinition.version == 1,
            ))
            if current is None:
                current = WorkflowDefinition(
                    process_key=item["process_key"], version=1, name=item["name"],
                    config=config, bpmn_xml=xml, status="DRAFT",
                    package_hash=bpm.content_hash({"config": config, "xml": xml}),
                )
                db.add(current)
                action = "created"
            else:
                current.name = item["name"]
                current.config = config
                current.bpmn_xml = xml
                current.package_hash = bpm.content_hash({"config": config, "xml": xml})
                if current.status != "PUBLISHED":
                    current.status = "DRAFT"
                action = "updated"
            imported.append({"process_key": item["process_key"], "action": action,
                             "status": current.status, "nodes": len(config["nodes"])})
        if dry_run:
            db.rollback()
    return imported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for item in migrate(dry_run=args.dry_run):
        print(f"{item['action']}: {item['process_key']} [{item['status']}] nodes={item['nodes']}")
