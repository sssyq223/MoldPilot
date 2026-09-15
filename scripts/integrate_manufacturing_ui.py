from pathlib import Path
root=Path(__file__).resolve().parents[1]
def edit(name,pairs):
    path=root/name;text=path.read_text(encoding='utf-8')
    for old,new in pairs:
        assert old in text,(name,old[:60]);text=text.replace(old,new)
    path.write_text(text,encoding='utf-8')

path=root/'web/src/businessForms.ts'
text=path.read_text(encoding='utf-8')
text=text.replace(" customer_id:"," drawing_revision:'图纸版本',drawing_evidence:'图纸及复核依据',reviewer_id:'设计复核人员',items:'BOM 明细',route:'加工路线',valid_from:'有效期开始',valid_to:'有效期结束',quote_evidence:'报价依据',design_id:'已生效设计',supervisor_id:'钳工主管',prerequisites_evidence:'齐套与装配条件核验',planned_date:'计划日期',execution_status:'实际执行状态',assembly_id:'已完成装配任务',location:'试模地点',acceptance_criteria:'验收标准',responsible_id:'试模责任人',results:'试模结果',execution:'实际执行记录',findings:'发现的问题与结论',change_id:'关联工程联络单',original_payment_id:'原付款记录',reversal_evidence:'实际冲正依据',reversal_date:'实际冲正日期',payments:'实付与冲正记录',price_subject_id:'生效价格版本',\n customer_id:",1)
text=text.replace("{'order.issue':", "{'assembly.execute':'确认装配执行','trial.confirm':'确认试模结论','order.issue':")
text=text.replace("{ACCEPT:", "{PURCHASE:'外购',OUTSOURCE:'委外加工',ACCEPT:")
path.write_text(text,encoding='utf-8')

edit('web/src/components/DomainPanel.vue',[
 ("async function loadOptions(){const [users,suppliers,customers,subjects]=await Promise.allSettled([props.permissions.includes('user.manage')?api('/users'):Promise.resolve([]),project.value?api('/master/suppliers?project_id='+project.value):Promise.resolve([]),props.permissions.includes('master.manage')?api('/master/customers'):Promise.resolve([]),api('/business/subjects')]);\n options.value={owner_user_id:users.status==='fulfilled'?users.value.map((u:any)=>({id:u.id,label:u.display_name+' · '+u.username})):[],supplier_id:suppliers.status==='fulfilled'?suppliers.value:[],customer_id:customers.status==='fulfilled'?customers.value:[]}",
 "async function loadOptions(){const [refs,subjects]=await Promise.allSettled([project.value&&kind.value&&props.permissions.includes(kind.value+'.create')?api('/business/options?kind='+kind.value+'&project_id='+project.value+(category.value?'&category='+category.value:'')):Promise.resolve({}),api('/business/subjects')]);\n options.value=refs.status==='fulfilled'?refs.value:{}"),
 ("const all=subjects.value;", "const all=subjects.value.filter((r:any)=>!project.value||r.project_id===project.value);options.value.design_id=all.filter((r:any)=>r.kind==='design_route'&&r.status==='EFFECTIVE');options.value.assembly_id=all.filter((r:any)=>r.kind==='assembly_issue'&&r.detail?.execution_status==='DONE');options.value.change_id=all.filter((r:any)=>r.kind==='engineering_change');options.value.original_payment_id=all.flatMap((r:any)=>(r.detail?.payments||[]).filter((p:any)=>Number(p.amount)>0).map((p:any)=>({...p,label:r.number+' / '+p.reference+' / '+p.amount+' '+p.currency})));"),
 ("watch(project,async", "watch([project,category],async"),
 ("<div v-if=\"row.status==='EFFECTIVE'\">", "<div v-if=\"row.status==='EFFECTIVE'\"><button v-if=\"row.kind==='assembly_issue'&&permissions.includes('assembly.execute')&&row.detail.execution_status!=='DONE'\" @click=\"command('assembly.execute',row,row.number)\">登记装配执行</button><button v-if=\"row.kind==='trial_request'&&permissions.includes('trial.confirm')&&!row.detail.results?.length\" @click=\"command('trial.confirm',row,row.number)\">登记试模结论</button>"),
 ])
edit('web/src/components/OrderPanel.vue',[
 ("const rows=", "const prices=ref<any[]>([])\nconst rows="),
 ("editing.value=structuredClone(row)", "prices.value=(await api('/business/subjects?kind=purchase_price')).filter((p:any)=>p.status==='EFFECTIVE'&&p.project_id===row.project_id);editing.value=structuredClone(row)"),
 ("id:l.id,unit_price:l.unit_price", "id:l.id,price_subject_id:l.price_subject_id,unit_price:l.unit_price"),
 ('<label>单价<input v-model="line.unit_price" type="number" min="0" step="0.000001" required/></label>', '<label>已审批价格<select v-model="line.price_subject_id" required @change="line.unit_price=prices.find(p=>p.id===line.price_subject_id)?.detail.unit_price"><option value="">请选择有效审批价格</option><option v-for="p in prices.filter(p=>p.detail.supplier_id===editing.supplier_id&&p.detail.material_id===line.material_id&&p.detail.currency===editing.currency)" :key="p.id" :value="p.id">{{p.number}} · {{p.detail.unit_price}} {{p.detail.currency}}</option></select></label><label>单价<input :value="line.unit_price" readonly/></label>'),
 ])
