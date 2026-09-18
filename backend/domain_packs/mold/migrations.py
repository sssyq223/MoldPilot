"""Ordered generic-host and mold-domain migration repositories."""

STAGES = (
    {
        "name": "core",
        "config": "alembic-core.ini",
        "version_table": "alembic_core_version",
    },
    {
        "name": "mold",
        "config": "backend/domain_packs/mold/alembic-domain.ini",
        "version_table": "alembic_mold_version",
    },
)

# Existing deployments are upgraded through this frozen compatibility chain,
# checked for model drift, then stamped into the two authoritative stages.
LEGACY = {
    "config": "backend/domain_packs/mold/alembic.ini",
    "version_table": "alembic_version",
}
