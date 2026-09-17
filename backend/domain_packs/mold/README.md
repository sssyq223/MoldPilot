# Mold business pack

This directory is the replaceable mold ERP/business layer for MoldPilot. It
contains the mold system policy, all installed Skill documents, the registered
tool catalog/dispatch gateway, proposal-handler mappings, and the existing ERP
HTTP adapter. `manifest.py` owns MoldPilot's product metadata, conversation
title rules and mold-only HTTP route registration; the generic host no longer
imports those routers directly.

The generic runtime loads it with:

```text
AGENT_BUSINESS_PACK=mold
```

A vehicle or fixture product should provide the same component interface in a
separate sibling folder and select that folder at startup. It must not add its
business terms, ERP endpoints, tools, or card routing to `agent_core`.

Some mature MoldPilot implementations still live under `app` and are imported
by this pack through explicit adapters. Those are host/business modules, not
Agent Core. New work should move behind this pack boundary instead of adding
new industry branches to the harness.
