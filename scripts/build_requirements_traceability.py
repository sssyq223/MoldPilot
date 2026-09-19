"""Preserve every V1.1 requirement; source coverage is not implementation acceptance."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/erp-audit/requirements-v1.1.txt'
OUT = ROOT / 'docs/requirements/coverage.json'

# Each range describes Agent work, even where a specific execution capability is reused.
MODULES = [
    (1, 5, '业务对象与匹配', 'Agent 开发关联、候选确认、防重和历史追溯；引用 ERP 已有实体，不复制实时台账'),
    (6, 12, '报价', 'Agent 开发资料接收、成本/工艺/工期评估、加工方式、报价版本、提交反馈及历史查询'),
    (13, 19, '中标与承接', 'Agent 开发客户分类、中标接收、匹配、人工承接/拒单及同一开工草稿延续'),
    (20, 25, '内部开工', 'Agent 开发开工依据、正式下达、业务状态、合同催补及财务交接；调用已有执行能力前校验开工条件'),
    (26, 32, '合同上传与管理', 'Agent 开发上传、版本、审核、业务关联、晚到差异及替代追加；历史 ERP 合同/收付款事实引用，禁止重复累计'),
    (33, 42, '项目大节点与计划', 'Agent 开发大节点维护、部门确认、审批、依赖、日期、计划版本、影响调整及看板；引用 ERP 实际执行记录'),
    (43, 47, '设计与成果协同', 'Agent 开发设计审批、资料协同及工程联络单关联；设计上传/BOM 等已登记业务能力经核对调用 ERP'),
    (48, 54, '采购与价格', 'Agent 开发辅材、办公用品、试模料新增需求及全部适用审批；原材/五金/委外和已有下单/拆单动作调用 ERP'),
    (55, 57, '整套委外合同与付款条件', 'Agent 开发合同草稿/审批、付款条件与防重申请；引用既有合同和执行事实，禁止以审批代替支付'),
    (58, 61, '制造与质检', '已有制造/检验/收货业务经审查调用 ERP；Agent 开发协同、审批、异常工程联络单及闭环验证'),
    (62, 63, '装配与试模', '实际装配/试模能力复用 ERP；Agent 开发申请审批、业务前置、资源协同及异常处理，仅设钳工主管'),
    (64, 69, '交付与物流', 'Agent 开发发货车辆、物流信息维护、物流报价审批及验收协同；既有出入库/发货执行经审查复用 ERP'),
    (70, 77, '整套委外协同', 'Agent 开发加工方式控制、节点协同、审批、异常及结算衔接；已有委外业务执行复用 ERP，不调用旧异常/审批流程'),
    (78, 81, '设变承接', 'Agent 开发设变分类、依据、报价与承接、原对象关联和版本；复用已有设计/制造业务动作'),
    (82, 90, '工程联络单与异常闭环', 'Agent 完整开发问题、方案、影响、BPM 审批、整改、复验及关闭；执行动作按能力目录调用，禁止旧 ERP 异常流程'),
    (91, 93, '暂停与恢复', 'Agent 开发依据、受影响动作限制、区间与顺延、防重复及客户交期独立确认；既有对象状态衔接须核对接口'),
    (94, 98, '终止结算与正常关闭', 'Agent 开发不同关闭清单、处置协同、人工确认、归档及历史更正；引用 ERP 已有执行/财务事实'),
    (99, 109, '财务节点与核对', 'Agent 开发财务需求缺失能力、合同节点、审批、实际确认、核对及汇总；已有 ERP 财务事实不另建同义总账'),
    (110, 112, 'Agent 查询与被动预警', 'Agent/Harness/LLM/Tool/Skills 新开发；只在提问时分析，查询先按对象和权限路由，不镜像 ERP 数据'),
    (113, 114, '权限与审计', 'Agent 开发管理员灵活授权、范围/字段/工具/Skill 隔离及全过程审计；正式操作人工确认'),
    (115, 116, '通知与附件', 'Agent 开发通知、待办及附件版本与权限；业务提醒与主动 AI 预警分别管理'),
    (117, 118, '来源与运行交付', 'Agent 开发明确来源的受控调用、失败核对、Docker 部署、备份恢复及实施验收；不转发投影或等待 ERP 开发'),
]

OVERRIDES = {
    'FR-015': '客户平台自动连接已由用户取消；保留人工接收、维护、确认及依据。',
    'FR-071': '不开发供应商门户；保留授权人员录入/导入上报证据和采购、项目协同。',
    'FR-073': '在线电子签署已由用户取消；保留模板、人工审核签订及签署文件上传。',
    'FR-104': '不开发银行自动转账；保留付款流程、人工实际支付确认及凭证。',
    'FR-110': 'AI 预警仅用户提问时执行，依据已上报异常或临期未发货；严格限定用户责任域。',
    'FR-117': '按权威来源直接查询/调用，不采用 ERP 镜像、CDC、投影、先本地后 ERP 的查找策略。',
}

CROSS_PHASE_IMPLEMENTATION = {
    key: (
        'query_project_execution_context 从基线计划继续投影设计/BOM、采购或整套委外、制造质检、'
        '装配试模、交付签收和客户验收；阶段权限独立，后续事实不覆盖前序缺口，不新增 ERP 页面或复制执行数据'
    )
    for key in ('FR-033', 'FR-043', 'FR-048', 'FR-058', 'FR-062', 'FR-064', 'FR-070')
}
CROSS_PHASE_VERIFICATION = {
    key: (
        'tests/test_project_execution_lifecycle.py 覆盖协调器注册、空项目当前焦点、整套委外分支、'
        '内部制造/装配不重复执行、交付独立保留、阶段权限不泄漏及多项目候选不合并'
    )
    for key in CROSS_PHASE_IMPLEMENTATION
}
COMPLETION_IMPLEMENTATION = {
    key: (
        'query_project_completion_context 把交付与客户验收、发票与客户回款、供应商结算、异常关闭、'
        '全过程归档和最终关闭保持为独立阶段，并按正常关闭或终止结算清单投影当前焦点；只读协调现有能力，不复制 ERP 台账'
    )
    for key in ('FR-094', 'FR-095', 'FR-096', 'FR-097', 'FR-098')
}
COMPLETION_VERIFICATION = {
    key: (
        'tests/test_project_completion_lifecycle.py 覆盖协调器注册、正常关闭逐阶段满足、终止项目交付/验收明确不适用、'
        '财务未完成不被推断、阶段能力不泄漏及多项目候选不合并'
    )
    for key in COMPLETION_IMPLEMENTATION
}


def parse_source(text):
    rows = {}
    for line in text.splitlines():
        match = re.fullmatch(r'(FR-\d{3})\s+(.+)', line)
        if match:
            key, content = match.groups()
            if key in rows:
                raise ValueError(f'Duplicate requirement: {key}')
            rows[key] = content
    expected = {f'FR-{n:03}' for n in range(1, 119)}
    if set(rows) != expected:
        raise ValueError('Source must contain exactly FR-001 through FR-118')
    tables = {}
    for prefix, count in [('AT', 18), ('AD', 13)]:
        values = []
        for line in text.splitlines():
            if re.match(fr'^{prefix}-\d{{2}} \|', line):
                key, title, content = line.split(' | ', 2)
                values.append({'id': key, 'title': title, 'source_text': content})
        if {v['id'] for v in values} != {f'{prefix}-{n:02}' for n in range(1, count+1)} or len(values) != count:
            raise ValueError(f'Incomplete {prefix} source')
        tables[prefix] = values
    return rows, tables


def main():
    text = SOURCE.read_text(encoding='utf-8')
    rows, tables = parse_source(text)
    existing = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    previous = {r['id']: r for r in existing.get('requirements', [])}
    requirements = []
    for key, content in rows.items():
        number = int(key[3:])
        matches = [m for m in MODULES if m[0] <= number <= m[1]]
        if len(matches) != 1:
            raise ValueError(f'Requirement must have exactly one coverage group: {key}')
        _, _, module, responsibility = matches[0]
        old = previous.get(key, {})
        implementation_evidence = list(old.get('implementation_evidence', []))
        verification_evidence = list(old.get('verification_evidence', []))
        if key in CROSS_PHASE_IMPLEMENTATION and CROSS_PHASE_IMPLEMENTATION[key] not in implementation_evidence:
            implementation_evidence.append(CROSS_PHASE_IMPLEMENTATION[key])
        if key in CROSS_PHASE_VERIFICATION and CROSS_PHASE_VERIFICATION[key] not in verification_evidence:
            verification_evidence.append(CROSS_PHASE_VERIFICATION[key])
        if key in COMPLETION_IMPLEMENTATION and COMPLETION_IMPLEMENTATION[key] not in implementation_evidence:
            implementation_evidence.append(COMPLETION_IMPLEMENTATION[key])
        if key in COMPLETION_VERIFICATION and COMPLETION_VERIFICATION[key] not in verification_evidence:
            verification_evidence.append(COMPLETION_VERIFICATION[key])
        requirements.append({
            'id': key, 'source_text': content, 'module': module,
            'agent_responsibility': responsibility,
            'latest_decision': OVERRIDES.get(key, '完整保留；具体既有动作复用不抵消本条需求。'),
            'implementation_evidence': implementation_evidence,
            'verification_evidence': verification_evidence,
            'acceptance_status': old.get('acceptance_status', 'NOT_VERIFIED'),
        })
    document = {
        'source': 'docs/erp-audit/requirements-v1.1.txt',
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'scope_note': '全量 V1.1 需求；列举模块非穷尽；不替代 V3.6 技术验收。状态未验收不等于无代码，存在代码不等于完整交付。',
        'requirements': requirements, 'acceptance_scenarios': tables['AT'], 'adaptation_items': tables['AD'],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['# V1.1 全量需求开发覆盖表', '',
             '本表保留 FR-001～118、AT-01～18、AD-01～13 原文。报价、中标、合同上传及项目大节点维护明确属于 Agent 开发；ERP 具体业务动作复用不代表整项需求已满足。', '',
             '状态 **未验收** 表示尚未登记足以证明整条需求通过的证据，不表示没有任何代码。只有相关代码、权限/异常路径测试和业务验收证据齐备才能标记通过。V3.6 的架构、界面、Harness 和部署等要求仍需独立核验，不能由本表代替。', '',
             '生成源为 `scripts/build_requirements_traceability.py`，机器跟踪文件为 `requirements/coverage.json`。生成器保留已登记的实现/验证证据；范围数量校验仅用于防遗漏，不能证明功能完成。', '',
             '## 跨阶段执行编排证据', '',
             '- `query_project_kickoff_context` 已把承接、合同、正式开工和基线计划保持为四类独立事实；`query_project_execution_context` 从基线计划继续投影设计/BOM、采购或整套委外、制造质检、装配试模、交付签收和客户验收；`query_project_completion_context` 再按正常关闭或终止结算分支投影交付验收、客户财务、供应商结算、异常关闭、归档及最终关闭。三个协调器只组织现有业务工具，不新增 ERP 式菜单或复制 ERP 执行数据。',
             '- 三个主线 Skill 首轮均只开放各自协调器，阶段查询是可选依赖。阶段能力未分配时返回 `UNAVAILABLE`；只有已读取业务事实或明确清单依据时才返回 `NOT_APPLICABLE`，后序事实不能覆盖前序缺口。',
             '- `tests/test_project_execution_lifecycle.py` 覆盖执行分支和权限边界；`tests/test_project_completion_lifecycle.py` 覆盖正常关闭、终止结算、不适用依据、财务不推断、权限隔离及候选不合并。各阶段原有测试继续验证各自证据和权限边界；这不等同于全部 FR 的真实 ERP 联调或业务验收完成。', '']
    last = None
    for row in requirements:
        if row['module'] != last:
            last = row['module']
            lines += [f"## {last}", '', row['agent_responsibility'], '']
        lines += [f"### {row['id']}", '', row['source_text'], '',
                  f"- 最新口径：{row['latest_decision']}",
                  '- 实现证据：'+('；'.join(row['implementation_evidence']) or '待逐条核验并登记'),
                  '- 验证证据：'+('；'.join(row['verification_evidence']) or '未登记完整通过证据'),
                  '- 验收状态：'+row['acceptance_status'], '']
    for name, items in [('原文验收场景', tables['AT']), ('原文适配事项', tables['AD'])]:
        lines += [f'## {name}', '', '以下保留原文；已确认的后续决定按 PRODUCT_CONTRACT.md 执行，不能重新引入已取消范围。', '']
        for item in items:
            lines += [f"### {item['id']} {item['title']}", '', item['source_text'], '']
    (ROOT/'docs/REQUIREMENTS_TRACEABILITY.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f"Preserved {len(requirements)} FR, {len(tables['AT'])} AT, {len(tables['AD'])} AD. No completion claim.")


if __name__ == '__main__':
    main()
