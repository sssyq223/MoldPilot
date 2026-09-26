"""复用已校验的字段候选和批次完成审计凭据，不另存模型原始响应。

作业成功前字段是暂存候选，查询层只读取 SUCCEEDED 作业；成功事务统一物化最终行标识。
完成凭据仅包含指纹、页码和候选记录引用，也能区分“空批次完成”与“未处理”。
"""
from uuid import uuid4
from sqlalchemy import select, delete
from . import models as m
from .errors import DomainError
from domain_packs.mold.erp.commercial.ocr_provider import ExtractedField

ACTION = 'document.ocr.batch.completed'
SPLIT_ACTION = 'document.ocr.batch.split'
CALL_ACTION = 'document.ocr.model_call.completed'


class BatchCache:
    def __init__(self, factory, job_id, lease_id):
        self.factory, self.job_id, self.lease_id = factory, job_id, lease_id

    def _job(self, db, lock=False):
        query = select(m.DocumentOcrJob).where(m.DocumentOcrJob.id==self.job_id,
            m.DocumentOcrJob.status=='PROCESSING',m.DocumentOcrJob.lease_id==self.lease_id)
        job = db.scalar(query.with_for_update() if lock else query)
        if not job:
            raise DomainError('OCR_LEASE_LOST','识别任务租约已失效',409)
        return job

    def _receipt(self, db, fingerprint):
        return db.scalar(select(m.AuditEvent).where(m.AuditEvent.action==ACTION,
            m.AuditEvent.resource_id==self.job_id,
            m.AuditEvent.detail['fingerprint'].as_string()==fingerprint).order_by(m.AuditEvent.created_at.desc()))

    def load(self, fingerprint):
        with self.factory() as db:
            self._job(db)
            receipt = self._receipt(db,fingerprint)
            if not receipt:
                return None
            fields=[]
            for ref in receipt.detail['fields']:
                row=db.get(m.DocumentExtractedField,ref['id'])
                if not row or row.job_id!=self.job_id:
                    return None
                fields.append(ExtractedField(row.scope,ref['row_key'],row.field_key,row.raw_value,
                    row.normalized_value,row.confidence,row.page_number,row.bbox,tuple(row.source_block_ids)))
            return tuple(fields)

    def _split_receipt(self, db, fingerprint):
        return db.scalar(select(m.AuditEvent).where(m.AuditEvent.action==SPLIT_ACTION,
            m.AuditEvent.resource_id==self.job_id,
            m.AuditEvent.detail['fingerprint'].as_string()==fingerprint).order_by(m.AuditEvent.created_at.desc()))

    @staticmethod
    def _check_split(receipt, pages):
        if receipt.detail.get('version') != 1 or receipt.detail.get('pages') != [p.page_number for p in pages]:
            raise DomainError('OCR_BATCH_CACHE_CONFLICT','批次拆分记录与来源不一致',409)

    def load_split(self, fingerprint, pages):
        with self.factory() as db:
            self._job(db)
            receipt=self._split_receipt(db,fingerprint)
            if receipt:
                self._check_split(receipt,pages)
            return receipt is not None

    def save_split(self, fingerprint, pages, reason):
        if not 2 <= len(pages) <= 4 or reason not in {'INPUT_BUDGET','OUTPUT_TRUNCATED','RESUME_SPLIT'}:
            raise DomainError('OCR_BATCH_CACHE_CONFLICT','无效的批次拆分记录',409)
        with self.factory.begin() as db:
            self._job(db,lock=True)
            receipt=self._split_receipt(db,fingerprint)
            if receipt:
                self._check_split(receipt,pages)
                return
            db.add(m.AuditEvent(action=SPLIT_ACTION,resource_id=self.job_id,detail={
                'version':1,'fingerprint':fingerprint,'pages':[p.page_number for p in pages],
                'reason':reason,'split_at':len(pages)//2}))

    def record_call(self, record):
        # 二次白名单过滤，来源正文和任意额外供应商字段都不能进入审计。
        allowed={'model','pages','reasoning_effort','max_tokens','error_code','elapsed_ms',
                 'prompt_tokens','completion_tokens','reasoning_tokens','total_tokens',
                 'request_bytes','retry_count','http_status','finish_reason'}
        detail={key:value for key,value in record.items() if key in allowed}
        with self.factory.begin() as db:
            job=self._job(db,lock=True)
            detail.update({'attempt':job.attempts+1,'phase':job.phase})
            db.add(m.AuditEvent(action=CALL_ACTION,resource_id=self.job_id,detail=detail))

    def save(self, fingerprint, pages, fields):
        with self.factory.begin() as db:
            self._job(db,lock=True)
            existing=self._receipt(db,fingerprint)
            if existing:
                # 不完整缓存不应被静默当作成功或覆盖；保留现场供诊断。
                raise DomainError('OCR_BATCH_CACHE_CONFLICT','识别批次缓存不完整',409)
            # 若完成凭据被保留策略清理，旧暂存候选不能阻断重新提取。
            db.execute(delete(m.DocumentExtractedField).where(
                m.DocumentExtractedField.job_id==self.job_id,
                m.DocumentExtractedField.row_key.startswith(fingerprint+':'),
                m.DocumentExtractedField.confirmed_by.is_(None)))
            refs=[]
            for index,field in enumerate(fields):
                row=m.DocumentExtractedField(id=str(uuid4()),job_id=self.job_id,scope=field.scope,
                    row_key=f'{fingerprint}:{index}',field_key=field.field_key,
                    raw_value=field.raw_value,normalized_value=field.normalized_value,
                    confidence=field.confidence,page_number=field.page_number,bbox=field.bbox,
                    source_block_ids=list(field.source_block_ids))
                db.add(row)
                refs.append({'id':row.id,'row_key':field.row_key})
            db.add(m.AuditEvent(action=ACTION,resource_id=self.job_id,
                detail={'fingerprint':fingerprint,'pages':[p.page_number for p in pages],'fields':refs}))
