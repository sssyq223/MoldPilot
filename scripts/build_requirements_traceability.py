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

PAYMENT_CONDITION_MATRIX_IMPLEMENTATION = (
    'PaymentStage.condition_profile 保存付款类型（PREPAYMENT/PROGRESS/ACCEPTANCE/FINAL）和结构化条件规则；'
    'condition_evidence_map 保存逐项核验依据，适用条件、非适用条件和特殊审批引用由 finance.condition 与供应商付款准备工具共同校验。'
    '非适用条件不阻断，适用条件缺失返回 PAYMENT_CONDITION_MISSING，声明特殊审批但没有引用返回 PAYMENT_SPECIAL_APPROVAL_REQUIRED；'
    '通过 mh0d0e000018 与 mi0d0e000019 迁移增加字段，保留既有条件文本/证据字段并复用原确认卡、BPM 和审批材料；'
    '条件存在特殊审批依据时，付款申请只能选择审批模板显式声明 supports_special_approval 的流程，未声明则在准备阶段阻断并返回授权候选。'
)
PAYMENT_CONDITION_MATRIX_VERIFICATION = (
    'tests/test_supplier_payment_request_tools.py 覆盖付款类型、适用/非适用规则、逐项证据缺失、特殊审批引用、'
    '确认后 evidence map 持久化及条件到申请的边界；相关供应商付款、财务上下文、合同、领域和迁移集合通过，完整真实模型/ERP/特殊审批业务验收仍未完成。'
)

QUOTATION_IMPLEMENTATION = {
    key: (
        '客户报价领域以 quote_inbound_record 保存来源标识、文件哈希和原始附件关联，以 quotation_detail 保存不可覆盖的版本化价格、'
        '交期、收款条件、内部成本/工艺/工期或整套委外供应商评估、客户与责任人快照；prepare_quotation_version 经本人确认后提交独立 Agent BPM，'
        '审批生效时关闭前一版本。prepare_quotation_feedback 只追加客户反馈事实，不自动承接、拒单或生成新版本；承接决定在存在生效报价时必须引用该版本且金额币种一致。'
        'query_quote_evaluation_context 和 query_project_kickoff_context 可查询历史版本、资料来源、反馈及后续承接链路，合同和实际执行数据不会覆盖原报价依据'
    )
    for key in ('FR-006', 'FR-007', 'FR-008', 'FR-009', 'FR-010', 'FR-011', 'FR-012')
}
QUOTATION_VERIFICATION = {
    key: (
        'tests/test_quotation_tools.py 覆盖首版报价确认前零写入、BPM 生效、客户反馈防重、同一接收资料合法复用、连续新版本替代、'
        '内部与整套委外结构化字段校验，以及承接必须引用当前报价且金额一致；tests/test_quote_evaluation_tools.py、tests/test_quote_tools.py '
        '和 tests/test_project_kickoff_lifecycle.py 覆盖历史查询、权限边界及报价到承接的链路投影；tests/test_split_migrations.py 验证报价证据表及数据库不可变触发器'
    )
    for key in QUOTATION_IMPLEMENTATION
}

ASSEMBLY_READINESS_IMPLEMENTATION = {
    'FR-062': (
        'query_assembly_trial_context 在权限隔离后投影可见设计 BOM、采购/收货/质检/库存事实，'
        '按物料需求与已核验数量给出 assembly_readiness 和装配试模 handoffs；'
        'prepare_assembly_execution 仅基于真实生效装配任务准备开工/完工回执确认卡，本人确认后复用既有 assembly.execute 命令写入 Agent 执行事实；'
        '关键件清单、70%～80%阈值、机台/资源排程和原始试模报告附件未被推断，'
        '分别明确返回 UNCONFIGURED、UNVERIFIED 或 UNAVAILABLE，不新增 ERP 页面或复制执行台账'
    ),
    'FR-063': (
        'query_assembly_trial_context 在装配/试模上下文中返回 trial_resources、trial_report_evidence 和 assembly_trial_handoffs，'
        '区分责任人/日期/地点等文本证据、资源可用性和试模报告附件状态；'
        'prepare_trial_result 仅基于真实生效试模申请准备通过/未通过确认卡，未通过必须关联工程联络单，本人确认后复用既有 trial.confirm 命令；'
        '确认试模结论时可在权限范围内关联当前用户上传的 FileObject 原件并保留不可变版本，'
        '没有 Agent 报告附件时仍保持 UNAVAILABLE；没有 ERP 排程时资源保持 UNVERIFIED，并将后续交付放行作为独立 handoff'
    ),
}
ASSEMBLY_READINESS_VERIFICATION = {
    'FR-062': (
        'tests/test_assembly_trial_tools.py 覆盖可见 BOM、采购/收货/库存数量投影、'
        'READY/NO_EFFECTIVE_BOM 状态、权限隔离、关键件规则未配置和阈值不被猜测；'
        '新增 prepare_assembly_execution 确认前零写入、确认后登记装配完工回执；'
        'tests/test_project_execution_lifecycle.py 覆盖装配试模 handoff 与阶段事实隔离'
    ),
    'FR-063': (
        'tests/test_assembly_trial_tools.py 覆盖试模资源字段、资源可用性未核验、报告文本证据和附件不可用状态；'
        '新增 prepare_trial_result 确认前零写入、确认后登记试模结论及未通过边界；'
        '新增试模结论→FileObject 报告原件关联及 assembly_readiness ATTACHED/VERIFIED 状态测试；'
        'tests/test_project_execution_lifecycle.py 覆盖 assembly_trial_handoffs 在交付放行前的串联；'
        '真实 ERP 排程、ERP 原生报告权限和业务现场验收仍需后续确认'
    ),
}

CUSTOMER_ACCEPTANCE_EVIDENCE_IMPLEMENTATION = {
    'FR-068': (
        '客户签收与客户质量验收继续作为两类独立 Agent 事实；prepare_customer_acceptance 在人工确认后登记验收结果，'
        'prepare_customer_delivery_signature 登记发运后的 DELIVERY 签收，prepare_mold_transfer_receipt 对移模时间使用的签收原件做同样边界校验；两类原件分别以 '
        'CustomerAcceptanceAttachment / CustomerDeliverySignatureAttachment 不可变版本保存哈希，查询交付、整套委外和财务上下文均返回原件元数据，'
        '不把签收自动升级为验收或回款。'
    ),
    'FR-069': (
        '客户验收记录保留问题、责任、整改期限、工程联络单、供应商、扣款、交期影响和合同变化字段；'
        '复验通过 previous_acceptance_id 串联当前验收链，客户原件以不可变附件事实随验收记录保存，不自动执行扣款、改合同或关闭项目。'
    ),
}
CUSTOMER_ACCEPTANCE_EVIDENCE_VERIFICATION = {
    'FR-068': (
        'tests/test_delivery_logistics_tools.py 与 tests/test_finance_context_tools.py 覆盖签收与验收分离、人工确认前零写入、'
        '确认后客户原件关联、DELIVERY 与 MOLD_TRANSFER 记录区分、交付/财务上下文原件投影和重复阻断；真实客户签字、ERP 物流和回款业务验收仍未完成。'
    ),
    'FR-069': (
        'tests/test_delivery_logistics_tools.py 覆盖失败验收字段、整改复验链、原件哈希及附件回读；'
        'tests/test_finance_quality_context.py 与收尾回归继续验证费用/合同/计划影响只读投影，真实整改执行和现场复验仍需验收。'
    ),
}

OUTBOUND_RELEASE_EVIDENCE_IMPLEMENTATION = (
    'prepare_outbound_release 继续只登记 Agent 出厂自检/放行事实，不复制 ERP 出库或发货；'
    '当前任务中本人上传的放行报告可在人工确认后以 OutboundReleaseAttachment 不可变版本保存，'
    'query_delivery_logistics_context 返回原件元数据并将放行证据与 ERP 履约、客户签收分别投影。'
)
OUTBOUND_RELEASE_EVIDENCE_VERIFICATION = (
    'tests/test_delivery_logistics_tools.py 覆盖试模前置、确认前零写入、放行原件关联、原件查询和交付门禁；'
    '真实 ERP 出厂检测报告权限、入库/出库放行和现场交付仍需业务环境验收。'
)

OUTSOURCE_CHANGE_NEGOTIATION_IMPLEMENTATION = (
    '新增 prepare_outsource_change_negotiation，基于真实项目版本、生效整套委外合同、供应商和工程联络单准备客户报价、供应商报价、最终协商金额、交期/任务影响及合同变更要求确认卡；'
    '本人确认后追加既有 OutsourceChangeNegotiation 事实，不自动改合同、计划、责任或 ERP 执行数据；AGREED 仍需独立合同版本或计划变更审批。'
)
OUTSOURCE_CHANGE_NEGOTIATION_VERIFICATION = (
    'tests/test_full_outsource_tools.py 覆盖确认前零写入、客户/供应商报价与议价金额、交期影响、工程联络关联、合同变更门禁、重复来源和查询上下文投影；'
    '真实客户设变、供应商议价回执、合同版本审批及 ERP/财务联调仍需业务环境验收。'
)


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
        if key in QUOTATION_IMPLEMENTATION and QUOTATION_IMPLEMENTATION[key] not in implementation_evidence:
            implementation_evidence.append(QUOTATION_IMPLEMENTATION[key])
        if key in QUOTATION_VERIFICATION and QUOTATION_VERIFICATION[key] not in verification_evidence:
            verification_evidence.append(QUOTATION_VERIFICATION[key])
        if key in ASSEMBLY_READINESS_IMPLEMENTATION and ASSEMBLY_READINESS_IMPLEMENTATION[key] not in implementation_evidence:
            implementation_evidence.append(ASSEMBLY_READINESS_IMPLEMENTATION[key])
        if key in ASSEMBLY_READINESS_VERIFICATION and ASSEMBLY_READINESS_VERIFICATION[key] not in verification_evidence:
            verification_evidence.append(ASSEMBLY_READINESS_VERIFICATION[key])
        if key in CUSTOMER_ACCEPTANCE_EVIDENCE_IMPLEMENTATION and CUSTOMER_ACCEPTANCE_EVIDENCE_IMPLEMENTATION[key] not in implementation_evidence:
            implementation_evidence.append(CUSTOMER_ACCEPTANCE_EVIDENCE_IMPLEMENTATION[key])
        if key in CUSTOMER_ACCEPTANCE_EVIDENCE_VERIFICATION and CUSTOMER_ACCEPTANCE_EVIDENCE_VERIFICATION[key] not in verification_evidence:
            verification_evidence.append(CUSTOMER_ACCEPTANCE_EVIDENCE_VERIFICATION[key])
        if key == 'FR-064':
            if OUTBOUND_RELEASE_EVIDENCE_IMPLEMENTATION not in implementation_evidence:
                implementation_evidence.append(OUTBOUND_RELEASE_EVIDENCE_IMPLEMENTATION)
            if OUTBOUND_RELEASE_EVIDENCE_VERIFICATION not in verification_evidence:
                verification_evidence.append(OUTBOUND_RELEASE_EVIDENCE_VERIFICATION)
        if key in {'FR-074', 'FR-075'}:
            if OUTSOURCE_CHANGE_NEGOTIATION_IMPLEMENTATION not in implementation_evidence:
                implementation_evidence.append(OUTSOURCE_CHANGE_NEGOTIATION_IMPLEMENTATION)
            if OUTSOURCE_CHANGE_NEGOTIATION_VERIFICATION not in verification_evidence:
                verification_evidence.append(OUTSOURCE_CHANGE_NEGOTIATION_VERIFICATION)
        if key in {'FR-056', 'FR-103', 'FR-104', 'FR-105', 'FR-114'}:
            if PAYMENT_CONDITION_MATRIX_IMPLEMENTATION not in implementation_evidence:
                implementation_evidence.append(PAYMENT_CONDITION_MATRIX_IMPLEMENTATION)
            if PAYMENT_CONDITION_MATRIX_VERIFICATION not in verification_evidence:
                verification_evidence.append(PAYMENT_CONDITION_MATRIX_VERIFICATION)
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
             '- `query_project_lifecycle_context` 只复用三个分段协调器的结构化结果，先返回启动、执行、收尾摘要和唯一当前分段；`query_project_kickoff_context` 把客户报价、承接、合同、正式开工和基线计划保持为五类独立事实；`query_project_execution_context` 从基线计划继续投影设计/BOM、采购或整套委外、制造质检、装配试模、交付签收和客户验收；`query_project_completion_context` 再按正常关闭或终止结算分支投影交付验收、客户财务、供应商结算、异常关闭、归档及最终关闭。四个协调器只组织现有业务工具，不新增 ERP 式菜单或复制 ERP 执行数据。',
             '- 总 Skill 首轮只开放全生命周期协调器，随后只展开一个分段协调器；三个分段 Skill 首轮也只开放各自协调器，阶段查询是可选依赖。阶段能力未分配时返回 `UNAVAILABLE`；只有已读取业务事实或明确清单依据时才返回 `NOT_APPLICABLE`，后序事实不能覆盖前序缺口。',
             '- `tests/test_project_lifecycle_overview.py` 覆盖分层注册、启动转执行、暂停路由、资料矛盾、权限隔离和候选不合并；`tests/test_project_execution_lifecycle.py` 覆盖执行分支和权限边界；`tests/test_project_completion_lifecycle.py` 覆盖正常关闭、终止结算、不适用依据、财务不推断、权限隔离及候选不合并。各阶段原有测试继续验证各自证据和权限边界；这不等同于全部 FR 的真实 ERP 联调或业务验收完成。', '']
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
