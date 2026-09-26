"""中标邮件候选字段协议：只接纳原文可定位的值，不推断内部身份或合同事实。"""
from decimal import Decimal, InvalidOperation

from domain_packs.mold.ports.errors import DomainError

BID_FIELD_KEYS = {
    'project_name', 'project_number', 'customer_name', 'external_order_number',
    'customer_mold_number', 'customer_due_date',
    'bid_amount', 'bid_currency',
}


def normalized(value):
    return ''.join(str(value or '').split()).casefold()


def validate_bid_fields(rows, blocks):
    """blocks 为 block_id -> (page_number, text)，兼用于模型输出和旧快照再校验。"""
    if not isinstance(rows, list) or len(rows) > 200:
        raise DomainError('DOCUMENT_MODEL_OUTPUT_INVALID', '中标邮件字段候选格式无效')
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get('field_key') not in BID_FIELD_KEYS:
            raise DomainError('DOCUMENT_MODEL_OUTPUT_INVALID', '中标邮件字段未登记')
        value = row.get('value')
        if not isinstance(value, (str, int, float)) or isinstance(value, bool) or not str(value).strip() or len(str(value)) > 150:
            raise DomainError('DOCUMENT_MODEL_OUTPUT_INVALID', '中标邮件候选须为非空标量')
        ids = row.get('source_block_ids')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 50
                or any(not isinstance(key, str) or key not in blocks for key in ids)
                or len(set(ids)) != len(ids)):
            raise DomainError('DOCUMENT_FIELD_SOURCE_INVALID', '中标邮件字段来源块不存在')
        pages = {blocks[key][0] for key in ids}
        text = '\n'.join(blocks[key][1] for key in ids)
        if len(pages) != 1 or normalized(value) not in normalized(text):
            raise DomainError('DOCUMENT_FIELD_SOURCE_INVALID', '候选值必须出现在同页来源文字中')
        try:
            confidence = Decimal(str(row.get('confidence')))
            if not confidence.is_finite() or not 0 <= confidence <= 1:
                raise ValueError
        except (InvalidOperation, ValueError, TypeError):
            raise DomainError('DOCUMENT_MODEL_OUTPUT_INVALID', '候选置信度无效') from None
        result.append({
            'field_key': row['field_key'], 'value': str(value).strip(),
            'confidence': str(confidence), 'page_number': next(iter(pages)),
            'source_block_ids': ids, 'source_text': text[:1000],
        })
    return result
