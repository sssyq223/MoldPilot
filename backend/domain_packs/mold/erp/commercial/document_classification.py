"""文档类型与业务事件的确定性证据识别。

这里不执行任何业务写入，也不把模型置信度当作自动触发条件。模型只负责
语义归一；是否确认由本模块的证据组、冲突和来源信息共同决定。
"""
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Literal


DocumentType = Literal[
    "BID_NOTICE", "CUSTOMER_START_NOTICE", "SALES_CONTRACT", "MOLD_DRAWING",
    "ENGINEERING_CONTACT", "OTHER",
]
EventType = Literal[
    "BID_WON", "CUSTOMER_START", "CONTRACT_SIGNED", "ENGINEERING_CONTACT", "UNKNOWN"
]
Decision = Literal["CONFIRMED", "NEEDS_REVIEW", "REJECTED"]


@dataclass(frozen=True)
class RuleEvidence:
    page: int
    text: str
    rule: str

    def as_dict(self) -> dict:
        return {"page": self.page, "text": self.text[:500], "rule": self.rule}


@dataclass(frozen=True)
class RuleConflict:
    type: str
    message: str

    def as_dict(self) -> dict:
        return {"type": self.type, "message": self.message}


@dataclass(frozen=True)
class RuleClassification:
    document_type: DocumentType
    event_type: EventType
    confidence: Decimal
    evidence: tuple[RuleEvidence, ...]
    conflicts: tuple[RuleConflict, ...]


_AWARD = (
    ("AWARD_RESULT", re.compile(r"中标通知|中标结果|中选|项目中标|恭喜贵司中标|确认承接")),
)
_CONTEXT = (
    ("PROJECT_CONTEXT", re.compile(r"客户|项目(?:号|编号|名称)?|客户模号|订单号|金额|交期|合同号")),
)
_CONTRACT = (
    ("CONTRACT_TITLE", re.compile(r"销售合同|模具合同|合同正文")),
    ("CONTRACT_NUMBER", re.compile(r"合同编号|合同号")),
    ("CONTRACT_PARTIES", re.compile(r"甲方|乙方|买方|卖方")),
    ("PAYMENT_TERMS", re.compile(r"付款条款|付款方式|付款节点|预付款")),
    ("SIGNED_DATE", re.compile(r"签订日期|签署日期|合同签订")),
)
_CUSTOMER_START = (
    ("CUSTOMER_START", re.compile(r"客户开工通知|开工通知|开工时间|开工日期")),
    ("START_APPROVAL", re.compile(r"内部审批|部门承接|责任部门")),
)
_DRAWING = re.compile(r"模具图纸|工程图|零件图|3D图|2D图|CAD|UG")
_CONTACT_TITLE = re.compile(r"工程联络单|工程联络|工程联系单|异常联络单|联络单")
_CONTACT_FIELDS = (
    ("CONTACT_PROBLEM", re.compile(r"问题(?:来源|描述|现象)|异常现象|问题点")),
    ("CONTACT_RESPONSIBILITY", re.compile(r"责任部门|责任人|担当部门|担当人")),
    ("CONTACT_ACTION", re.compile(r"处理意见|改善对策|纠正措施|临时措施|永久措施|计划动作")),
    ("CONTACT_REVIEW", re.compile(r"验证结果|复验|确认结果|关闭依据")),
)


def _lines(document):
    for page in document.pages:
        for block in page.blocks:
            text = str(block.text or "").strip()
            if text:
                yield page.page_number, text


def _hits(lines, rules):
    result = []
    seen = set()
    for page, text in lines:
        for name, pattern in rules:
            if pattern.search(text) and (page, name) not in seen:
                result.append(RuleEvidence(page, text, name))
                seen.add((page, name))
    return result


def classify_rules(document, *, source_metadata: dict | None = None) -> RuleClassification:
    """按全文页级文字生成候选类型、事件、证据和冲突。"""
    lines = tuple(_lines(document))
    award = _hits(lines, _AWARD)
    context = _hits(lines, _CONTEXT)
    contract_candidates = _hits(lines, _CONTRACT)
    # “甲方/乙方”在中标通知中也很常见，必须有合同标题/编号，或至少两组合同正文证据。
    contract_rules = {item.rule for item in contract_candidates}
    contract = (
        contract_candidates
        if {"CONTRACT_TITLE", "CONTRACT_NUMBER"} & contract_rules
        or len(contract_rules) >= 2
        else []
    )
    start = _hits(lines, _CUSTOMER_START)
    drawing = [RuleEvidence(page, text, "DRAWING_CONTEXT") for page, text in lines if _DRAWING.search(text)]
    contact_title = [RuleEvidence(page, text, "ENGINEERING_CONTACT_TITLE") for page, text in lines if _CONTACT_TITLE.search(text)]
    contact_fields = _hits(lines, _CONTACT_FIELDS)
    contact = contact_title + contact_fields
    evidence = award + context + contract + start + drawing + contact
    conflicts: list[RuleConflict] = []

    if contact_title or len({item.rule for item in contact_fields}) >= 2:
        document_type = "ENGINEERING_CONTACT"
        event_type = "ENGINEERING_CONTACT"
        confidence = Decimal("0.88" if contact_title else "0.78")
    elif award and context:
        document_type = "BID_NOTICE"
        event_type = "BID_WON"
        confidence = Decimal("0.95")
    elif contract:
        document_type, event_type, confidence = "SALES_CONTRACT", "CONTRACT_SIGNED", Decimal("0.90")
    elif start:
        document_type, event_type, confidence = "CUSTOMER_START_NOTICE", "CUSTOMER_START", Decimal("0.88")
    elif drawing:
        document_type, event_type, confidence = "MOLD_DRAWING", "UNKNOWN", Decimal("0.82")
    else:
        document_type, event_type, confidence = "OTHER", "UNKNOWN", Decimal("0.20")

    if contact and contract:
        conflicts.append(RuleConflict("MIXED_DOCUMENT", "同一 PDF 同时出现工程联络和销售合同正文语义"))
    if award and contract:
        conflicts.append(RuleConflict("MIXED_DOCUMENT", "同一 PDF 同时出现中标语义和销售合同正文语义"))
    if award and start and not contract:
        conflicts.append(RuleConflict("MIXED_DOCUMENT", "同一 PDF 同时出现中标通知和客户开工通知语义"))
    if document_type == "BID_NOTICE" and not source_metadata:
        conflicts.append(RuleConflict("SOURCE_METADATA_MISSING", "中标来源元数据缺失，需人工复核"))

    return RuleClassification(
        document_type=document_type,
        event_type=event_type,
        confidence=confidence,
        evidence=tuple(evidence[:50]),
        conflicts=tuple(conflicts),
    )


def merge_model_classification(rule: RuleClassification, model: dict, *, classifier_version: str,
                               source_metadata: dict | None = None):
    """合并模型候选；规则冲突或证据不足时绝不自动确认中标。"""
    model_type = model.get("document_type")
    model_event = model.get("event_type", "UNKNOWN")
    model_confidence = model.get("confidence")
    try:
        model_confidence = Decimal(str(model_confidence))
    except (ValueError, TypeError):
        model_confidence = Decimal("0")
    model_evidence = []
    for item in model.get("evidence") or []:
        if isinstance(item, dict) and isinstance(item.get("page"), int) and item.get("text") and item.get("rule"):
            model_evidence.append({"page": item["page"], "text": str(item["text"])[:500], "rule": str(item["rule"])[:80]})
    conflicts = [item.as_dict() for item in rule.conflicts]
    valid_types = {"BID_NOTICE", "CUSTOMER_START_NOTICE", "SALES_CONTRACT", "MOLD_DRAWING", "ENGINEERING_CONTACT"}
    valid_events = {"BID_WON", "CUSTOMER_START", "CONTRACT_SIGNED", "ENGINEERING_CONTACT", "UNKNOWN"}
    if model_type in valid_types \
            and model_type != rule.document_type and rule.document_type != "OTHER":
        conflicts.append({"type": "MODEL_RULE_CONFLICT", "message": "模型类型与确定性规则类型不一致"})
    if model_event in valid_events - {"UNKNOWN"} and model_event != rule.event_type \
            and rule.event_type != "UNKNOWN":
        conflicts.append({"type": "MODEL_RULE_EVENT_CONFLICT", "message": "模型业务事件与确定性规则事件不一致"})

    final_type = rule.document_type if rule.document_type != "OTHER" else (model_type or "OTHER")
    final_event = rule.event_type if rule.event_type != "UNKNOWN" else (model_event if model_event in valid_events - {"UNKNOWN"} else "UNKNOWN")
    # 中标仍采用规则与模型的较低值，避免来源证据不足时抬高自动确认信心；
    # 合同/开工通知等非中标文档的置信度以模型输出为准，规则只负责类型冲突和门禁。
    confidence = (
        min(rule.confidence, model_confidence)
        if final_type == "BID_NOTICE"
        else model_confidence
    )
    sufficient_bid = final_type == "BID_NOTICE" and final_event == "BID_WON" and bool(rule.evidence) and not conflicts
    if final_type == "BID_NOTICE" and not source_metadata:
        sufficient_bid = False
    # 工程联络单即使分类置信度较高，也必须由本人确认办理模式、项目和责任范围。
    decision: Decision = "NEEDS_REVIEW" if final_type == "ENGINEERING_CONTACT" else (
        "CONFIRMED" if (sufficient_bid or (final_type != "BID_NOTICE" and rule.document_type != "OTHER" and not conflicts))
        else "NEEDS_REVIEW"
    )
    if final_type == "OTHER" and not model_evidence and not rule.evidence:
        decision = "REJECTED"
    return {
        "document_type": final_type,
        "event_type": final_event,
        "decision": decision,
        "confidence": confidence,
        "evidence": tuple(model_evidence) + tuple(item.as_dict() for item in rule.evidence),
        "conflicts": tuple(conflicts),
        "needs_human_confirmation": decision != "CONFIRMED",
        "classifier_version": classifier_version,
    }
