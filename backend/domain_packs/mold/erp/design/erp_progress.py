"""Read-only ERP progress references for Agent plan context.

This module never mirrors ERP work orders into local Agent tables. It only asks
registered ERP read endpoints for the current user's ERP identity and returns a
bounded DTO with native references and an as-of timestamp.
"""
from sqlalchemy import select
from domain_packs.mold import models as m
from domain_packs.mold.config import settings
from domain_packs.mold.erp_adapter import ERPClient, decrypt
from domain_packs.mold.ports.errors import DomainError


def project_mold_numbers(db, project_id):
    rows = db.execute(select(m.Mold.internal_number).join(
        m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id).where(
        m.ProjectMold.project_id == project_id).order_by(m.Mold.internal_number).limit(20))
    return [row[0] for row in rows if row[0]]


def mapped_erp_project_code(db, project):
    """Use the human-confirmed ERP project code when one exists.

    The Agent project code is not an ERP identifier.  Keeping the lookup here
    read-only makes every downstream ERP progress query honor the explicit
    mapping without guessing from similar project numbers.
    """
    scalar = getattr(db, 'scalar', None)
    if not callable(scalar):
        return None
    mapping = scalar(select(m.ProjectERPMapping).where(
        m.ProjectERPMapping.project_id == project.id,
        m.ProjectERPMapping.status == 'CONFIRMED',
    ))
    return str(mapping.erp_project_code).strip() if mapping and mapping.erp_project_code else None


def query_project_progress(db, user, project, client_factory=None):
    erp_project_code = mapped_erp_project_code(db, project)
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'erp_project_code': erp_project_code,
        'mold_numbers': project_mold_numbers(db, project.id),
        'mold_reference_source': 'AGENT_PROJECT_MOLD' if project_mold_numbers(db, project.id) else None,
        'erp_mold_candidates': [],
        'records': None,
        'limitations': [],
    }
    if not settings().erp_base_url:
        result['limitations'].append('ERP 服务地址未配置，未读取原系统项目节点或生产进度。')
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result['status'] = 'LOGIN_REQUIRED'
        result['limitations'].append('当前用户尚未绑定或验证 ERP 身份，未读取原系统进度。')
        return result
    mold_numbers = result['mold_numbers']
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            lookup_project_code = erp_project_code or project.code
            if mold_numbers and not erp_project_code and hasattr(client, 'business_molds'):
                # A local Agent mold number is not proof of an ERP mold.  Do
                # not query execution by the Agent code and local number until
                # a human confirms the ERP project mapping; expose the
                # bounded candidate feed instead so the handoff can proceed.
                candidate_context = client.business_molds()
                result['erp_mold_candidates'] = (candidate_context.get('records') or [])[:20]
                result['status'] = 'ERP_PROJECT_MAPPING_REQUIRED'
                result['limitations'].append(
                    'Agent 项目已有本地模具号，但尚未确认 ERP 项目映射；仅返回 ERP 候选，不读取未绑定的执行节点。'
                )
                return result
            if not mold_numbers and hasattr(client, 'business_molds'):
                candidate_context = client.business_molds(project_no=lookup_project_code)
                candidates = [
                    row for row in (candidate_context.get('records') or [])
                    if str(row.get('project_code') or '').strip() == str(lookup_project_code).strip()
                    and str(row.get('mold_code') or '').strip()
                ]
                result['erp_mold_candidates'] = candidates[:20]
                if len(candidates) == 1:
                    # This is read-only planning evidence from an exact ERP
                    # project match. It remains a pending handoff until a
                    # human confirms the project_mold relation.
                    mold_numbers = [str(candidates[0]['mold_code']).strip()]
                    result['mold_numbers'] = mold_numbers
                    result['mold_reference_source'] = 'ERP_EXACT_CANDIDATE'
                    result['handoff_required'] = True
                elif len(candidates) > 1:
                    result['status'] = 'MULTIPLE_ERP_MOLD_CANDIDATES'
                    result['limitations'].append(
                        'ERP 返回多个同项目模具候选，未读取执行节点；须先人工确认唯一模具号。'
                    )
                    return result
            if not mold_numbers:
                result['status'] = 'NO_MOLD_REFERENCE'
                result['limitations'].append(
                    '当前项目未维护可用于 ERP 查询的模具号，且 ERP 未返回唯一项目模具候选，仅保留 Agent 本地计划事实。'
                )
                return result
            records = [client.plan_execution_progress(mold_no=mold_no, project_no=lookup_project_code) for mold_no in mold_numbers[:5]]
        finally:
            close = getattr(client, 'close', None)
            if close: close()
    except DomainError as error:
        result['status'] = error.code
        result['limitations'].append(error.message)
        return result
    result['status'] = 'RESOLVED'
    result['records'] = records
    result['limitations'].append('ERP 进度为原系统只读事实引用；Agent 不登记开完工、不修改 ERP 工单、不据此自动重排计划。')
    if result.get('handoff_required'):
        result['limitations'].append(
            '本次节点来自 ERP 精确项目候选，尚未建立 Agent project_mold；节点可用于核对基线计划，不代表本地交接或计划已生效。'
        )
    if len(mold_numbers) > 5:
        result['limitations'].append('ERP 进度本次最多读取前 5 个模具号，更多模具需按项目档案拆分核对。')
    return result


def query_project_outsource_execution(db, user, project, client_factory=None):
    """Read ERP委外工单/生产/履约/异常事实 for the project; never mirror or mutate ERP."""
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'mold_numbers': project_mold_numbers(db, project.id),
        'records': None,
        'limitations': [],
    }
    if not settings().erp_base_url:
        result['limitations'].append('ERP 服务地址未配置，未读取原系统委外执行事实。')
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result['status'] = 'LOGIN_REQUIRED'
        result['limitations'].append('当前用户尚未绑定或验证 ERP 身份，未读取原系统委外执行事实。')
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            context = client.outsource_execution_context(
                mold_no=(result['mold_numbers'][0] if result['mold_numbers'] else None),
                project_no=project.code,
            )
            fulfillment_records = context.get('fulfillment_records') or []
            order_nos = [
                value
                for row in fulfillment_records
                for value in (
                    row.get('order_no'),
                    row.get('orderNo'),
                    row.get('production_order_no'),
                    row.get('productionOrderNo'),
                )
                if str(value or '').strip()
            ]
            quality = client.quality_inspection_context(
                mold_no=(result['mold_numbers'][0] if result['mold_numbers'] else None),
                project_no=project.code,
                order_nos=order_nos,
            )
            result['records'] = {
                **context,
                'quality_inspection_records': quality.get('inspection_records') or [],
                'quality_totals': quality.get('totals') or {},
                'quality_status': quality.get('status'),
                'totals': {
                    **(context.get('totals') or {}),
                    'quality_inspections': (quality.get('totals') or {}).get('inspections', 0),
                },
                'limitations': (context.get('limitations') or []) + (quality.get('limitations') or []),
            }
        finally:
            close = getattr(client, 'close', None)
            if close:
                close()
    except DomainError as error:
        result['status'] = error.code
        result['limitations'].append(error.message)
        return result
    result['status'] = 'RESOLVED'
    result['limitations'].append('ERP 委外执行为原系统只读事实引用；Agent 不登记开完工、不修改 ERP 阶段、不自动处理异常。')
    return result


def query_project_delivery_execution(db, user, project, client_factory=None):
    """Read ERP fulfillment facts for delivery coordination without mirroring them."""
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'mold_numbers': project_mold_numbers(db, project.id),
        'records': None,
        'limitations': [],
    }
    if not settings().erp_base_url:
        result['limitations'].append('ERP 服务地址未配置，未读取原系统交付履约事实。')
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result['status'] = 'LOGIN_REQUIRED'
        result['limitations'].append('当前用户尚未绑定或验证 ERP 身份，未读取原系统交付履约事实。')
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            context = client.outsource_execution_context(
                mold_no=(result['mold_numbers'][0] if result['mold_numbers'] else None),
                project_no=project.code,
            )
            fulfillment_records = context.get('fulfillment_records') or []
            order_nos = [
                value
                for row in fulfillment_records
                for value in (
                    row.get('order_no'),
                    row.get('orderNo'),
                    row.get('production_order_no'),
                    row.get('productionOrderNo'),
                )
                if str(value or '').strip()
            ]
            quality = client.quality_inspection_context(
                mold_no=(result['mold_numbers'][0] if result['mold_numbers'] else None),
                project_no=project.code,
                order_nos=order_nos,
            )
            result['records'] = {
                'fulfillment_records': fulfillment_records,
                'product_shipment_records': context.get('product_shipment_records') or [],
                'exception_records': context.get('exception_records') or [],
                'quality_inspection_records': quality.get('inspection_records') or [],
                'quality_totals': quality.get('totals') or {},
                'quality_status': quality.get('status'),
                'totals': {
                    'fulfillment_orders': (context.get('totals') or {}).get('fulfillment_orders', 0),
                    'product_shipments': (context.get('totals') or {}).get('product_shipments', 0),
                    'exceptions': (context.get('totals') or {}).get('exceptions', 0),
                    'quality_inspections': (quality.get('totals') or {}).get('inspections', 0),
                },
                'as_of': context.get('as_of'),
                'source_system': context.get('source_system', 'ERP'),
                'limitations': (context.get('limitations') or []) + (quality.get('limitations') or []),
            }
        finally:
            close = getattr(client, 'close', None)
            if close:
                close()
    except DomainError as error:
        result['status'] = error.code
        result['limitations'].append(error.message)
        return result
    result['status'] = 'RESOLVED'
    result['limitations'].append('ERP 交付履约和质检为原系统只读事实引用；Agent 不确认发货、签收、客户验收或项目关闭，也不修改 ERP 质检结论。')
    return result


def query_project_finance_execution(db, user, project, contract_numbers=(), client_factory=None):
    """Read ERP contract payment facts matched by Agent contract numbers."""
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'contract_numbers': [str(value) for value in (contract_numbers or ()) if str(value).strip()],
        'records': None,
        'limitations': [],
    }
    if not settings().erp_base_url:
        result['limitations'].append('ERP 服务地址未配置，未读取原系统合同付款计划或付款记录。')
        return result
    if not result['contract_numbers']:
        result['status'] = 'NO_CONTRACT_REFERENCE'
        result['limitations'].append('当前项目未维护可用于 ERP 查询的合同号，仅保留 Agent 本地财务事实。')
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result['status'] = 'LOGIN_REQUIRED'
        result['limitations'].append('当前用户尚未绑定或验证 ERP 身份，未读取原系统合同付款资料。')
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            result['records'] = client.contract_finance_context(result['contract_numbers'])
        finally:
            close = getattr(client, 'close', None)
            if close:
                close()
    except DomainError as error:
        result['status'] = error.code
        result['limitations'].append(error.message)
        return result
    result['status'] = 'RESOLVED'
    result['limitations'].append('ERP 合同付款资料为原系统只读事实引用；Agent 不把付款记录改写为客户回款或供应商实付，也不自动更新合同余额。')
    return result


def query_project_procurement_execution(db, user, project, client_factory=None):
    """Read ERP procurement and warehouse execution facts without mirroring."""
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'mold_numbers': project_mold_numbers(db, project.id),
        'records': None,
        'limitations': [],
    }
    if not settings().erp_base_url:
        result['limitations'].append('ERP 服务地址未配置，未读取原系统采购、发货、入库或库存流水。')
        return result
    if not result['mold_numbers'] and not project.code:
        result['status'] = 'NO_PROJECT_REFERENCE'
        result['limitations'].append('当前项目未维护可用于 ERP 查询的项目号或模具号，仅保留 Agent 本地采购事实。')
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result['status'] = 'LOGIN_REQUIRED'
        result['limitations'].append('当前用户尚未绑定或验证 ERP 身份，未读取原系统采购执行资料。')
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            result['records'] = client.procurement_execution_context(
                mold_no=(result['mold_numbers'][0] if result['mold_numbers'] else None),
                project_no=project.code,
            )
        finally:
            close = getattr(client, 'close', None)
            if close:
                close()
    except DomainError as error:
        result['status'] = error.code
        result['limitations'].append(error.message)
        return result
    result['status'] = 'RESOLVED'
    result['limitations'].append('ERP 采购执行为原系统只读事实引用；Agent 不把 ERP 订单、入库或库存流水镜像成本地业务对象。')
    return result


def query_project_manufacturing_execution(db, user, project, client_factory=None):
    """Read ERP manufacturing orders and work reports for the project without mirroring."""
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'mold_numbers': project_mold_numbers(db, project.id),
        'records': None,
        'limitations': [],
    }
    if not settings().erp_base_url:
        result['limitations'].append('ERP 服务地址未配置，未读取原系统制造工单或现场报工。')
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result['status'] = 'LOGIN_REQUIRED'
        result['limitations'].append('当前用户尚未绑定或验证 ERP 身份，未读取原系统制造工单或现场报工。')
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            result['records'] = client.manufacturing_execution_context(
                mold_no=(result['mold_numbers'][0] if result['mold_numbers'] else None),
                project_no=project.code,
            )
        finally:
            close = getattr(client, 'close', None)
            if close:
                close()
    except DomainError as error:
        result['status'] = error.code
        result['limitations'].append(error.message)
        return result
    result['status'] = 'RESOLVED'
    result['limitations'].append('ERP 制造执行为原系统只读事实引用；Agent 不登记开完工、不修改 ERP 工单、不据此自动重排计划。')
    return result
