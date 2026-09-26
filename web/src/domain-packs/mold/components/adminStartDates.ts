const ADMIN_START_DATE_FIELDS=new Set(['effective_date','customer_due_date'])

export function isAdminStartDateField(key:string){
 return ADMIN_START_DATE_FIELDS.has(key)
}

export function normalizeAdminStartDate(value:unknown){
 const text=String(value??'').trim()
 if(!text)return ''
 const match=text.match(/^(\d{4})\s*(?:[-/.年])\s*(\d{1,2})\s*(?:[-/.月])\s*(\d{1,2})/)
 if(!match)return ''
 const year=Number(match[1]),month=Number(match[2]),day=Number(match[3])
 const date=new Date(Date.UTC(year,month-1,day))
 if(date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day)return ''
 return `${match[1]}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`
}
