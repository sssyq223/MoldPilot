from pathlib import Path


def test_local_change_migration_source_is_chain_head_and_declares_all_tables():
    root = Path(__file__).resolve().parents[1]
    path = root / "backend" / "domain_packs" / "mold" / "alembic_domain" / "versions" / "mb0d0e000017_local_change_intake.py"
    source = path.read_text(encoding="utf-8")
    assert 'revision = "mb0d0e000017"' in source
    assert 'down_revision = "mb0d0e000016"' in source
    for table in (
        "local_change_intake",
        "local_change_customer_mold_history",
        "local_change_association",
    ):
        assert f'"{table}"' in source
