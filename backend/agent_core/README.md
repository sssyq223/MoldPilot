# Agent Core

This folder is the reusable runtime boundary. It owns only:

- model transports;
- the bounded multi-round harness and context compaction;
- the stable tool/skill gateway protocol;
- generic error/schema contracts and validated host-port loading;
- loading one configured business pack.

It must not contain mold, vehicle, fixture, ERP-document, approval-node, or
confirmation-card rules. Set `AGENT_BUSINESS_PACK` to an installed package
under `backend/domain_packs`. The selected pack must expose:

- `manifest.py`: product metadata, conversation-title policy and HTTP route installation;
- `harness_policy.py`: business vocabulary and system policy;
- `tool_gateway.py`: registered tools, skills, schemas, permissions and execution;
- `proposal_handlers.py`: tool-to-human-confirmation action handlers.
- `erp_adapter.py`: optional original-system integration behind the host facade.

`AGENT_HOST_PORTS_MODULE` independently selects the embedding host adapter
(default `app.host_ports`). A pack may use `agent_core.host_ports.host_ports()`
for database models, authorization, clock, configuration, audit hashing and
proposal-confirmation policy. It must not import those implementation modules
directly. Industry-to-industry dependencies belong in a named adapter inside
the pack until both sides have been migrated.

The host application may keep compatibility facades while business modules are
migrated, but new industry behavior belongs in a business pack, not here.
ToolSearch wording/examples, domain search terms, permission-mode instructions
and provider-specific ReAct guidance are also harness policy. Agent Core owns
their protocol shape only and must not embed one industry's examples.
