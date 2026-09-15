"""Apply least-privilege table grants after migrations; never connects to ERP."""
import os
from pathlib import Path
import sys
from dotenv import dotenv_values
from sqlalchemy import create_engine,text

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.models import Base


def main():
    url=os.environ.get('MOLD_MIGRATION_URL') or dotenv_values('.env').get('MOLD_MIGRATION_URL')
    if not url:raise SystemExit('Explicit migration DSN required')
    engine=create_engine(url)
    if engine.url.database not in {'agent_db','agent_test'}:raise SystemExit('Refusing unrelated database')
    immutable={'audit_event','approval_action','payment_confirmation','supplier_shipment','goods_receipt','receipt_inspection','stock_movement','assembly_execution','trial_result','contact_record','contact_resolution','file_object','contact_attachment','agent_run_file'}
    with engine.begin() as connection:
        role=connection.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname='agent_app'")).first()
        if not role or role.rolsuper or role.rolbypassrls:raise SystemExit('agent_app must exist without superuser/BYPASSRLS')
        quote=engine.dialect.identifier_preparer.quote
        for table in Base.metadata.tables:
            connection.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {quote(table)} TO agent_app'))
            if table in immutable:connection.execute(text(f'REVOKE UPDATE,DELETE ON TABLE {quote(table)} FROM agent_app'))
        connection.execute(text('GRANT SELECT ON TABLE alembic_version TO agent_app'))
    print('Application table grants updated; immutable facts remain insert/select only.')


if __name__=='__main__':main()
