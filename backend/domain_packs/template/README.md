# Business pack template

This pack is an executable, business-neutral starting point for a vehicle,
fixture, or another ERP workbench. It proves that the host application can
start without registering MoldPilot's projects, purchases, contacts, ERP
design upload, domain subjects, or mold confirmation-card routes.

Copy this directory to a new sibling package, change `PUBLIC_METADATA.id`, and
implement the stable components:

- `manifest.py`: product metadata, title policy and HTTP route installation;
- `harness_policy.py`: domain language and model contract;
- `tool_gateway.py`: Tool/Skill schemas, permissions and execution;
- `proposal_handlers.py`: trusted confirmation-card handlers;
- `erp_adapter.py`: optional original-system integration;
- `models.py`: pack-owned ORM exports; the template intentionally exports none;
- `authorization.py`: domain permissions and data-scope dimensions;
- `resources.py`: resource types accepted by generic approval persistence;
- `migrations.py`: Alembic repository and version-table ownership.
- `workflow_policy.py`: business types, rule language and workflow applicability;
- `file_policy.py`: visibility of files linked to domain objects;
- `notification_policy.py`: event titles and revocation-aware visibility.

The template intentionally registers no models, domain permissions, scope
dimensions, or approval resource types. Its metadata must remain sortable and
compilable as PostgreSQL DDL so a new pack starts from a valid host schema.
`scripts/migrate.py` selects `alembic-core.ini` for this pack; its frozen SQL
baseline creates only the host-owned tables plus `alembic_core_version` and has a
reviewed checksum. A real PostgreSQL integration test upgrades, checks and
downgrades that chain in an isolated schema.

Select the package with `AGENT_BUSINESS_PACK=<package_name>`. Industry code
must remain in that package and must not add branches to `agent_core`.
The browser build selects its matching presentation adapter with
`VITE_BUSINESS_PACK=<package_name>`; domain wording and panels live under
`web/src/domain-packs/<package_name>`.
