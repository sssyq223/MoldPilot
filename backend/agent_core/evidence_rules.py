"""Typed material rules, independent of file parsing and never executing source code.

Data is supplied by the material service after authorization and confirmation. The
simulator can supply synthetic data; it does not make that data business evidence.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import re
from .errors import DomainError

TYPES={'text','decimal','boolean','date','money'}
COMPARISONS={'eq','ne','gt','gte','lt','lte','in','contains'}
KEY=re.compile(r'^[a-z][a-z0-9_]{0,63}$')
MAX_ROWS=5000

def invalid(message):raise DomainError('INVALID_MATERIAL_RULE',message)

def fields_index(fields):
    if not isinstance(fields,list) or len(fields)>100:invalid('每份资料最多配置100个字段')
    index={}
    for f in fields:
        if (not isinstance(f,dict) or not {'key','label','type'}<=set(f)
                or set(f)-{'key','label','type','unit','currency_field'}):invalid('资料字段结构无效')
        key=f['key']
        if not isinstance(key,str) or not KEY.fullmatch(key) or key in index:invalid('资料字段标识无效或重复')
        if not isinstance(f['label'],str) or not 1<=len(f['label'].strip())<=100:invalid('字段必须设置中文显示名称')
        if not isinstance(f['type'],str) or f['type'] not in TYPES:invalid('字段类型未登记')
        if 'unit' in f and (not isinstance(f['unit'],str) or not 1<=len(f['unit'])<=30):invalid('字段单位无效')
        if f['type']!='money' and 'currency_field' in f:invalid('只有金额字段可以关联币种')
        index[key]=f
    for f in index.values():
        if f['type']=='money':
            currency=f.get('currency_field')
            if not isinstance(currency,str) or currency not in index or index[currency]['type']!='text':invalid('金额字段必须关联同层的币种文本字段')
    return index

def validate_contract(contract):
    if not isinstance(contract,dict) or set(contract)!={'fields','tables'}:invalid('资料契约必须包含表头字段和明细表')
    headers=fields_index(contract['fields'])
    if not isinstance(contract['tables'],list) or len(contract['tables'])>20:invalid('资料明细表数量无效')
    tables={}
    for table in contract['tables']:
        if not isinstance(table,dict) or set(table)!={'key','label','fields'}:invalid('明细表结构无效')
        key=table['key']
        if not isinstance(key,str) or not KEY.fullmatch(key) or key in tables:invalid('明细表标识无效或重复')
        if not isinstance(table['label'],str) or not 1<=len(table['label'].strip())<=100:invalid('明细表名称无效')
        tables[key]={'definition':table,'fields':fields_index(table['fields'])}
        if not tables[key]['fields']:invalid('明细表至少配置一个字段')
    return headers,tables

def typed(value,field):
    kind=field['type']
    if kind in {'decimal','money'}:
        if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):raise ValueError()
        text=str(value)
        if len(text)>100:raise ValueError()
        number=Decimal(text)
        if (not number.is_finite() or abs(number.adjusted())>30 or len(number.as_tuple().digits)>30
                or number.as_tuple().exponent < -12):raise ValueError()
        return number
    if kind=='boolean':
        if type(value) is not bool:raise ValueError()
        return value
    if not isinstance(value,str) or len(value)>4000:raise ValueError()
    if kind=='date':
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError()
        return date.fromisoformat(value)
    return value

def validate_comparison(rule,field):
    op=rule.get('op')
    if not isinstance(op,str) or op not in COMPARISONS:invalid('比较方式未登记')
    if op=='contains' and field['type']!='text':invalid('包含判断仅适用于文本')
    if op in {'gt','gte','lt','lte'} and field['type'] not in {'decimal','money','date'}:invalid('该字段不支持大小比较')
    if field['type']=='money':
        if not isinstance(rule.get('currency'),str) or not re.fullmatch('[A-Z]{3}',rule['currency']):invalid('金额条件必须明确三位币种编码')
    elif 'currency' in rule:invalid('非金额条件不应包含币种')
    values=rule.get('value') if op=='in' else [rule.get('value')]
    if not isinstance(values,list) or not 1<=len(values)<=100:invalid('比较值或集合无效')
    try:
        for value in values:typed(value,field)
    except (ValueError,InvalidOperation):invalid('比较值与字段类型不一致')

def validate_rule(rule,contract):
    headers,tables=validate_contract(contract)
    count=0
    def visit(r,fields,depth=0,in_row=False):
        nonlocal count
        count+=1
        if count>200 or depth>8 or not isinstance(r,dict):invalid('条件数量、结构或嵌套深度无效')
        if 'all' in r or 'any' in r:
            if len(r)!=1:invalid('组合条件不能混用其他配置')
            children=next(iter(r.values()))
            if not isinstance(children,list) or not 1<=len(children)<=20:invalid('组合条件数量无效')
            for child in children:visit(child,fields,depth+1,in_row)
        elif 'table' in r:
            key=r['table']
            if in_row or not isinstance(key,str) or key not in tables:invalid('明细表未登记或不能在行内嵌套另一张表')
            fields=tables[key]['fields']
            if 'quantifier' in r:
                q=r['quantifier']
                if not isinstance(q,str) or q not in {'ANY','ALL','ROW'}:invalid('明细判断范围无效')
                expected={'table','quantifier','condition'}|({'row_id'} if q=='ROW' else set())
                if set(r)!=expected:invalid('明细条件结构无效')
                if q=='ROW' and (not isinstance(r['row_id'],str) or not 1<=len(r['row_id'])<=100):invalid('指定明细需要稳定行标识')
                visit(r['condition'],fields,depth+1,True)
            else:
                operation=r.get('aggregate')
                if not isinstance(operation,str) or operation not in {'SUM','COUNT','MIN','MAX'}:invalid('汇总方式无效')
                required={'table','aggregate','op','value'}|({'field'} if operation!='COUNT' else set())
                if not required<=set(r) or set(r)-required-{'filter','currency'}:invalid('汇总条件结构无效')
                f={'type':'decimal'} if operation=='COUNT' else fields.get(r['field']) if isinstance(r['field'],str) else None
                if not f or f['type'] not in {'decimal','money'}:invalid('仅可汇总已登记数值字段')
                validate_comparison(r,f)
                if 'filter' in r:visit(r['filter'],fields,depth+1,True)
        else:
            if not {'field','op','value'}<=set(r) or set(r)-{'field','op','value','currency'}:invalid('字段条件结构无效')
            f=fields.get(r['field']) if isinstance(r['field'],str) else None
            if not f:invalid('条件字段不在该资料范围内')
            validate_comparison(r,f)
    visit(rule,headers)

def compare(lhs,rule,field):
    rhs=[typed(v,field) for v in rule['value']] if rule['op']=='in' else typed(rule['value'],field)
    return {'eq':lambda:lhs==rhs,'ne':lambda:lhs!=rhs,'gt':lambda:lhs>rhs,
            'gte':lambda:lhs>=rhs,'lt':lambda:lhs<rhs,'lte':lambda:lhs<=rhs,
            'in':lambda:lhs in rhs,'contains':lambda:rhs in lhs}[rule['op']]()

def evaluate(rule,data,contract):
    """Return a JSON-safe explanation. Missing/invalid/ambiguous data never means pass."""
    validate_rule(rule,contract)
    headers,tables=validate_contract(contract)
    budget=50000
    def outcome(result,**extra):return {'result':result,**extra}
    def unknown(reason,**extra):return outcome(None,reason=reason,**extra)
    def visit(r,fields,values,location):
        nonlocal budget
        budget-=1
        if budget<0:raise DomainError('MATERIAL_RULE_BUDGET','资料条件计算超出限制，请缩小资料范围',409)
        if 'all' in r or 'any' in r:
            op='all' if 'all' in r else 'any'
            children=[visit(c,fields,values,location) for c in r[op]]
            results=[c['result'] for c in children]
            # Explicitly strict unknown propagation: no missing cell is bypassed by another row/term.
            result=None if None in results else all(results) if op=='all' else any(results)
            return outcome(result,kind=op,children=children,reason='DATA_MISSING' if result is None else None)
        if 'table' in r:
            key=r['table'];table=tables[key];fields=table['fields']
            raw=data.get('tables',{}).get(key) if isinstance(data,dict) and isinstance(data.get('tables'),dict) else None
            if not isinstance(raw,list) or not raw:return unknown('ROWS_MISSING',table=key)
            if len(raw)>MAX_ROWS:return unknown('ROW_LIMIT',table=key)
            ids=[]
            for row in raw:
                if (not isinstance(row,dict) or not isinstance(row.get('id'),str) or not 1<=len(row['id'])<=100
                        or not isinstance(row.get('values'),dict)):return unknown('ROW_INVALID',table=key)
                ids.append(row['id'])
            if len(ids)!=len(set(ids)):return unknown('ROW_ID_DUPLICATE',table=key)
            if 'quantifier' in r:
                chosen=[row for row in raw if row['id']==r['row_id']] if r['quantifier']=='ROW' else raw
                if not chosen:return unknown('ROW_NOT_FOUND',table=key)
                traces=[visit(r['condition'],fields,row['values'],{'table':key,'row_id':row['id']}) for row in chosen]
                results=[c['result'] for c in traces]
                result=None if None in results else any(results) if r['quantifier']=='ANY' else all(results)
                return outcome(result,kind=r['quantifier'],table=key,label=table['definition']['label'],
                               rows=[{'row_id':row['id'],'evaluation':trace} for row,trace in zip(chosen[:100],traces[:100])],
                               evaluated_rows=len(chosen),omitted_rows=max(0,len(chosen)-100))
            chosen=[]
            for row in raw:
                if 'filter' in r:
                    filtered=visit(r['filter'],fields,row['values'],{'table':key,'row_id':row['id']})
                    if filtered['result'] is None:return unknown('FILTER_DATA_MISSING',table=key,row_id=row['id'])
                    if not filtered['result']:continue
                chosen.append(row)
            operation=r['aggregate']
            if operation=='COUNT':lhs=Decimal(len(chosen));field={'type':'decimal'}
            else:
                if not chosen:return unknown('ROWS_MISSING',table=key)
                field=fields[r['field']];numbers=[]
                for row in chosen:
                    if field['type']=='money' and row['values'].get(field['currency_field'])!=r['currency']:
                        return unknown('CURRENCY_MISMATCH',table=key,row_id=row['id'])
                    try:numbers.append(typed(row['values'].get(r['field']),field))
                    except (ValueError,InvalidOperation):return unknown('VALUE_INVALID',table=key,row_id=row['id'],field=r['field'])
                with localcontext() as decimal_context:
                    decimal_context.prec=80
                    lhs=sum(numbers,Decimal(0)) if operation=='SUM' else min(numbers) if operation=='MIN' else max(numbers)
            return outcome(compare(lhs,r,field),kind=operation,table=key,actual=str(lhs),expected=r['value'],
                           currency=r.get('currency'),row_ids=[row['id'] for row in chosen[:100]],evaluated_rows=len(chosen))
        field=fields[r['field']]
        context={**location,'field':r['field'],'label':field['label'],'operator':r['op'],'expected':r['value']}
        if not isinstance(values,dict) or values.get(r['field']) is None:return unknown('VALUE_MISSING',**context)
        if field['type']=='money' and values.get(field['currency_field'])!=r['currency']:return unknown('CURRENCY_MISMATCH',**context)
        try:lhs=typed(values[r['field']],field)
        except (ValueError,InvalidOperation):return unknown('VALUE_INVALID',**context)
        return outcome(compare(lhs,r,field),actual=str(lhs) if isinstance(lhs,(Decimal,date)) else lhs,**context)
    return visit(rule,headers,data.get('fields') if isinstance(data,dict) else None,{})
