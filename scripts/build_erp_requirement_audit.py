"""Link all V1.1 requirements to source candidates without equating routes with delivery."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
SCOPE = {
    '业务对象与匹配': ['project', 'mold', 'partner'],
    '报价': ['project', 'mold', 'partner', 'contract'],
    '中标与承接': ['project', 'mold', 'partner', 'contract'],
    '内部开工': ['project', 'mold', 'contract', 'design_order'],
    '合同上传与管理': ['contract', 'common', 'project'],
    '项目大节点与计划': ['project_node', 'production_schedule', 'project'],
    '设计与成果协同': ['design_order', 'design_upload', 'design_drawing_version', 'bom', 'design_change'],
    '采购与价格': ['material', 'material_supplier', 'purchase_request', 'purchase_request_detail', 'purchase_workbench', 'purchase_decision', 'purchase_order', 'purchase_supplier_delivery', 'purchase_supplier_return', 'material_inbound'],
    '整套委外合同与付款条件': ['contract', 'outsource_order', 'purchase_reconcile_statement'],
    '制造与质检': ['work_order', 'work_report', 'quality_inspection', 'material_inbound', 'outsource_order', 'outsource_batch'],
    '装配与试模': ['assembly', 'trial_mold'],
    '交付与物流': ['trial_mold', 'receive_site', 'warehouse', 'material_inbound', 'purchase_supplier_delivery'],
    '整套委外协同': ['outsource_order', 'outsource_batch', 'entrust:*'],
    '设变承接': ['design_change', 'project', 'mold'],
    '工程联络单与异常闭环': ['design_change', 'production_exception', 'repair_order', 'quality_inspection', 'entrust:exception'],
    '暂停与恢复': ['project', 'project_node', 'production_schedule'],
    '终止结算与正常关闭': ['project', 'contract', 'purchase_reconcile_statement'],
    '财务节点与核对': ['contract', 'purchase_reconcile_statement', 'additional_processing_fee'],
    'Agent 查询与被动预警': ['project', 'purchase_workbench', 'purchase_order', 'bom'],
    '权限与审计': ['user', 'dept', 'role', 'log'],
    '通知与附件': ['message', 'common', 'design_drawing_version', 'contract'],
    '来源与运行交付': [],
}

NOT_EQUIVALENT = {
    '报价': '只有项目/客户/合同基础候选，不能用供应商报价替代客户报价；完整业务在 Agent 开发。',
    '中标与承接': '采购定标不是客户中标；客户分类、中标匹配、人工承接、拒单和草稿延续在 Agent 开发。',
    '合同上传与管理': '合同 CRUD 或通用上传不满足会话上传→OCR 草稿→人工核对→BPM→合同归档；整条业务在 Agent 开发。',
    '项目大节点与计划': 'ERP 节点表及工序排程不能代替 Agent 项目大节点制定、部门协同和计划版本审批。',
    '工程联络单与异常闭环': 'ERP 异常和设变流程只作业务参考，不调用；工程联络单完整业务在 Agent 开发。',
    '权限与审计': '仅参考 ERP 原用户/权限接口约束；Agent 账号、Grant、工具/Skill、字段与查询权限独立开发。',
    '来源与运行交付': '接口目录不能证明部署、备份、恢复、可用性或完整数据来源规则；Agent 独立交付。',
}


def selected(endpoint, selector):
    module = 'module_entrust' if selector.startswith('entrust:') else 'module_admin'
    stem = selector.split(':', 1)[-1]
    return f'/{module}/controller/' in endpoint['file'] and (stem == '*' or Path(endpoint['file']).stem == stem+'_controller')


def disposition(endpoint):
    file, route, func = endpoint['file'], endpoint['route'], endpoint['function']
    if any(x in file for x in ['workflow_controller', 'exception_controller', 'design_change_controller']):
        return 'REFERENCE_ONLY_NO_FLOW_CALL'
    if re.search(r'approval|approve|reject|myTodo|/review|/agent|/chat|supplier_portal', route+' '+func, re.I):
        return 'REFERENCE_ONLY_NOT_REGISTERED'
    return 'QUERY_CANDIDATE_NOT_VERIFIED' if endpoint['method'] == 'GET' else 'ACTION_CANDIDATE_NOT_VERIFIED'


def main():
    coverage = json.loads((DOCS/'requirements/coverage.json').read_text(encoding='utf-8'))
    inventory_path = DOCS/'erp-audit/erp-endpoints.json'
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    source = Path(inventory['source'])
    endpoints = inventory['endpoints']
    groups, evidence, checked = {}, {}, set()
    for module, selectors in SCOPE.items():
        candidates = [e for e in endpoints if any(selected(e, s) for s in selectors)]
        refs = []
        for e in candidates:
            if e['file'] not in checked:
                raw = (source/e['file']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != e['sha256']:
                    raise ValueError('ERP source changed; refresh inventory: '+e['file'])
                checked.add(e['file'])
            ref = hashlib.sha256((e['method']+' '+e['route']+' '+e['file']).encode()).hexdigest()[:16]
            evidence[ref] = {**e, 'disposition': disposition(e)}
            refs.append(ref)
        groups[module] = {'candidate_ids': refs,
                          'gap': NOT_EQUIVALENT.get(module, '逐条需求在 Agent 实现协同及缺失能力；仅复用实际符合权限、版本、人工确认与副作用边界的 ERP 具体动作。')}
    decisions = []
    for row in coverage['requirements']:
        group = groups[row['module']]
        decisions.append({'requirement_id': row['id'], 'module': row['module'],
                          'source_text': row['source_text'], 'agent_work': row['agent_responsibility'],
                          'latest_decision': row['latest_decision'], 'erp_candidate_ids': group['candidate_ids'],
                          'gap': group['gap'], 'scope_review': 'MAPPED_WITH_EXPLICIT_BOUNDARY',
                          'runtime_acceptance': 'NOT_VERIFIED'})
    assert {d['requirement_id'] for d in decisions} == {f'FR-{n:03}' for n in range(1,119)}
    result = {'scope': 'Static requirement ownership and route evidence mapping; not runtime acceptance or an executable allowlist.',
              'inventory_sha256': hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
              'groups': groups, 'requirements': decisions, 'erp_evidence': evidence}
    (DOCS/'erp-audit/requirement-map.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['# V1.1 与 ERP 源码逐条核对', '',
             '核对基线：V1.1 原文、用户最新约定及提供的 ERP 源码。118 条需求全部保留，业务开发归属已逐条登记。此表用于静态范围决策；接口调用、业务数据和副作用尚需隔离环境联调，不能据此宣布接入完成。', '',
             '每条需求关联的是相关源码候选，不能把同一组的全部接口理解成每条需求都需要调用，也不能把 GET 理解为已经证明无副作用。审批、异常和旧 Agent 入口不注册为复用能力。', '',
             '关键动作的人工源码核对与接口差异见 ERP_ACTION_REVIEW.md；完整原文与验收跟踪见 REQUIREMENTS_TRACEABILITY.md。', '']
    for module, group in groups.items():
        subset = [d for d in decisions if d['module'] == module]
        lines += [f'## {module}', '', group['gap'], '', '| 需求 | Agent 工作与归属 | 适用源码范围 | 验收 |', '|---|---|---|---|']
        file_names = sorted({Path(evidence[k]['file']).name for k in group['candidate_ids']})
        for row in subset:
            lines.append(f"| {row['requirement_id']} | {row['agent_work']} | 本节下列候选；须按具体动作核对 | 未联调验收 |")
        lines += ['', '相关源码文件：'+('、'.join(file_names) if file_names else '不以 ERP 业务接口替代本项开发'), '',
                  '| 方法与接口 | 文件与行号 | Service 调用 | 归类 |', '|---|---|---|---|']
        for ref in group['candidate_ids']:
            item = evidence[ref]
            label = Path(item['file']).name+':'+str(item['line'])
            link = (source/item['file']).as_posix()+':'+str(item['line'])
            lines.append(f"| {item['method']} `{item['route']}` | [{label}](<{link}>) | {', '.join(item['service_calls']) or '须读函数体核对'} | {item['disposition']} |")
        lines += ['']
    (DOCS/'ERP_REQUIREMENT_REVIEW.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f"Mapped {len(decisions)} requirements, {len(evidence)} candidate routes; verified hashes of {len(checked)} ERP files. Runtime acceptance remains unverified.")


if __name__ == '__main__': main()
