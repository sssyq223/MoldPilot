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
configuration, presentation metadata and several upstream mold services still
need to move. New mold slices should follow this direction until `app` contains
host ports and transport concerns rather than industry rules.

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
