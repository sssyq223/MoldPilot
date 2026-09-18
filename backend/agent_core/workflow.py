"""A restricted DSL compiled to BPMN; seats and engine snapshots share a DB transaction."""
import re
from copy import deepcopy
from lxml import etree
from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow import TaskState
from .errors import DomainError
from .assignments import validate_assignment, validate_add_sign_policy
from .domain_pack import component
from .hashing import canonical, content_hash


def _workflow_policy():
    return component("workflow_policy")

NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
serializer = BpmnWorkflowSerializer()


def validate(config):
    policy = _workflow_policy()
    CATALOG = policy.CATALOG
    business_types = {"generic", *getattr(policy, "WORKFLOW_TYPES", CATALOG)}
    if not isinstance(config, dict) or not {'business_type','nodes'} <= set(config) or set(config) - {'business_type','nodes','applicability','material_contract'} or not isinstance(config['business_type'], str) or config["business_type"] not in business_types:
        raise DomainError("INVALID_WORKFLOW", "流程业务类型尚未登记")
    contract=config.get('material_contract')
    if 'material_contract' in config:
        from .evidence_rules import validate_contract
        validate_contract(contract)
    policy.validate_applicability(config)
    if not isinstance(config["nodes"], list) or not 1 <= len(config["nodes"]) <= 20:
        raise DomainError("INVALID_WORKFLOW", "审批节点数量须为1至20")
    keys = set()
    for node in config["nodes"]:
        if not isinstance(node, dict) or set(node) - {"key", "name", "users", "assignment", "mode", "reject_rules", "routes", "default_target", "agent_auto_approval", "agent_auto_policy", "allow_transfer", "allow_proxy", "add_sign_policy", "return_policy", "sla"}:
            raise DomainError("INVALID_WORKFLOW", "包含尚未支持的节点配置")
        key = node.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', key) or key in keys or key in {"start", "end"} or key.startswith('gateway_'):
            raise DomainError("INVALID_WORKFLOW", "节点标识不合法或重复")
        keys.add(key)
        if not isinstance(node.get('mode'), str) or node.get("mode") not in {"ALL", "ANY", "CLAIM"} or not isinstance(node.get("name"), str) or not 1 <= len(node['name']) <= 150:
            raise DomainError("INVALID_WORKFLOW", "必须设置节点名称和审批办理方式")
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
                or set(sla) not in ({"due_hours", "remind_before_hours"}, {"due_hours", "remind_before_hours", "calendar_id"})
                or type(sla.get("due_hours")) is not int
                or type(sla.get("remind_before_hours")) is not int
                or not 1 <= sla["due_hours"] <= 24 * 365
                or not 0 <= sla["remind_before_hours"] < sla["due_hours"]
                or ("calendar_id" in sla and (not isinstance(sla["calendar_id"], str) or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", sla["calendar_id"])))
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
        etree.SubElement(proc, f"{{{NS}}}userTask", id=node["key"], name=node["name"])
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
                 'mode': node['mode'], 'rejection_reasons': matched, 'missing_rules': missing}
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
