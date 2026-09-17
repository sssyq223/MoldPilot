# Agent Core

This folder is the reusable runtime boundary. It owns only:

- model transports;
- the bounded multi-round harness and context compaction;
- the stable tool/skill gateway protocol;
- loading one configured business pack.

It must not contain mold, vehicle, fixture, ERP-document, approval-node, or
confirmation-card rules. Set `AGENT_BUSINESS_PACK` to an installed package
under `backend/domain_packs`. The selected pack must expose:

- `harness_policy.py`: business vocabulary and system policy;
- `tool_gateway.py`: registered tools, skills, schemas, permissions and execution;
- `proposal_handlers.py`: tool-to-human-confirmation action handlers.

The host application may keep compatibility facades while business modules are
migrated, but new industry behavior belongs in a business pack, not here.
