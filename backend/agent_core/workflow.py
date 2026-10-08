"""A restricted DSL compiled to BPMN; seats and engine snapshots share a DB transaction."""
import re
from copy import deepcopy
from lxml import etree
from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow import TaskState
from .errors import DomainError
from .assignments import validate_assignment, validate_add_sign_policy, valid_ids, valid_keys
from .domain_pack import component
from .hashing import canonical, content_hash


def _workflow_policy():
    return component("workflow_policy")

NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
serializer = BpmnWorkflowSerializer()

WORKFLOW_KEYS = {
    'business_type', 'nodes', 'applicability', 'material_contract',
    'form_schema', 'metadata', 'integration',
}
NODE_KEYS = {
    'key', 'name', 'users', 'assignment', 'mode', 'required_approvals',
    'reject_rules', 'routes', 'default_target', 'agent_auto_approval',
    'agent_auto_policy', 'allow_transfer', 'allow_proxy', 'add_sign_policy',
    'return_policy', 'sla', 'task_type', 'form_schema', 'parallel_group',
    'line_item_scope', 'external_action',
}
FORM_TYPES = {'string', 'text', 'integer', 'decimal', 'date', 'datetime', 'boolean', 'enum'}


def validate_form_schema(schema, *, label='表单'):
    """Validate the bounded form contract stored with a workflow.

    Form values are deliberately declarative.  They are later copied into the
    approval snapshot/action context; no executable expression is accepted.
    """
    if schema is None:
        return
    if not isinstance(schema, dict) or set(schema) - {'fields', 'allow_extra'}:
        raise DomainError('INVALID_WORKFLOW', f'{label}配置格式无效')
    fields = schema.get('fields')
    if not isinstance(fields, list) or len(fields) > 100:
        raise DomainError('INVALID_WORKFLOW', f'{label}字段数量无效')
    keys = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - {'key', 'label', 'type', 'required', 'options', 'min', 'max'}:
            raise DomainError('INVALID_WORKFLOW', f'{label}字段配置包含未支持的属性')
        key = field.get('key', '')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', key) or key in keys:
            raise DomainError('INVALID_WORKFLOW', f'{label}字段标识不合法或重复')
        keys.add(key)
        if not isinstance(field.get('label'), str) or not 1 <= len(field['label']) <= 150:
            raise DomainError('INVALID_WORKFLOW', f'{label}字段必须有名称')
        if field.get('type') not in FORM_TYPES or type(field.get('required', False)) is not bool:
            raise DomainError('INVALID_WORKFLOW', f'{label}字段类型或必填标记无效')
        if field.get('type') == 'enum':
            options = field.get('options')
            if not isinstance(options, list) or not 1 <= len(options) <= 100 or any(
                not isinstance(item, str) or not item for item in options
            ) or len(options) != len(set(options)):
                raise DomainError('INVALID_WORKFLOW', f'{label}枚举字段必须配置不重复选项')
        elif 'options' in field:
            raise DomainError('INVALID_WORKFLOW', f'{label}非枚举字段不能配置选项')
        for bound in ('min', 'max'):
            if bound in field and (not isinstance(field[bound], (int, float)) or isinstance(field[bound], bool)):
                raise DomainError('INVALID_WORKFLOW', f'{label}字段范围无效')
    if 'allow_extra' in schema and type(schema['allow_extra']) is not bool:
        raise DomainError('INVALID_WORKFLOW', f'{label}允许扩展字段标记必须为布尔值')


def validate_form_values(schema, values):
    """Validate submitted values against a published node form contract."""
    if schema is None:
        if values:
            raise DomainError('FORM_FIELDS_NOT_ALLOWED', '当前审批节点没有可填写的表单字段', 400)
        return
    validate_form_schema(schema)
    if not isinstance(values, dict):
        raise DomainError('FORM_VALUES_INVALID', '审批表单值必须是对象', 400)
    fields = {field['key']: field for field in schema['fields']}
    if not schema.get('allow_extra', False) and set(values) - set(fields):
        raise DomainError('FORM_VALUES_INVALID', '审批表单包含未登记字段', 400)
    for key, field in fields.items():
        value = values.get(key)
        if value is None:
            if field.get('required'):
                raise DomainError('FORM_REQUIRED', f'请填写：{field["label"]}', 400)
            continue
        kind = field['type']
        valid = (
            (kind in {'string', 'text', 'date', 'datetime'} and isinstance(value, str))
            or (kind == 'integer' and type(value) is int)
            or (kind == 'decimal' and (isinstance(value, (int, float, str)) and not isinstance(value, bool)))
            or (kind == 'boolean' and type(value) is bool)
            or (kind == 'enum' and isinstance(value, str) and value in field['options'])
        )
        if not valid:
            raise DomainError('FORM_VALUES_INVALID', f'字段“{field["label"]}”类型或取值无效', 400)
        if kind in {'integer', 'decimal'}:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                raise DomainError('FORM_VALUES_INVALID', f'字段“{field["label"]}”必须是数字', 400) from None
            if 'min' in field and numeric < field['min'] or 'max' in field and numeric > field['max']:
                raise DomainError('FORM_VALUES_INVALID', f'字段“{field["label"]}”超出允许范围', 400)


def validate(config):
    policy = _workflow_policy()
    CATALOG = policy.CATALOG
    business_types = {"generic", *getattr(policy, "WORKFLOW_TYPES", CATALOG)}
    if not isinstance(config, dict) or not {'business_type','nodes'} <= set(config) or set(config) - WORKFLOW_KEYS or not isinstance(config['business_type'], str) or config["business_type"] not in business_types:
        raise DomainError("INVALID_WORKFLOW", "流程业务类型尚未登记")
    contract=config.get('material_contract')
    if 'material_contract' in config:
        from .evidence_rules import validate_contract
        validate_contract(contract)
    validate_form_schema(config.get('form_schema'), label='流程表单')
    if 'metadata' in config and (not isinstance(config['metadata'], dict) or len(config['metadata']) > 50):
        raise DomainError('INVALID_WORKFLOW', '流程元数据格式无效')
    if 'integration' in config:
        integration = config['integration']
        if not isinstance(integration, dict) or set(integration) - {'system', 'action', 'on_approve', 'payload_fields'}:
            raise DomainError('INVALID_WORKFLOW', '流程联动配置格式无效')
        if integration.get('system') not in (None, 'erp') or any(
            not isinstance(integration.get(key), str) or not integration[key]
            for key in ('action', 'on_approve') if key in integration
        ):
            raise DomainError('INVALID_WORKFLOW', '流程联动目标无效')
        if 'payload_fields' in integration and not valid_keys(integration['payload_fields'], True):
            raise DomainError('INVALID_WORKFLOW', '流程联动字段列表无效')
    policy.validate_applicability(config)
    if not isinstance(config["nodes"], list) or not 1 <= len(config["nodes"]) <= 20:
        raise DomainError("INVALID_WORKFLOW", "审批节点数量须为1至20")
    keys = set()
    for node in config["nodes"]:
        if not isinstance(node, dict) or set(node) - NODE_KEYS:
            raise DomainError("INVALID_WORKFLOW", "包含尚未支持的节点配置")
        key = node.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', key) or key in keys or key in {"start", "end"} or key.startswith('gateway_'):
            raise DomainError("INVALID_WORKFLOW", "节点标识不合法或重复")
        keys.add(key)
        if not isinstance(node.get('mode'), str) or node.get("mode") not in {"ALL", "ANY", "QUORUM", "CLAIM"} or not isinstance(node.get("name"), str) or not 1 <= len(node['name']) <= 150:
            raise DomainError("INVALID_WORKFLOW", "必须设置节点名称和审批办理方式")
        if node.get('task_type', 'approval') not in {'approval', 'business_task'}:
            raise DomainError('INVALID_WORKFLOW', '节点任务类型只支持 approval 或 business_task')
        if node.get('task_type') == 'business_task' and (
            not isinstance(node.get('external_action'), str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{2,79}', node['external_action'])
        ):
            raise DomainError('INVALID_WORKFLOW', '业务任务必须配置合法的外部动作标识')
        if 'external_action' in node and node.get('task_type', 'approval') != 'business_task':
            raise DomainError('INVALID_WORKFLOW', '只有业务任务节点可以配置外部动作')
        validate_form_schema(node.get('form_schema'), label=f'节点 {key} 表单')
        if 'parallel_group' in node and (
            not isinstance(node['parallel_group'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', node['parallel_group'])
        ):
            raise DomainError('INVALID_WORKFLOW', '并行会签组标识无效')
        if 'line_item_scope' in node and node['line_item_scope'] not in {'document', 'line', 'line_required'}:
            raise DomainError('INVALID_WORKFLOW', '明细办理范围无效')
        required_approvals = node.get("required_approvals")
        if node.get("mode") == "QUORUM":
            if type(required_approvals) is not int or not 1 <= required_approvals <= 50:
                raise DomainError("INVALID_WORKFLOW", "比例会签须设置1至50人的通过票数")
        elif required_approvals is not None:
            raise DomainError("INVALID_WORKFLOW", "只有比例会签节点可以设置通过票数")
        if "agent_auto_approval" in node and not isinstance(node["agent_auto_approval"], bool):
            raise DomainError("INVALID_WORKFLOW", "Agent 自动审批节点标记必须为布尔值")
        if node.get("mode") == "CLAIM" and node.get("agent_auto_approval"):
            raise DomainError("INVALID_WORKFLOW", "候选领取节点必须由候选人本人先领取，不能配置 Agent 自动审批")
        if "allow_transfer" in node and not isinstance(node["allow_transfer"], bool):
            raise DomainError("INVALID_WORKFLOW", "审批转交节点标记必须为布尔值")
        if "allow_proxy" in node and not isinstance(node["allow_proxy"], bool):
            raise DomainError("INVALID_WORKFLOW", "审批代理节点标记必须为布尔值")
        if "return_policy" in node:
            return_policy = node["return_policy"]
            if not isinstance(return_policy, dict) or set(return_policy) != {"targets"}:
                raise DomainError("INVALID_WORKFLOW", "退回策略必须只包含允许目标")
            return_targets = return_policy["targets"]
            if (not isinstance(return_targets, list) or not 1 <= len(return_targets) <= 20
                    or any(not isinstance(target, str) for target in return_targets)
                    or len(return_targets) != len(set(return_targets))):
                raise DomainError("INVALID_WORKFLOW", "退回目标须为1至20个不重复节点")
        if "agent_auto_policy" in node:
            if not node.get("agent_auto_approval"):
                raise DomainError("INVALID_WORKFLOW", "只有允许 Agent 自动审批的节点才能设置自动审批策略")
            if not isinstance(node["agent_auto_policy"], dict) or set(node["agent_auto_policy"]) != {"condition"}:
                raise DomainError("INVALID_WORKFLOW", "Agent 自动审批策略必须包含安全条件")
            validate_condition(node["agent_auto_policy"]["condition"], contract)
        validate_assignment(node)
        validate_add_sign_policy(node)
        if "sla" in node:
            sla = node["sla"]
            if (
                not isinstance(sla, dict)
                or not {"due_hours", "remind_before_hours"} <= set(sla)
                or set(sla) - {"due_hours", "remind_before_hours", "calendar_id", "cc_user_ids", "escalation_user_ids"}
                or type(sla.get("due_hours")) is not int
                or type(sla.get("remind_before_hours")) is not int
                or not 1 <= sla["due_hours"] <= 24 * 365
                or not 0 <= sla["remind_before_hours"] < sla["due_hours"]
                or ("calendar_id" in sla and (not isinstance(sla["calendar_id"], str) or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", sla["calendar_id"])))
                or ("cc_user_ids" in sla and not valid_ids(sla["cc_user_ids"]))
                or ("escalation_user_ids" in sla and not valid_ids(sla["escalation_user_ids"]))
            ):
                raise DomainError(
                    "INVALID_WORKFLOW",
                    "办理时限须为1至8760小时；提前提醒须小于办理时限，填0表示不提前提醒",
                )
        if not isinstance(node.get('reject_rules', []), list) or len(node.get('reject_rules', [])) > 20:
            raise DomainError('INVALID_RULE', '单个节点最多配置20条驳回规则')
        for rule in node.get("reject_rules", []):
            if not isinstance(rule, dict) or set(rule) != {"condition", "reason"} or not isinstance(rule['reason'], str) or not 1 <= len(rule["reason"]) <= 2000:
                raise DomainError("INVALID_RULE", "驳回规则必须包含条件和原因")
            validate_condition(rule["condition"],contract)
    groups = {}
    for index, node in enumerate(config['nodes']):
        group = node.get('parallel_group')
        if group:
            groups.setdefault(group, []).append(index)
            if node['mode'] != 'ALL':
                raise DomainError('INVALID_WORKFLOW', '并行会签组节点必须使用 ALL 模式')
    for group, indexes in groups.items():
        if indexes != list(range(min(indexes), max(indexes) + 1)):
            raise DomainError('INVALID_WORKFLOW', f'并行会签组 {group} 必须连续配置')
    positions = {node['key']: i for i, node in enumerate(config['nodes'])}
    reachable = {0}
    for i, node in enumerate(config['nodes']):
        if i not in reachable:
            raise DomainError('INVALID_WORKFLOW', '存在从开始节点无法到达的审批节点')
        for return_target in node.get("return_policy", {}).get("targets", ["applicant"]):
            if return_target != "applicant" and (
                    return_target not in positions or positions[return_target] >= i):
                raise DomainError("INVALID_WORKFLOW", "退回目标只能是申请人或当前节点之前的责任节点")
        if 'routes' in node or 'default_target' in node:
            routes = node.get('routes')
            if not isinstance(routes, list) or not 1 <= len(routes) <= 20 or 'default_target' not in node:
                raise DomainError('INVALID_WORKFLOW', '条件路由须配置1至20个分支及默认出口')
            targets = [node['default_target']]
            for branch in routes:
                if not isinstance(branch, dict) or set(branch) != {'condition', 'target'}:
                    raise DomainError('INVALID_WORKFLOW', '分支必须包含条件与目标')
                validate_condition(branch['condition'],contract)
                targets.append(branch['target'])
        else:
            targets = [config['nodes'][i+1]['key'] if i+1 < len(config['nodes']) else 'end']
        for target in targets:
            if target == 'end': continue
            if not isinstance(target, str) or target not in positions or positions[target] <= i:
                raise DomainError('INVALID_WORKFLOW', '路由须指向后续审批节点或结束，不能包含环路或未知节点')
            reachable.add(positions[target])


def compile_bpmn(config):
    validate(config)
    root = etree.Element(f"{{{NS}}}definitions", nsmap={None: NS}, targetNamespace="urn:agent-workbench")
    proc = etree.SubElement(root, f"{{{NS}}}process", id="approval", isExecutable="true")
    etree.SubElement(proc, f"{{{NS}}}startEvent", id="start")
    for node in config["nodes"]:
        task = etree.SubElement(proc, f"{{{NS}}}userTask", id=node["key"], name=node["name"])
        # Keep the generic engine executable while preserving the richer BPM
        # contract for clients, audit exports, and future service-task workers.
        task.set("taskType", node.get("task_type", "approval"))
        if node.get("parallel_group"):
            task.set("parallelGroup", node["parallel_group"])
    etree.SubElement(proc, f"{{{NS}}}endEvent", id="end")
    def connect(source, target, suffix, expression=None):
        edge = etree.SubElement(proc, f'{{{NS}}}sequenceFlow', id=f'flow_{suffix}', sourceRef=source, targetRef=target)
        if expression is not None:
            # Only generated identifiers and validated node keys enter the engine expression.
            etree.SubElement(edge, f'{{{NS}}}conditionExpression').text = expression
    connect('start', config['nodes'][0]['key'], 'start')
    for i, node in enumerate(config['nodes']):
        if 'routes' not in node:
            connect(node['key'], config['nodes'][i+1]['key'] if i+1 < len(config['nodes']) else 'end', str(i))
            continue
        gateway = 'gateway_' + node['key']
        etree.SubElement(proc, f'{{{NS}}}exclusiveGateway', id=gateway)
        connect(node['key'], gateway, f'{i}_gateway')
        targets = dict.fromkeys([r['target'] for r in node['routes']] + [node['default_target']])
        for j, target in enumerate(targets):
            connect(gateway, target, f'{i}_branch_{j}', f"route_{node['key']} == '{target}'")
    return etree.tostring(root, encoding="unicode")


def start_engine(xml):
    # XML is generated only by our validated compiler, not accepted from browser uploads.
    root = etree.fromstring(xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    parser = BpmnParser()
    parser.add_bpmn_xml(root)
    workflow = BpmnWorkflow(parser.get_spec("approval"))
    workflow.do_engine_steps()
    return serializer.to_dict(workflow)


def advance_engine(state, node_key, route_target=None):
    workflow = serializer.from_dict(deepcopy(state))
    tasks = workflow.get_tasks(state=TaskState.READY, manual=True)
    matched = [t for t in tasks if t.task_spec.name == node_key]
    if len(matched) != 1: raise DomainError("ENGINE_STATE_CONFLICT", "流程等待节点不一致", 409)
    if route_target is not None:
        matched[0].data[f'route_{node_key}'] = route_target
    matched[0].run()
    workflow.do_engine_steps()
    return serializer.to_dict(workflow)


def route_target(config, stage_index, snapshot):
    node = config['nodes'][stage_index]
    if 'routes' not in node:
        return config['nodes'][stage_index+1]['key'] if stage_index+1 < len(config['nodes']) else 'end'
    results = [evaluate_snapshot(r['condition'], snapshot,config.get('material_contract')) for r in node['routes']]
    if None in results:
        raise DomainError('ROUTE_DATA_MISSING', '分支判断资料缺失或类型不正确，不能沿默认出口放行', 409)
    if results.count(True) > 1:
        raise DomainError('ROUTE_AMBIGUOUS', '同时命中多个审批分支，请管理员修正条件后重新提交', 409)
    return next((r['target'] for r, result in zip(node['routes'], results) if result), node['default_target'])


def validate_condition(condition,contract=None):
    if contract is None:return _workflow_policy().validate_rule(condition)
    from .evidence_rules import validate_rule as validate_material_rule
    return validate_material_rule(condition,contract)


def evaluate_snapshot(condition, snapshot,contract=None):
    if contract is not None:
        from .evidence_rules import evaluate as evaluate_material_rule
        return evaluate_material_rule(condition,snapshot.get('material_data',{}),contract)['result']
    outcomes = [_workflow_policy().evaluate(condition, {**snapshot, **line}) for line in (snapshot.get('lines') or [{}])]
    # Multi-line conditions explicitly mean any line; unknown lines must not silently route to default.
    return True if True in outcomes else None if None in outcomes else False


def current_stage(state, config):
    # Spiff deserialization mutates its input; never contaminate persisted JSON with spec objects.
    workflow = serializer.from_dict(deepcopy(state))
    if workflow.is_completed(): return len(config['nodes'])
    tasks = workflow.get_tasks(state=TaskState.READY, manual=True)
    if len(tasks) != 1:
        raise DomainError('ENGINE_STATE_CONFLICT', '流程未处于唯一可办理节点', 409)
    for i, node in enumerate(config['nodes']):
        if node['key'] == tasks[0].task_spec.name: return i
    raise DomainError('ENGINE_STATE_CONFLICT', '引擎节点与已发布模板不一致', 409)


def simulate(config, snapshot):
    validate(config)
    state = start_engine(compile_bpmn(config))
    path = []
    stage = current_stage(state, config)
    while stage < len(config['nodes']):
        node = config['nodes'][stage]
        matched, missing = reject_findings(node, snapshot,config.get('material_contract'))
        entry = {'key': node['key'], 'name': node['name'], 'users': node.get('users',[]), 'assignment': node.get('assignment'),
                 'mode': node['mode'], 'required_approvals': node.get('required_approvals'),
                 'task_type': node.get('task_type', 'approval'),
                 'parallel_group': node.get('parallel_group'),
                 'line_item_scope': node.get('line_item_scope', 'document'),
                 'form_schema': node.get('form_schema', config.get('form_schema')),
                 'rejection_reasons': matched, 'missing_rules': missing}
        path.append(entry)
        if config.get('material_contract') is not None:
            from .evidence_rules import evaluate as evaluate_material_rule
            entry['condition_evaluations']=[{'target':r['target'],'evaluation':evaluate_material_rule(r['condition'],snapshot.get('material_data',{}),config['material_contract'])} for r in node.get('routes',[])]
        if matched or missing:
            return {'simulation': True, 'path': path, 'outcome': 'MUST_REJECT' if matched else 'RULE_DATA_MISSING'}
        try: target = route_target(config, stage, snapshot)
        except DomainError as error:
            return {'simulation': True, 'path': path, 'outcome': error.code}
        entry['target'] = target
        state = advance_engine(state, node['key'], target if 'routes' in node else None)
        stage = current_stage(state, config)
    return {'simulation': True, 'path': path, 'outcome': 'ROUTE_VALID'}


def reject_findings(node, snapshot,contract=None):
    matched, missing = [], []
    for rule in node.get("reject_rules", []):
        result=evaluate_snapshot(rule['condition'],snapshot,contract)
        if result is True:matched.append(rule['reason'])
        elif result is None:missing.append(rule['reason'])
    return matched, missing
