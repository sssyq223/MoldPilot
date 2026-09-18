"""Business-neutral host migration repository used by a new domain pack."""

STAGES = ({
    "name": "core",
    "config": "alembic-core.ini",
    "version_table": "alembic_core_version",
},)
