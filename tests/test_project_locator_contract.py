from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
from domain_packs.mold.tools.erp.commercial.contract_tools import ContractContextInput
from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput
from domain_packs.mold.tools.erp.design.design_tools import DesignRouteContextInput
from domain_packs.mold.tools.erp.governance.governance_context_tools import GovernanceContextInput
from domain_packs.mold.tools.erp.procurement.procurement_tools import ProcurementPriceContextInput
from domain_packs.mold.tools.erp.project.completion_lifecycle_tools import ProjectCompletionContextInput
from domain_packs.mold.tools.erp.project.execution_lifecycle_tools import ProjectExecutionContextInput
from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import ProjectKickoffContextInput
from domain_packs.mold.tools.erp.project.lifecycle_overview_tools import ProjectLifecycleContextInput
from domain_packs.mold.tools.erp.project.start_tools import StartReadinessInput
from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput
from domain_packs.mold.tools.erp.project.project_control_tools import ProjectContextInput
from domain_packs.mold.tools.erp.project.project_closure_tools import ClosureContextInput, execute_tool
from agent_core.errors import DomainError


@pytest.mark.parametrize('model', [
    ProjectPlanContextInput, ContractContextInput, QuoteContextInput, DesignRouteContextInput,
    GovernanceContextInput, ProcurementPriceContextInput, ProjectCompletionContextInput,
    ProjectExecutionContextInput, ProjectKickoffContextInput, ProjectLifecycleContextInput, StartReadinessInput,
    ProjectDossierInput, ProjectContextInput,
])
def test_project_code_is_not_silently_queried_as_internal_id(model):
    with pytest.raises(ValidationError, match='identifier'):
        model(project_id='CUSTOMER-PROJECT-001')
    assert model(identifier='CUSTOMER-PROJECT-001').identifier == 'CUSTOMER-PROJECT-001'
    identity = str(uuid4())
    assert model(project_id=identity).project_id == identity
    assert model(project_id=identity.upper()).project_id == identity
    schema = model.model_json_schema()['properties']['project_id']
    assert schema['anyOf'][0]['pattern'].startswith('^')
    assert 'identifier' in schema['anyOf'][0]['description']


def test_id_only_closure_query_rejects_business_code_before_database_access():
    with pytest.raises(DomainError) as caught:
        execute_tool(None, None, 'query_project_closure_context', {'project_id': 'CUSTOMER-PROJECT-001'})
    assert caught.value.code == 'INVALID_TOOL_INPUT'
    assert 'query_projects' in str(caught.value)
    schema = ClosureContextInput.model_json_schema()['properties']['project_id']
    assert schema['pattern'].startswith('^')
    assert 'identifier' not in schema['description']
    identity = str(uuid4())
    assert ClosureContextInput(project_id=identity.upper()).project_id == identity
