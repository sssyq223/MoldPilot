# Mold business pack

This directory is the replaceable mold ERP/business layer for MoldPilot. It
contains the mold system policy, all installed Skill documents, the registered
tool catalog/dispatch gateway, proposal-handler mappings, and the existing ERP
HTTP adapter. `manifest.py` owns MoldPilot's product metadata, conversation
title rules and mold-only HTTP route registration; the generic host no longer
imports those routers directly.

The delivery-logistics application service is the first service relocated
behind this pack boundary. Its schemas, read model, validation, proposal
preparation and confirmed writes live in `delivery_logistics.py`; the similarly
named module under `app` is a compatibility facade only. This is a migration
step, not a fully independent vertical slice: logistics ORM, migrations,
configuration and several upstream mold services still need to move. New mold
slices should follow this direction until `app` contains host ports and
transport concerns rather than industry rules.

The relocated service consumes generic infrastructure only through the
validated `agent_core.host_ports` contract. Mold-wide project locator schemas
and matching rules live in `contracts.py`. Remaining calls into the legacy
plan/procurement/contact read services are deliberately isolated in
`legacy_read_ports.py`; that adapter is migration debt and must shrink as those
services move into the pack.

`harness_policy.py` owns every mold-specific ToolSearch example and search
term, the user-facing permission-mode instructions, and the Ollama structured
ReAct guidance. The reusable harness consumes those fields without knowing
project, contract, contact-case or mold vocabulary.

`manifest.py` also owns proposal-card presentation metadata: action-title
normalization, domain value labels, and receipt-to-workspace detail links. The
generic `ProposalCard` renders that validated contract and emits a generic
workspace target; it does not import the mold UI dictionary or assume every
proposal opens a contact-case panel.

`models.py` is the pack's ORM registry. Project, material and purchase models
live there; the larger typed business-fact and collaboration model sets live
in `domain_models.py` and `contact_models.py`, with contact/file relationships
in `attachment_models.py`. The host re-exports these mapped classes from
`app.models` while this pack is active so mature services remain compatible.
Selecting the template pack does not import these modules or register their
tables in the host metadata.

`authorization.py` owns the mold permission vocabulary and scope dimensions;
`resources.py` registers the resource types this pack can submit to the
generic approval envelope. The host persists approvals as
`resource_type/resource_id` and no longer has foreign keys or check constraints
that name mold tables. Both contracts are empty in the template pack.

`migrations.py` selects the existing `alembic.ini` history and
`alembic_version` table. This preserves all deployed MoldPilot databases while
the mixed historical chain is retired gradually; new migration commands go
through `scripts/migrate.py` so another active pack cannot accidentally run
the mold history.

The generic runtime loads it with:

```text
AGENT_BUSINESS_PACK=mold
```

A vehicle or fixture product should provide the same component interface in a
separate sibling folder and select that folder at startup. It must not add its
business terms, ERP endpoints, tools, or card routing to `agent_core`.

Some mature MoldPilot implementations still live under `app` and are imported
by this pack through explicit adapters. They are migration debt, not the target
architecture. New work should move behind this pack boundary instead of adding
new industry branches to the harness.
