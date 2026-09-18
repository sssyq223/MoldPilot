"""Read-only ERP progress references for Agent plan context.

This module never mirrors ERP work orders into local Agent tables. It only asks
registered ERP read endpoints for the current user's ERP identity and returns a
bounded DTO with native references and an as-of timestamp.
"""
from sqlalchemy import select
from domain_packs.mold import models as m
from domain_packs.mold.ports.config import settings
from domain_packs.mold.erp_adapter import ERPClient, decrypt
from domain_packs.mold.ports.errors import DomainError


def project_mold_numbers(db, project_id):
    rows = db.execute(select(m.Mold.internal_number).join(
        m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id).where(
        m.ProjectMold.project_id == project_id).order_by(m.Mold.internal_number).limit(20))
    return [row[0] for row in rows if row[0]]


def query_project_progress(db, user, project, client_factory=None):
    result = {
        'status': 'NOT_CONFIGURED',
        'source': 'erp',
        'project_code': project.code,
        'mold_numbers': project_mold_numbers(db, project.id),
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
    if not mold_numbers:
        result['status'] = 'NO_MOLD_REFERENCE'
        result['limitations'].append('当前项目未维护可用于 ERP 查询的模具号，仅保留 Agent 本地计划事实。')
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            records = [client.plan_execution_progress(mold_no=mold_no, project_no=project.code) for mold_no in mold_numbers[:5]]
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
    if len(mold_numbers) > 5:
        result['limitations'].append('ERP 进度本次最多读取前 5 个模具号，更多模具需按项目档案拆分核对。')
    return result
