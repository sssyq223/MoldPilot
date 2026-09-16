"""New adapter for existing ERP business APIs; no old Agent Tool/Skill imports."""
from urllib.parse import urlsplit
from decimal import Decimal
import json
import httpx
from cryptography.fernet import Fernet,InvalidToken
from .config import settings
from .errors import DomainError

CATEGORIES={'hardware':'hardware','hardware_standard':'hardware','wj':'hardware','五金':'hardware',
            'steel':'raw_material','steel_plate':'raw_material','steelplate':'raw_material','钢料':'raw_material',
            'outsource':'outsource','outsourcing':'outsource','entrust':'outsource','委外':'outsource','外协':'outsource'}
READ_FIELDS=['groupId','groupNo','requestId','requestNo','moldNo','materialCategory','supplierName','groupStatus',
             'decisionStatusLabel','totalQuantity','totalAmount','deliveryDate','canCreateOrder','orderCreateBlockReason',
             'orderId','orderNo','details','candidates','abnormalFlag','abnormalReason','frozenFlag','supplierId',
             'pricingMode','finalConfirmedPrice','pricingRemark']
PLAN_PROGRESS_FIELDS=['id','nodeId','nodeName','name','code','projectNo','projectCode','moldNo','moldNumber',
                      'status','statusLabel','planStartDate','planEndDate','plannedStart','plannedEnd',
                      'actualStartDate','actualEndDate','actualStartTime','actualEndTime','progress','percent',
                      'workOrderId','workOrderNo','orderNo','procedureName','processName','partNo','partName',
                      'ownerName','responsibleName','updatedAt','createTime','createdAt']


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
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise DomainError('ERP_NOT_CONFIGURED','管理员尚未配置有效 ERP 服务地址',503)
        if parts.scheme!='https' and not (parts.scheme=='http' and config.erp_allow_insecure_local and config.environment!='production'):
            raise DomainError('ERP_HTTPS_REQUIRED','ERP 连接需要 HTTPS；本地测试例外必须显式配置',503)
        self.client=httpx.Client(base_url=config.erp_base_url.rstrip('/')+'/',transport=transport,
            headers={'Authorization':'Bearer '+token} if token else {},trust_env=False,follow_redirects=False,
            timeout=httpx.Timeout(30,connect=5))

    def close(self):self.client.close()

    def request(self,method,path,**kwargs):
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
        if not isinstance(payload,dict) or payload.get('code')!=200:raise DomainError('ERP_BUSINESS_REJECTED','ERP 未接受本次请求，请核对原系统业务条件',409)
        return payload

    def info(self):return self.request('GET','getInfo')
    def groups(self,mold_no=None):return self.request('GET','purchase/decision/list',params={'moldNo':mold_no} if mold_no else {})['data']
    def group(self,group_id):return self.request('GET',f'purchase/decision/{int(group_id)}')['data']
    def create_order(self,group_id):return self.request('POST',f'purchase/decision/{int(group_id)}/create-order')['data']
    def project_nodes(self,mold_no=None,project_no=None):
        return self.request('GET','system/projectNode/list',params=plan_progress_params(mold_no,project_no))['data']
    def production_schedules(self,mold_no=None,project_no=None):
        return self.request('GET','system/productionSchedule/list',params=plan_progress_params(mold_no,project_no))['data']
    def plan_execution_progress(self,mold_no=None,project_no=None):
        from .db import now
        return normalize_plan_progress(self.project_nodes(mold_no,project_no),
            self.production_schedules(mold_no,project_no),now().isoformat())


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
