import sys
sys.path.insert(0, 'E:/mold-agent-handoff-20260915-100906/mold-agent/backend')

from app.domain.agent_run import AgentRun
from app.infrastructure.database import SessionLocal
from sqlalchemy import select

db = SessionLocal()
runs = db.execute(
    select(AgentRun)
    .where(AgentRun.status == 'PENDING')
    .order_by(AgentRun.id.desc())
    .limit(3)
).scalars().all()

print(f'待处理任务数: {len(runs)}')
for r in runs:
    print(f'Run {r.id}: worker_scope={r.worker_scope}, status={r.status}')

db.close()
