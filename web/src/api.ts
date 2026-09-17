export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('mold_csrf='))?.split('=')[1] ?? ''
  const response = await fetch(`/api${path}`, { credentials: 'same-origin', cache: 'no-store', ...options, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...options.headers } })
  const raw=await response.text()
  let body:any={}
  try{body=raw?JSON.parse(raw):{}}catch{}
  if (!response.ok) {
    const message=body.error?.message ?? (typeof body.detail==='string' ? (body.detail==='Not Found'?'接口不存在，请检查后端服务是否已更新':body.detail) : body.detail?.[0]?.msg) ?? `服务返回异常（${response.status}），请稍后重试`
    throw Object.assign(new Error(message), {status:response.status,code:body.error?.code})
  }
  return body
}
export const post = (path: string, body: unknown = {}) => api(path, { method: 'POST', body: JSON.stringify(body) })
export function shanghai(value: string | null | undefined) {
  if (!value) return '时间待确认'
  const date=new Date(value)
  if(!Number.isFinite(date.getTime()))return '时间待确认'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(date)
}
