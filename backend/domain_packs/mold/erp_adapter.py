"""New adapter for existing ERP business APIs; no old Agent Tool/Skill imports."""
from urllib.parse import urlsplit
from decimal import Decimal
import json
import httpx
from cryptography.fernet import Fernet,InvalidToken
from domain_packs.mold.config import settings
from agent_core.host_ports import host_ports
from agent_core.errors import DomainError

CATEGORIES={'hardware':'hardware','hardware_standard':'hardware','wj':'hardware','五金':'hardware',
            'steel':'raw_material','steel_plate':'raw_material','steelplate':'raw_material','钢料':'raw_material',
            'outsource':'outsource','outsourcing':'outsource','entrust':'outsource','委外':'outsource','外协':'outsource'}
READ_FIELDS=['groupId','groupNo','requestId','requestNo','projectNo','project_no','projectId','project_id','moldNo','materialCategory','supplierName','groupStatus','version','versionNo',
             'decisionStatusLabel','totalQuantity','totalAmount','deliveryDate','canCreateOrder','orderCreateBlockReason',
             'orderId','orderNo','details','candidates','abnormalFlag','abnormalReason','frozenFlag','supplierId',
             'pricingMode','finalConfirmedPrice','pricingRemark']
PLAN_PROGRESS_FIELDS=['id','nodeId','nodeName','name','code','projectNo','projectCode','moldNo','moldNumber',
                      'status','statusLabel','planStartDate','planEndDate','plannedStart','plannedEnd',
                      'actualStartDate','actualEndDate','actualStartTime','actualEndTime','progress','percent',
                      'workOrderId','workOrderNo','orderNo','procedureName','processName','partNo','partName',
                      'ownerName','responsibleName','updatedAt','createTime','createdAt']
OUTSOURCE_PROJECT_FIELDS=['id','project_no','projectNo','name','customer','status','outsource_type','mold_nos',
                          'required_ship_date','required_receive_date','outsource_due_at','flow_status','flowStatus',
                          'flow_status_label','flowStatusLabel','flow_phase','flowPhase']
OUTSOURCE_ORDER_FIELDS=['id','order_id','order_no','orderNo','project_id','project_no','projectNo','project_name',
                        'projectName','supplier_id','supplier_name','supplierName','status','stage','material_preparation',
                        'total_parts','completed_parts','overall_pct','parts','updated_at','updatedAt','created_at','createdAt']
OUTSOURCE_FULFILLMENT_FIELDS=['id','order_id','order_no','orderNo','status','stage','project_name','projectName',
                              'supplier_name','supplierName','material_preparation','updated_at','updatedAt']
OUTSOURCE_PRODUCT_SHIPMENT_FIELDS=['id','order_id','order_ids','order_nos','order_no','shipment_no',
                                   'logistics_company','tracking_no','ship_from','ship_to','shipped_at',
                                   'status','outsource_type','arrival_status','remark','created_at',
                                   'createdAt','qr_generated_at','qrGeneratedAt']
OUTSOURCE_PRODUCT_SHIPMENT_LINE_FIELDS=['id','order_part_id','part_no','part_name','mold_no',
                                         'is_end_operation','qty','arrival_confirmed_qty',
                                         'arrival_exception_qty','inbound_received_qty','receipt_status',
                                         'received_qty','problem_note']
OUTSOURCE_EXCEPTION_FIELDS=['id','order_id','order_no','orderNo','mold_code','moldCode','part_no','partNo',
                            'description','reporter_name','reporterName','new_deadline','newDeadline','is_delay',
                            'reporter_role','reporterRole','status','resolution','created_at','createdAt']
BUSINESS_MOLD_FIELDS=['project_code','mold_code','customer','due','part_count','order_count',
                      'op_count','completed_count','overall_progress']
MANUFACTURING_ORDER_FIELDS=['reference','gongdan_id','project_id','mold_id','operation_id','face_detail','resource_id',
                            'status','db_status','business_status','quantity','quantity_completed','startdate','enddate',
                            'batch','criticality','delay','schedule_type','is_outsourced','lastmodified']
WORK_REPORT_FIELDS=['id','order_ref','order_no','report_type','resource','resource_id','operation_id','operation_name',
                    'part_id','part_no','worker_name','work_hours','quantity','progress','start_time','end_time',
                    'reported_at','status','remark','created_at','updated_at']
FINANCE_CONTRACT_FIELDS=['id','contract_no','contractNo','contract_name','contractName','contract_amount',
                         'contractAmount','supplier_id','supplier_name','supplierName','status',
                         'effective_date','effectiveDate','latest_delivery_date','latestDeliveryDate']
FINANCE_PAYMENT_PLAN_FIELDS=['id','contract_id','contractId','contract_no','contractNo','payment_stage',
                             'paymentStage','stage_order','stageOrder','payment_ratio','paymentRatio',
                             'payment_amount','paymentAmount','payment_condition','paymentCondition',
                             'payment_trigger','paymentTrigger','remark','created_at','createdAt','updated_at','updatedAt']
FINANCE_PAYMENT_RECORD_FIELDS=['id','payment_no','paymentNo','contract_id','contractId','mold_id','moldId',
                               'payment_plan_id','paymentPlanId','payment_amount','paymentAmount','payment_date',
                               'paymentDate','payment_method','paymentMethod','payment_term','paymentTerm',
                               'invoice_status','invoiceStatus','invoice_no','invoiceNo','status','remark',
                               'created_at','createdAt','updated_at','updatedAt']
PROCUREMENT_ORDER_FIELDS=['id','order_no','orderNo','request_id','requestId','request_no','requestNo',
                          'contract_id','contractId','contract_no','contractNo','project_id','projectId',
                          'project_no','projectNo','mold_id','moldId','mold_no','moldNo','part_no','partNo',
                          'partner_id','partnerId','partner_name','partnerName','status','supplier_confirm_status',
                          'supplier_confirm_time','supplier_promised_delivery_date','expected_date','expectedDate',
                          'total_amount','material_category','fulfillment_status','fulfillmentStatus',
                          'execution_progress_percent','pending_delivery_quantity','pending_arrival_quantity',
                          'pending_inbound_quantity','delay_flag','current_stage_code','current_stage_label',
                          'processing_status','processing_status_label','ordered_quantity','delivery_quantity',
                          'arrival_confirmed_quantity','inbound_quantity','latest_expected_date','created_at','createdAt',
                          'updated_at','updatedAt']
PROCUREMENT_DELIVERY_FIELDS=['id','delivery_no','deliveryNo','purchase_order_id','purchaseOrderId',
                             'purchase_order_no','purchaseOrderNo','order_id','orderId','order_no','orderNo',
                             'mold_no','moldNo','part_no','partNo','partner_id','partnerId','partner_name',
                             'partnerName','delivery_qty','deliveryQty','arrival_confirmed_qty',
                             'arrivalConfirmedQty','received_qty','receivedQty','pending_arrival_qty',
                             'pendingArrivalQty','pending_inbound_qty','pendingInboundQty','status',
                             'delivery_date','deliveryDate','arrival_date','arrivalDate','remark','created_at','createdAt']
PROCUREMENT_INBOUND_FIELDS=['id','inbound_no','inboundNo','order_id','orderId','order_no','orderNo',
                            'mold_no','moldNo','part_no','partNo','partner_id','partnerId','partner_name',
                            'partnerName','inbound_type','status','inbound_date','inboundDate','warehouse',
                            'inspector_name','inspect_time','inspectTime','inspect_result','rejection_reason',
                            'rejection_delivery_no','rejectionDeliveryNo','total_quantity','total_amount',
                            'assignment_status','assignmentStatus','created_at','createdAt','updated_at','updatedAt']
PROCUREMENT_STOCK_FLOW_FIELDS=['id','flow_no','flowNo','flow_type','flowType','business_type','businessType',
                               'outbound_flag','outboundFlag','source_type','sourceType','source_id','sourceId',
                               'source_no','sourceNo','material_id','materialId','material_no','materialNo',
                               'material_name','materialName','quantity','supplier_id','supplierId','supplier_name',
                               'supplierName','order_id','orderId','order_no','orderNo','mold_no','moldNo',
                               'part_no','partNo','flow_time','flowTime','flow_type_label','flowTypeLabel',
                               'business_type_label','businessTypeLabel','created_at','createdAt']
QUALITY_INSPECTION_FIELDS=['id','inspection_no','inspectionNo','inbound_id','inboundId','inbound_no','inboundNo',
                           'source_type','sourceType','operation_ref','operationRef','operation_name','operationName',
                           'process_name','processName','part_no','partNo','mold_no','moldNo','operation_quantity',
                           'operationQuantity','order_id','orderId','order_no','orderNo','project_no','projectNo',
                           'partner_id','partnerId','partner_name','partnerName','warehouse','status','inspection_type',
                           'inspectionType','result','handling_action','handlingAction','inspector_id','inspectorId',
                           'inspector_name','inspectorName','claimed_at','claimedAt','completed_at','completedAt',
                           'remark','created_at','createdAt','updated_at','updatedAt']
QUALITY_INSPECTION_DETAIL_FIELDS=['id','task_id','taskId','inbound_id','inboundId','inbound_detail_id',
                                  'inboundDetailId','material_id','materialId','material_no','materialNo',
                                  'material_name','materialName','specification','unit','inbound_qty','inboundQty',
                                  'sample_qty','sampleQty','qualified_qty','qualifiedQty','unqualified_qty',
                                  'unqualifiedQty','result','reason','handling_action','handlingAction','remark',
                                  'created_at','createdAt','updated_at','updatedAt']


def cipher():
    try:return Fernet(settings().credential_encryption_key.encode())
    except (ValueError,TypeError):raise DomainError('ERP_KEY_REQUIRED','管理员尚未配置 ERP 凭据加密密钥',503) from None


def encrypt(token):return cipher().encrypt(token.encode()).decode()
def decrypt(value):
    if not value:raise DomainError('ERP_LOGIN_REQUIRED','请先验证本人的 ERP 账号',401)
    try:return cipher().decrypt(value.encode()).decode()
    except InvalidToken:raise DomainError('ERP_LOGIN_REQUIRED','ERP 连接凭据已失效，请重新验证',401) from None


class ERPClient:
    def __init__(self,token=None,transport=None):
        config=settings();parts=urlsplit(config.erp_base_url)
        environment=host_ports().settings().environment
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise DomainError('ERP_NOT_CONFIGURED','管理员尚未配置有效 ERP 服务地址',503)
        if parts.scheme!='https' and not (parts.scheme=='http' and config.erp_allow_insecure_local and environment!='production'):
            raise DomainError('ERP_HTTPS_REQUIRED','ERP 连接需要 HTTPS；本地测试例外必须显式配置',503)
        self.client=httpx.Client(base_url=config.erp_base_url.rstrip('/')+'/',transport=transport,
            headers={'Authorization':'Bearer '+token} if token else {},trust_env=False,follow_redirects=False,
            timeout=httpx.Timeout(30,connect=5))

    def close(self):self.client.close()

    def request(self,method,path,accept_unwrapped=False,**kwargs):
        # Callers provide code-registered paths only; no browser/LLM arbitrary URL input.
        try:
            with self.client.stream(method,path.lstrip('/'),**kwargs) as response:
                if response.status_code in {401,403}:raise DomainError('ERP_FORBIDDEN','ERP 登录失效或原系统权限不足',403)
                if response.status_code>=500:raise DomainError('ERP_OUTCOME_UNKNOWN','ERP 服务异常，执行结果需要核对',502)
                if response.status_code!=200:raise DomainError('ERP_PROTOCOL_ERROR','ERP 未返回有效业务响应',502)
                parts=[];size=0
                for part in response.iter_bytes():
                    size+=len(part)
                    if size>2_000_000:raise DomainError('ERP_RESPONSE_LIMIT','ERP 返回资料过大，请缩小查询范围',502)
                    parts.append(part)
            payload=json.loads(b''.join(parts),parse_float=Decimal)
        except httpx.HTTPError:raise DomainError('ERP_OUTCOME_UNKNOWN','ERP 连接中断或超时；不会自动重复正式操作',502) from None
        except (ValueError,UnicodeError):raise DomainError('ERP_PROTOCOL_ERROR','ERP 响应格式不合法',502) from None
        if not isinstance(payload,dict) or (payload.get('code')!=200 and not (accept_unwrapped and payload.get('code') is None)):
            raise DomainError('ERP_BUSINESS_REJECTED','ERP 未接受本次请求，请核对原系统业务条件',409)
        return payload

    def info(self):return self.request('GET','getInfo')
    def groups(self,mold_no=None):return self.request('GET','purchase/decision/list',params={'moldNo':mold_no} if mold_no else {})['data']
    def group(self,group_id):return self.request('GET',f'purchase/decision/{int(group_id)}')['data']
    def purchase_decision_groups(self, mold_no=None, project_no=None, material_category=None):
        """Read bounded ERP purchase-decision groups for procurement skills.

        The adapter keeps the endpoint and filters code-registered.  Callers
        never provide an arbitrary URL or a raw query string.
        """
        params = {
            key: value for key, value in {
                'moldNo': mold_no,
                'projectNo': project_no,
                'materialCategory': material_category,
                'pageNum': 1,
                'pageSize': 200,
            }.items() if value not in (None, '')
        }
        payload = self.request('GET', 'purchase/decision/list', params=params)
        rows = payload.get('data') or payload.get('rows') or payload
        if isinstance(rows, dict):
            rows = rows.get('rows') or rows.get('records') or rows.get('items') or []
        if not isinstance(rows, list):
            raise DomainError('ERP_PROTOCOL_ERROR', 'ERP 采购决策列表响应格式不合法', 502)
        cards = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            card = review_material(row)
            native_id = row.get('groupId') or row.get('id') or row.get('groupNo')
            card.update({
                'source_system': 'ERP',
                'source_endpoint': 'purchase/decision/list',
                'source_ref': f'purchase/decision/list:{native_id}',
                'source_as_of': host_ports().now().isoformat(),
            })
            cards.append(card)
        return cards[:200]

    def quote_approval_preview(self, group_id):
        """Read the ERP quote-approval preview for one purchase group."""
        return normalized(self.request(
            'GET', f'purchase/decision/{int(group_id)}/quote-approval-preview'
        ).get('data') or {})

    def submit_hardware_quote(self, group_id, payload):
        """Submit a confirmed hardware quote to the ERP business API."""
        return normalized(self.request(
            'POST', f'purchase/decision/{int(group_id)}/hardware-quote', json=payload
        ).get('data') or {})

    def confirm_purchase_decision(self, group_id, payload=None):
        """Confirm a purchase decision after MoldPilot human confirmation."""
        return normalized(self.request(
            'POST', f'purchase/decision/{int(group_id)}/confirm', json=payload or {}
        ).get('data') or {})

    def create_purchase_order(self, group_id):
        """Create the ERP purchase order for an already confirmed group."""
        return normalized(self.create_order(group_id) or {})
    def preview_split_mode(self, payload):
        """Preview steel split/no-split feasibility without committing a group."""
        return normalized(self.request(
            'POST', 'purchase/workbench/split/mode-preview', json=payload
        ).get('data') or {})

    def confirm_split_workbench(self, payload):
        """Confirm the ERP split workbench route after human confirmation."""
        return normalized(self.request(
            'POST', 'purchase/workbench/split/confirm', json=payload
        ).get('data') or {})

    def supplier_delivery_modifiable_list(self, params=None):
        payload = self.request(
            'GET', 'purchase/supplier-delivery/modifiable-list',
            params={**(params or {}), 'pageNum': 1, 'pageSize': 200},
        )
        return normalized(payload.get('rows') or payload.get('data') or [])

    def supplier_delivery_detail(self, delivery_id):
        return normalized(self.request(
            'GET', f'purchase/supplier-delivery/{int(delivery_id)}'
        ).get('data') or {})

    def supplier_delivery_modify_preview(self, delivery_id):
        return normalized(self.request(
            'GET', f'purchase/supplier-delivery/{int(delivery_id)}/modify-preview'
        ).get('data') or {})

    def create_supplier_delivery_modify_request(self, delivery_id, payload):
        return normalized(self.request(
            'POST', f'purchase/supplier-delivery/{int(delivery_id)}/modify-request',
            json=payload,
        ).get('data') or {})
    def accounting_checklist_reference(self, file_id):
        return self.request('GET', f'production/preplanOrder/cost-sheet/directory/{int(file_id)}/binding-reference')['data']
    def create_order(self,group_id):return self.request('POST',f'purchase/decision/{int(group_id)}/create-order')['data']
    def project_nodes(self,mold_no=None,project_no=None):
        # The ERP's authoritative project-node endpoint is keyed by the
        # numeric project id.  The old adapter passed projectNo/moldNo to the
        # paginated node list, which the ERP silently ignores and returns an
        # envelope that cannot be scoped to this project.  Resolve the code
        # first, then read the project-scoped endpoint without mutating ERP.
        if project_no:
            project_payload = self.request('GET', 'system/project/list', params={
                'projectNo': project_no,
                'pageNum': 1,
                'pageSize': 50,
            })
            project_rows = _payload_rows(project_payload)
            if not _payload_has_collection(project_payload):
                raise DomainError('ERP_PROTOCOL_ERROR', 'ERP 项目列表响应缺少 data/rows 字段', 502)
            wanted = str(project_no).strip().casefold()
            project = next((row for row in project_rows if isinstance(row, dict) and str(
                row.get('projectNo') or row.get('project_no') or ''
            ).strip().casefold() == wanted), None)
            if project is None:
                raise DomainError('ERP_PROJECT_NOT_FOUND', f'ERP 未找到项目 {project_no}', 404)
            project_id = project.get('id') or project.get('projectId') or project.get('project_id')
            if project_id in (None, ''):
                raise DomainError('ERP_PROTOCOL_ERROR', 'ERP 项目响应缺少项目 id', 502)
            payload = self.request('GET', f'system/projectNode/project/{int(project_id)}')
            if not _payload_has_collection(payload):
                raise DomainError('ERP_PROTOCOL_ERROR', 'ERP 项目节点响应缺少 data/rows 字段', 502)
            return _payload_rows(payload)

        # Keep a bounded compatibility path for callers that only have an
        # ERP mold number.  It is still read-only and accepts all envelopes
        # used by the ERP, while malformed success payloads remain explicit.
        payload = self.request('GET', 'system/projectNode/list', params=plan_progress_params(mold_no, None))
        if not _payload_has_collection(payload):
            raise DomainError('ERP_PROTOCOL_ERROR', 'ERP 项目节点响应缺少 data/rows 字段', 502)
        return _payload_rows(payload)
    def production_schedules(self,mold_no=None,project_no=None):
        payload = self.request('GET','system/productionSchedule/list',params=plan_progress_params(mold_no,project_no))
        if not _payload_has_collection(payload):
            raise DomainError('ERP_PROTOCOL_ERROR','ERP 生产排程响应缺少 data/rows 字段',502)
        return _payload_rows(payload)
    def plan_execution_progress(self,mold_no=None,project_no=None):
        return normalize_plan_progress(self.project_nodes(mold_no,project_no),
            self.production_schedules(mold_no,project_no),host_ports().now().isoformat())

    def business_molds(self, project_no=None, mold_no=None):
        """Read ERP project/mold candidates without mirroring or writes."""
        payload=self.request(
            'GET',
            'scheduling/api/business/molds/',
            accept_unwrapped=True,
            params={'project':project_no or '', 'mold':mold_no or ''},
        )
        rows=payload.get('molds') if isinstance(payload,dict) else None
        if not isinstance(rows,list):
            rows=_payload_rows(payload)
        cards=[]
        for row in rows:
            if not isinstance(row,dict):
                continue
            card={k:normalized(row[k]) for k in BUSINESS_MOLD_FIELDS if k in row}
            native_id=':'.join(str(value) for value in (card.get('project_code'),card.get('mold_code')) if value not in (None,''))
            if native_id:
                card['source_ref']='scheduling/api/business/molds/:'+native_id
            card['source_system']='ERP'
            card['source_endpoint']='scheduling/api/business/molds/'
            cards.append(card)
        return normalized({
            'records':cards[:200],
            'totals':{'molds':len(cards)},
            'as_of':host_ports().now().isoformat(),
            'source_system':'ERP',
            'limitations':['ERP 业务模具候选仅作原系统事实引用；Agent 不创建、修改或自动关联 ERP 模具。'],
        })

    def quality_inspection_context(self,mold_no=None,project_no=None,order_nos=()):
        """Read ERP quality inspection tasks that can be scoped to this project."""
        try:
            payload=self.request('GET','quality/inspection/list',params={'pageNum':1,'pageSize':200})
        except DomainError as error:
            return normalized({
                'inspection_records':[],
                'totals':{
                    'inspections':0,'open_inspections':0,'completed_inspections':0,
                    'failed_inspections':0,'qualified_inspections':0,
                },
                'status':error.code,
                'as_of':host_ports().now().isoformat(),
                'source_system':'ERP',
                'limitations':[f'ERP 质检查询未完成：{error.message}'],
            })
        rows=_quality_cards(payload.get('rows') or payload.get('data') or payload,
                            'quality/inspection/list')
        wanted_orders={str(value).strip() for value in (order_nos or ()) if str(value).strip()}
        wanted_mold=str(mold_no or '').strip()
        wanted_project=str(project_no or '').strip()

        def value(row,*names):
            for name in names:
                item=row.get(name)
                if item not in (None,''):
                    return str(item).strip()
            return ''

        scoped=[]
        for row in rows:
            if row.get('is_deleted') in (1,'1',True) or row.get('isDeleted') in (1,'1',True):
                continue
            candidates=[
                value(row,'mold_no','moldNo'),
                value(row,'project_no','projectNo'),
                value(row,'order_no','orderNo'),
                value(row,'inbound_no','inboundNo'),
            ]
            if any(candidate and (
                candidate == wanted_mold or candidate == wanted_project or candidate in wanted_orders
            ) for candidate in candidates):
                scoped.append(row)
        open_statuses={'pending','inspecting'}
        completed_statuses={'completed','partial','reject_return'}
        failed_results={'unqualified','partial'}
        completed=[row for row in scoped if str(row.get('status') or '').lower() in completed_statuses]
        failed=[row for row in scoped if str(row.get('result') or '').lower() in failed_results
                or str(row.get('status') or '').lower() in {'partial','reject_return'}]
        return normalized({
            'inspection_records':scoped[:200],
            'totals':{
                'inspections':len(scoped),
                'open_inspections':sum(1 for row in scoped if str(row.get('status') or '').lower() in open_statuses),
                'completed_inspections':len(completed),
                'failed_inspections':len(failed),
                'qualified_inspections':sum(1 for row in scoped if str(row.get('result') or '').lower() == 'qualified'),
            },
            'as_of':host_ports().now().isoformat(),
            'source_system':'ERP',
            'status':'RESOLVED',
            'limitations':['ERP 质检任务及结果仅作原系统事实引用；Agent 不领取、不提交、不修改 ERP 质检结论。'],
        })

    def outsource_execution_context(self,mold_no=None,project_no=None):
        """Read the ERP's existing outsource execution facts without mirroring or writes."""
        project_payload=self.request('GET','entrust/project/list',params={
            **plan_progress_params(mold_no,project_no),'pageNum':1,'pageSize':100})
        query_value=project_no or mold_no
        production_payload=self.request('GET','entrust/production/list',params={'q':query_value} if query_value else {})
        project_rows=_safe_cards(project_payload.get('data'),OUTSOURCE_PROJECT_FIELDS,'entrust/project/list')
        production_rows=_safe_cards(production_payload.get('data'),OUTSOURCE_ORDER_FIELDS,'entrust/production/list')
        order_ids=[]
        order_nos=set()
        for row in production_rows:
            order_id=row.get('order_id') or row.get('id')
            if order_id is not None:
                try: order_ids.append(int(order_id))
                except (TypeError,ValueError): pass
            if row.get('order_no') or row.get('orderNo'): order_nos.add(str(row.get('order_no') or row.get('orderNo')))
        fulfillment_rows=_safe_cards(self.request('GET','entrust/fulfillment/orders')['data'],OUTSOURCE_FULFILLMENT_FIELDS,'entrust/fulfillment/orders')
        if order_ids or order_nos:
            fulfillment_rows=[row for row in fulfillment_rows if row.get('id') in order_ids or row.get('order_id') in order_ids or str(row.get('order_no') or row.get('orderNo') or '') in order_nos]
        else:
            fulfillment_rows=[]
        product_shipment_rows=[]
        for order_id in list(dict.fromkeys(order_ids))[:20]:
            payload=self.request(
                'GET',
                'entrust/fulfillment/product-shipment/list',
                params={'order_id':order_id},
            )
            product_shipment_rows.extend(
                _product_shipment_cards(
                    payload.get('data') or payload,
                    'entrust/fulfillment/product-shipment/list',
                )
            )
        exception_rows=[]
        for order_id in list(dict.fromkeys(order_ids))[:20]:
            payload=self.request('GET','entrust/exception/list',params={'order_id':order_id,'page_num':1,'page_size':100})
            exception_rows.extend(_safe_cards(payload.get('data') or payload,OUTSOURCE_EXCEPTION_FIELDS,'entrust/exception/list'))
        return normalized({'project_records':project_rows[:100],'production_records':production_rows[:100],
            'fulfillment_records':fulfillment_rows[:100],
            'product_shipment_records':product_shipment_rows[:200],
            'exception_records':exception_rows[:100],
            'totals':{'projects':len(project_rows),'production_orders':len(production_rows),
                      'fulfillment_orders':len(fulfillment_rows),
                      'product_shipments':len(product_shipment_rows),
                      'exceptions':len(exception_rows)},
            'as_of':host_ports().now().isoformat(),'source_system':'ERP',
            'limitations':['ERP 委外项目、生产、履约和异常仅作原系统事实引用；Agent 不创建工单、不上报进度、不改阶段、不处理异常。']})

    def manufacturing_execution_context(self,mold_no=None,project_no=None):
        """Read ERP internal manufacturing orders and work reports; no writes or mirroring."""
        order_payload=self.request('GET','scheduling/api/manufacturing-orders/',accept_unwrapped=True,params={
            'project':project_no or '', 'mold':mold_no or '', 'page':1, 'page_size':200})
        report_payload=self.request('GET','scheduling/api/work-reports/',accept_unwrapped=True,params={
            'order_ref':project_no or mold_no or '', 'limit':500})
        work_order_report_payload=self.request('GET','scheduling/api/work-order-reports/',accept_unwrapped=True,params={
            'order_no':project_no or mold_no or '', 'page':1, 'page_size':500})
        orders=_safe_cards(order_payload.get('rows') or order_payload.get('data') or order_payload,
                           MANUFACTURING_ORDER_FIELDS,'scheduling/api/manufacturing-orders/')
        reports=_safe_cards(report_payload.get('rows') or report_payload.get('data') or report_payload,
                            WORK_REPORT_FIELDS,'scheduling/api/work-reports/')
        work_order_reports=_safe_cards(work_order_report_payload.get('rows') or work_order_report_payload.get('data') or work_order_report_payload,
                                       WORK_REPORT_FIELDS,'scheduling/api/work-order-reports/')
        quality=self.quality_inspection_context(mold_no=mold_no,project_no=project_no)
        return normalized({'manufacturing_orders':orders[:200],'work_reports':reports[:500],
            'work_order_reports':work_order_reports[:500],
            'quality_inspections':quality.get('inspection_records') or [],
            'quality_totals':quality.get('totals') or {},
            'quality_status':quality.get('status'),
            'totals':{'manufacturing_orders':len(orders),'work_reports':len(reports),
                      'work_order_reports':len(work_order_reports),
                      'quality_inspections':(quality.get('totals') or {}).get('inspections',0)},
            'as_of':host_ports().now().isoformat(),'source_system':'ERP',
            'limitations':['ERP 制造工单、报工和质检仅作原系统事实引用；Agent 不创建工单、不登记报工、不修改 ERP 状态或质检结论。'] +
                         (quality.get('limitations') or [])})

    def contract_finance_context(self, contract_numbers=()):
        """Read ERP contract payment plans and records matched by contract number."""
        wanted = list(dict.fromkeys(
            str(value).strip() for value in (contract_numbers or ()) if str(value).strip()
        ))
        if not wanted:
            return normalized({
                'contract_records': [], 'payment_plan_records': [], 'payment_records': [],
                'totals': {'contracts': 0, 'payment_plans': 0, 'payment_records': 0},
                'as_of': host_ports().now().isoformat(), 'source_system': 'ERP',
                'limitations': ['未提供可核对的 Agent 合同号，未读取 ERP 合同付款资料。'],
            })
        exact = []
        for number in wanted[:50]:
            payload = self.request('GET', 'system/contract/list', params={
                'contractNo': number, 'pageNum': 1, 'pageSize': 100,
            })
            rows = _safe_cards(
                payload.get('rows') or payload.get('data') or payload,
                FINANCE_CONTRACT_FIELDS,
                'system/contract/list',
            )
            exact.extend(
                row for row in rows
                if str(row.get('contract_no') or row.get('contractNo') or '').strip() == number
            )
        by_id = {}
        for row in exact:
            key = row.get('id') or row.get('contract_no') or row.get('contractNo')
            by_id[str(key)] = row
        contract_rows = list(by_id.values())
        contract_rows = contract_rows[:50]
        payment_plan_rows = []
        payment_record_rows = []
        for contract in contract_rows[:20]:
            contract_id = contract.get('id')
            if contract_id is None:
                continue
            try:
                contract_id = int(contract_id)
            except (TypeError, ValueError):
                continue
            plan_payload = self.request('GET', f'system/contract/paymentPlan/list/{contract_id}')
            record_payload = self.request('GET', f'system/contract/paymentRecord/list/{contract_id}')
            payment_plan_rows.extend(_safe_cards(
                plan_payload.get('data') or plan_payload,
                FINANCE_PAYMENT_PLAN_FIELDS,
                f'system/contract/paymentPlan/list/{contract_id}',
            ))
            payment_record_rows.extend(_safe_cards(
                record_payload.get('data') or record_payload,
                FINANCE_PAYMENT_RECORD_FIELDS,
                f'system/contract/paymentRecord/list/{contract_id}',
            ))
        return normalized({
            'contract_records': contract_rows[:50],
            'payment_plan_records': payment_plan_rows[:500],
            'payment_records': payment_record_rows[:500],
            'totals': {
                'contracts': len(contract_rows),
                'payment_plans': len(payment_plan_rows),
                'payment_records': len(payment_record_rows),
            },
            'as_of': host_ports().now().isoformat(),
            'source_system': 'ERP',
            'limitations': [
                'ERP 合同付款计划和付款记录仅作原系统事实引用；Agent 不创建、修改或确认 ERP 合同、付款计划、付款记录或发票。',
            ],
        })

    def procurement_execution_context(self, mold_no=None, project_no=None):
        """Read ERP procurement, supplier delivery, inbound and stock facts."""
        if not mold_no and not project_no:
            return normalized({
                'purchase_order_records': [], 'supplier_delivery_records': [],
                'inbound_records': [], 'stock_flow_records': [], 'quality_inspection_records': [],
                'quality_totals': {'inspections': 0, 'open_inspections': 0, 'completed_inspections': 0,
                                   'failed_inspections': 0, 'qualified_inspections': 0},
                'quality_status': 'NOT_REQUESTED',
                'totals': {'purchase_orders': 0, 'supplier_deliveries': 0, 'inbounds': 0, 'stock_flows': 0,
                           'quality_inspections': 0},
                'as_of': host_ports().now().isoformat(), 'source_system': 'ERP',
                'limitations': ['未提供可核对的项目号或模具号，未读取 ERP 采购执行资料。'],
            })
        base_params = {
            'projectNo': project_no or '',
            'moldNo': mold_no or '',
            'pageNum': 1,
            'pageSize': 200,
        }
        order_payload = self.request('GET', 'purchase/order/list', params=base_params)
        delivery_payload = self.request('GET', 'purchase/supplier-delivery/list', params=base_params)
        inbound_payload = self.request('GET', 'material/inbound/list', params={
            'moldNo': mold_no or '', 'orderNo': '', 'pageNum': 1, 'pageSize': 200,
            'includeSupplierRejections': 'true',
        })
        stock_payload = self.request('GET', 'material/stock-flow/list', params={
            'moldNo': mold_no or '', 'orderNo': '', 'pageNum': 1, 'pageSize': 500,
        })
        orders = _safe_cards(
            order_payload.get('rows') or order_payload.get('data') or order_payload,
            PROCUREMENT_ORDER_FIELDS,
            'purchase/order/list',
        )
        scoped_orders = [
            row for row in orders
            if (
                (mold_no and str(mold_no).strip() in {
                    str(row.get('mold_no') or row.get('moldNo') or '').strip(),
                })
                or (project_no and str(project_no).strip() in {
                    str(row.get('project_no') or row.get('projectNo') or '').strip(),
                    str(row.get('project_id') or row.get('projectId') or '').strip(),
                })
            )
        ]
        order_nos = {
            str(row.get('order_no') or row.get('orderNo')).strip()
            for row in scoped_orders
            if str(row.get('order_no') or row.get('orderNo') or '').strip()
        }

        def relevant(row, *, include_project=False):
            if not isinstance(row, dict):
                return False
            values = {
                str(row.get('mold_no') or row.get('moldNo') or '').strip(),
                str(row.get('order_no') or row.get('orderNo') or '').strip(),
                str(row.get('purchase_order_no') or row.get('purchaseOrderNo') or '').strip(),
                str(row.get('source_no') or row.get('sourceNo') or '').strip(),
            }
            if include_project:
                values.update({
                    str(row.get('project_no') or row.get('projectNo') or '').strip(),
                    str(row.get('project_id') or row.get('projectId') or '').strip(),
                })
            return bool(
                (mold_no and str(mold_no).strip() in values)
                or (project_no and str(project_no).strip() in values)
                or values.intersection(order_nos)
            )

        deliveries = [
            row for row in _safe_cards(
                delivery_payload.get('rows') or delivery_payload.get('data') or delivery_payload,
                PROCUREMENT_DELIVERY_FIELDS,
                'purchase/supplier-delivery/list',
            )
            if relevant(row)
        ]
        inbounds = [
            row for row in _safe_cards(
                inbound_payload.get('rows') or inbound_payload.get('data') or inbound_payload,
                PROCUREMENT_INBOUND_FIELDS,
                'material/inbound/list',
            )
            if relevant(row)
        ]
        stock_flows = [
            row for row in _safe_cards(
                stock_payload.get('rows') or stock_payload.get('data') or stock_payload,
                PROCUREMENT_STOCK_FLOW_FIELDS,
                'material/stock-flow/list',
            )
            if relevant(row)
        ]
        quality = self.quality_inspection_context(
            mold_no=mold_no,
            project_no=project_no,
            order_nos=order_nos,
        )
        return normalized({
            'purchase_order_records': scoped_orders[:200],
            'supplier_delivery_records': deliveries[:500],
            'inbound_records': inbounds[:500],
            'stock_flow_records': stock_flows[:500],
            'quality_inspection_records': quality.get('inspection_records') or [],
            'quality_totals': quality.get('totals') or {},
            'quality_status': quality.get('status'),
            'totals': {
                'purchase_orders': len(scoped_orders),
                'supplier_deliveries': len(deliveries),
                'inbounds': len(inbounds),
                'stock_flows': len(stock_flows),
                'quality_inspections': (quality.get('totals') or {}).get('inspections', 0),
            },
            'as_of': host_ports().now().isoformat(),
            'source_system': 'ERP',
            'limitations': [
                'ERP 采购订单、供应商发货、入库、库存流水和质检仅作原系统事实引用；Agent 不创建订单、不确认收货、不修改库存、质检结论或 ERP 状态。',
            ] + (quality.get('limitations') or []),
        })


def verified_identity(client,expected_id,permission=None):
    info=client.info();user=info.get('user') or {}
    if str(user.get('userId'))!=expected_id:raise DomainError('ERP_IDENTITY_MISMATCH','ERP 登录身份与管理员绑定的人员不一致',403)
    if permission and permission not in info.get('permissions',[]) and '*:*:*' not in info.get('permissions',[]):
        raise DomainError('ERP_FORBIDDEN','本人在 ERP 中没有此操作权限',403)
    return info


def object_scope(row):
    category=CATEGORIES.get(str(row.get('materialCategory') or row.get('bizType') or '').strip().lower())
    mold=row.get('moldNo')
    if not category or not mold:raise DomainError('ERP_SCOPE_UNRESOLVED','ERP 记录缺少可核对的模号或责任域，暂不开放此记录',403)
    return {'project_id':'erp:mold:'+str(mold),'category':category}


def normalized(row):
    # Stable DTO conversion, not a database mirror. Preserve decimal money as strings.
    if isinstance(row,Decimal):return str(row)
    if isinstance(row,dict):return {k:normalized(v) for k,v in row.items()}
    if isinstance(row,list):return [normalized(v) for v in row]
    return row


def review_material(row):return normalized({k:row[k] for k in READ_FIELDS if k in row})


def _payload_rows(data):
    if isinstance(data,list):return data
    if not isinstance(data,dict):return []
    for key in ('rows','records','items','list','data'):
        value=data.get(key)
        if isinstance(value,list):return value
        if isinstance(value,dict):
            nested=_payload_rows(value)
            if nested:return nested
    return []


def _payload_has_collection(data):
    """Return whether an ERP success envelope explicitly contains a row list.

    ``_payload_rows`` intentionally collapses an empty nested list to ``[]``;
    protocol validation needs to distinguish a valid empty result from a
    success message that contains no collection at all.
    """
    if isinstance(data, list):
        return True
    if not isinstance(data, dict):
        return False
    for key in ('rows', 'records', 'items', 'list', 'data'):
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            return True
        if isinstance(value, dict) and _payload_has_collection(value):
            return True
    return False


def _progress_row(row,source):
    if not isinstance(row,dict):return None
    card={k:normalized(row[k]) for k in PLAN_PROGRESS_FIELDS if k in row}
    native_id=card.get('id') or card.get('nodeId') or card.get('workOrderId') or card.get('workOrderNo') or card.get('orderNo')
    if native_id is not None:card['source_ref']=source+':'+str(native_id)
    card['source_system']='ERP';card['source_endpoint']=source
    return card


def normalize_plan_progress(project_nodes=None,production_schedules=None,as_of=None):
    nodes=[card for card in (_progress_row(row,'system/projectNode/list') for row in _payload_rows(project_nodes)) if card]
    schedules=[card for card in (_progress_row(row,'system/productionSchedule/list') for row in _payload_rows(production_schedules)) if card]
    return normalized({'project_nodes':nodes[:100],'production_schedules':schedules[:100],
        'totals':{'project_nodes':len(nodes),'production_schedules':len(schedules)},
        'as_of':as_of,'source_system':'ERP',
        'limitations':['ERP 进度只作为原系统事实引用返回，不写入 Agent 计划任务，不替代计划变更审批或部门确认。']})


def plan_progress_params(mold_no=None,project_no=None):
    params={}
    if mold_no:params['moldNo']=mold_no
    if project_no:params['projectNo']=project_no
    return params


def _safe_cards(payload,fields,source):
    rows=[]
    for row in _payload_rows(payload):
        if not isinstance(row,dict): continue
        card={k:normalized(row[k]) for k in fields if k in row}
        native_id=(card.get('id') or card.get('order_id') or card.get('order_no') or card.get('project_no')
                   or card.get('reference') or card.get('order_ref') or card.get('work_order_no'))
        if native_id is not None: card['source_ref']=source+':'+str(native_id)
        card['source_system']='ERP';card['source_endpoint']=source
        rows.append(card)
    return rows


def _product_shipment_cards(payload, source):
    rows=[]
    for row in _payload_rows(payload):
        if not isinstance(row,dict):
            continue
        card={k:normalized(row[k]) for k in OUTSOURCE_PRODUCT_SHIPMENT_FIELDS if k in row}
        raw_lines=row.get('lines')
        if isinstance(raw_lines,list):
            card['lines']=[
                {k:normalized(item[k]) for k in OUTSOURCE_PRODUCT_SHIPMENT_LINE_FIELDS if k in item}
                for item in raw_lines[:200] if isinstance(item,dict)
            ]
        native_id=card.get('id') or card.get('shipment_no') or card.get('shipmentNo')
        if native_id is not None:
            card['source_ref']=source+':'+str(native_id)
        card['source_system']='ERP'
        card['source_endpoint']=source
        rows.append(card)
    return rows


def _quality_cards(payload,source):
    rows=[]
    for row in _payload_rows(payload):
        if not isinstance(row,dict):
            continue
        card={k:normalized(row[k]) for k in QUALITY_INSPECTION_FIELDS if k in row}
        details=row.get('details')
        if isinstance(details,list):
            card['details']=[
                {k:normalized(item[k]) for k in QUALITY_INSPECTION_DETAIL_FIELDS if k in item}
                for item in details[:100] if isinstance(item,dict)
            ]
        raw_remark=card.get('remark')
        if isinstance(raw_remark,str) and raw_remark.strip():
            try:
                metadata=json.loads(raw_remark)
            except (TypeError,ValueError):
                metadata={}
            if isinstance(metadata,dict):
                for key in ('projectNo','moldNo','operationRef','operationName','processName','partNo','workOrderNo'):
                    if key in metadata and key not in card and metadata[key] not in (None,''):
                        card[key]=normalized(metadata[key])
        native_id=(card.get('id') or card.get('inspection_no') or card.get('inspectionNo')
                   or card.get('inbound_no') or card.get('inboundNo'))
        if native_id is not None:
            card['source_ref']=source+':'+str(native_id)
        card['source_system']='ERP'
        card['source_endpoint']=source
        rows.append(card)
    return rows
