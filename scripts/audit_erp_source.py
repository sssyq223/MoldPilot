"""Read-only static evidence inventory. Does not import or execute ERP source."""
import ast
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[2]
source=root/'work/erp_review_20260914/source/management-system-zhangwenjin'
backend=source/'ruoyi-fastapi-backend'
out=root/'mold-agent/docs/erp-audit'
out.mkdir(parents=True,exist_ok=True)
inventory=[];errors=[]
for path in sorted(backend.glob('module_*/controller/*.py')):
    content=path.read_text(encoding='utf-8-sig')
    try:tree=ast.parse(content)
    except SyntaxError as e:errors.append({'file':str(path.relative_to(source)),'line':e.lineno});continue
    routers={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call):
            prefix=next((k.value.value for k in node.value.keywords if k.arg=='prefix' and isinstance(k.value,ast.Constant)),None)
            if prefix is not None:
                for target in node.targets:
                    if isinstance(target,ast.Name):routers[target.id]=prefix
    for node in tree.body:
        if not isinstance(node,(ast.AsyncFunctionDef,ast.FunctionDef)):continue
        for dec in node.decorator_list:
            if not isinstance(dec,ast.Call) or not isinstance(dec.func,ast.Attribute) or not isinstance(dec.func.value,ast.Name):continue
            router=dec.func.value.id
            if router not in routers or dec.func.attr not in {'get','post','put','delete','patch'}:continue
            route=dec.args[0].value if dec.args and isinstance(dec.args[0],ast.Constant) else ''
            summary=next((k.value.value for k in dec.keywords if k.arg=='summary' and isinstance(k.value,ast.Constant)),'')
            calls=sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and 'Service' in ast.unparse(n.func)})
            permissions=sorted({n.args[0].value for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                and n.func.id=='UserInterfaceAuthDependency' and n.args and isinstance(n.args[0],ast.Constant)})
            inventory.append({'file':str(path.relative_to(source)).replace('\\','/'),'line':node.lineno,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'method':dec.func.attr.upper(),'route':routers[router]+route,
                'summary':summary,'function':node.name,'service_calls':calls,'permission_codes':permissions})
(out/'erp-endpoints.json').write_text(json.dumps({'source':str(source),'scope':'module_* controllers; excludes old agent runtime and tools',
    'endpoints':inventory,'parse_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
chosen={'project','mold','contract','assembly','trial_mold','design_change','production_schedule','quality_inspection',
        'design_order','bom','work_report','procedure_work_report','purchase_request','purchase_approval','purchase_order',
        'purchase_decision','material_inbound','warehouse','production_exception','project_node','workflow'}
for key in sorted(chosen):
    rows=[r for r in inventory if Path(r['file']).name==key+'_controller.py']
    print(key+': '+str(len(rows))+' endpoints')
    for row in rows:
        if row['method']!='GET' or key in {'bom','project_node'}:
            print('  '+row['method']+' '+row['route']+' | '+row['function']+' @'+str(row['line']))
print(f'Total endpoints: {len(inventory)}; parse errors: {len(errors)}')
