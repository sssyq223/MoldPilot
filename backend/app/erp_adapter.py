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
