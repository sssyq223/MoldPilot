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
