# Mold business pack

This directory is the replaceable mold ERP layer installed on the generic
LLM + Harness + Tool + Skill workbench. The host loads only the stable files
at this package root; business implementations are categorized below them.

## Package layout

- `erp/core/`: shared mold business records, commands, matching and HTTP API.
- `erp/project/`: start, plans, pause/resume and project closure.
- `erp/design/`: design uploads, drawings, BOM and design progress services.
- `erp/procurement/`: purchasing, outsourcing and delivery logistics.
- `erp/manufacturing/`: manufacturing quality, assembly and trial context.
- `erp/commercial/`: bid intake, quotation and contract context.
- `erp/finance/`: receipts, supplier payments and settlement evidence.
- `erp/change/`: engineering contacts, change intake and attachments.
- `erp/governance/`: permission, audit and source-governance context.
- `ports/`: adapters to host identity, persistence, files, events, workflow
  engine and confirmation infrastructure.
- `tools/agent/<domain>/`: Agent-native executable tools by responsibility.
- `tools/erp/<domain>/`: ERP-backed executable tools and MCP adapters by
  business domain.
- `skills/agent/<domain>/`: Agent-native skills by business responsibility.
- `skills/erp/<domain>/`: ERP skills by business domain.
- `alembic/`: this pack's complete deployed PostgreSQL migration history.

Tool and Skill folders use the same `agent/erp -> domain` taxonomy. Skill
folders are runtime retrieval boundaries, not cosmetic grouping. The
pack exposes each Skill's `skill_layer`, `skill_domain`, and `route_terms`.
ToolSearch first narrows candidates to the matched directory (for example,
`erp/design`) and only then ranks Skills and tools inside that domain.

The package-root modules (`manifest`, `models`, `authorization`,
`harness_policy`, `tool_gateway`, `proposal_handlers`, `business`,
`workflow_policy`, `file_policy`, `notification_policy`, `erp_adapter`,
`workflow_assignment`, `resources`, and `migrations`) are the host-facing component contract. They
stay thin and assemble categorized implementations; business rules must not
move back into `app` or `agent_core`.

The browser presentation follows the same boundary under
`web/src/domain-packs/mold`. Select both halves together:

```text
AGENT_BUSINESS_PACK=mold
VITE_BUSINESS_PACK=mold
```

To build a vehicle, fixture, or other ERP workbench, copy the template pack,
implement the same root components, add categorized ERP/Agent Skills and UI
adapters, then change only these selectors. No Harness branch is needed.
