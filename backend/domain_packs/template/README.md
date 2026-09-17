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
- `erp_adapter.py`: optional original-system integration.

Select the package with `AGENT_BUSINESS_PACK=<package_name>`. Industry code
must remain in that package and must not add branches to `agent_core`.
