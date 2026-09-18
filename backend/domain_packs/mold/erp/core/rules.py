"""A bounded, three-valued rule interpreter. Never evaluates source code."""
from decimal import Decimal, InvalidOperation
from domain_packs.mold.ports.errors import DomainError

FIELDS = {"project_id", "category", "quantity", "remark", "currency", "amount"}
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in"}
NUMERIC_FIELDS = {'amount', 'quantity'}


def validate_rule(rule, depth=0):
    if depth > 8 or not isinstance(rule, dict): raise DomainError("INVALID_RULE", "规则结构或深度无效")
    if "all" in rule or "any" in rule:
        if len(rule) != 1: raise DomainError("INVALID_RULE", "规则不能混用组合键")
        children = next(iter(rule.values()))
        if not isinstance(children, list) or not 1 <= len(children) <= 20: raise DomainError("INVALID_RULE", "条件数量无效")
        for child in children: validate_rule(child, depth+1)
    elif set(rule) != {"field", "op", "value"} or not isinstance(rule['field'], str) or not isinstance(rule['op'], str) or rule["field"] not in FIELDS or rule["op"] not in OPS:
        raise DomainError("INVALID_RULE", "条件字段或操作符未登记")
    elif rule["op"] == "in" and (not isinstance(rule["value"], list) or len(rule["value"]) > 100):
        raise DomainError("INVALID_RULE", "集合条件无效")
    else:
        values = rule['value'] if rule['op'] == 'in' else [rule['value']]
        if not values: raise DomainError('INVALID_RULE', '集合条件不能为空')
        if rule['op'] in {'gt', 'gte', 'lt', 'lte'} and rule['field'] not in NUMERIC_FIELDS:
            raise DomainError('INVALID_RULE', '文本字段只支持等于、不等于和集合判断')
        for value in values:
            if rule['field'] in NUMERIC_FIELDS:
                try:
                    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or not Decimal(str(value)).is_finite():
                        raise ValueError()
                except (ValueError, InvalidOperation):
                    raise DomainError('INVALID_RULE', '金额或数量比较值必须为有限数字') from None
            elif not isinstance(value, str) or len(value) > 4000:
                raise DomainError('INVALID_RULE', '文本比较值类型或长度无效')


def evaluate(rule, values):
    validate_rule(rule)
    if "all" in rule or "any" in rule:
        key = "all" if "all" in rule else "any"
        answers = [evaluate(r, values) for r in rule[key]]
        if key == "all": return False if False in answers else None if None in answers else True
        return True if True in answers else None if None in answers else False
    lhs, rhs, op = values.get(rule["field"]), rule["value"], rule["op"]
    if lhs is None: return None
    if rule["field"] == "amount" and not values.get("currency"): return None
    try:
        if rule['field'] in NUMERIC_FIELDS:
            if isinstance(lhs, bool): return None
            lhs = Decimal(str(lhs))
            rhs = [Decimal(str(v)) for v in rhs] if op == 'in' else Decimal(str(rhs))
            if not lhs.is_finite(): return None
        elif not isinstance(lhs, str): return None
        return {"eq": lambda: lhs == rhs, "ne": lambda: lhs != rhs,
                "gt": lambda: lhs > rhs, "gte": lambda: lhs >= rhs,
                "lt": lambda: lhs < rhs, "lte": lambda: lhs <= rhs,
                "in": lambda: lhs in rhs}[op]()
    except (TypeError, ValueError, InvalidOperation): return None
