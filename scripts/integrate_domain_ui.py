from pathlib import Path

root=Path(__file__).resolve().parents[1]
def edit(name,pairs):
    path=root/name
    text=path.read_text(encoding='utf-8')
    for old,new in pairs:
        assert old in text, (name,old[:80])
        text=text.replace(old,new)
    path.write_text(text,encoding='utf-8')

edit('web/src/App.vue',[
 ("const modelName=", "import DomainPanel from './components/DomainPanel.vue'\nimport OrderPanel from './components/OrderPanel.vue'\nimport WarehousePanel from './components/WarehousePanel.vue'\nimport RiskPanel from './components/RiskPanel.vue'\nimport MasterPanel from './components/MasterPanel.vue'\nconst modelName="),
 ("{work:'我的工作',", "{business:'业务流转',orders:'订单执行',warehouse:'仓储与检验',risk:'发货风险分析',master:'基础资料',work:'我的工作',"),
 ("{key:'approvals',name:", "{key:'business',name:'业务',icon:Layers,allow:permissions.value.some(x=>x.endsWith('.create')&&!x.startsWith('purchase.'))},{key:'orders',name:'订单',icon:ShoppingCart,allow:permissions.value.includes('order.read')},{key:'warehouse',name:'仓储',icon:Folder,allow:permissions.value.includes('warehouse.read')},{key:'risk',name:'预警',icon:Bell,allow:permissions.value.includes('risk.read')||permissions.value.includes('risk.configure')},{key:'master',name:'资料',icon:Settings,allow:permissions.value.includes('master.manage')},{key:'approvals',name:"),
 ("allow:permissions.value.includes('purchase.approve')", "allow:permissions.value.some(x=>x.endsWith('.approve'))"),
 ('    <template v-if="panel===\'approvals\'">', '    <DomainPanel v-if="panel===\'business\'" :permissions="permissions" @error="fail" @changed="changed" @approval="openApproval"/>\n    <OrderPanel v-else-if="panel===\'orders\'" :permissions="permissions" @error="fail"/>\n    <WarehousePanel v-else-if="panel===\'warehouse\'" :permissions="permissions" @error="fail"/>\n    <RiskPanel v-else-if="panel===\'risk\'" :permissions="permissions" @error="fail"/>\n    <MasterPanel v-else-if="panel===\'master\'" @error="fail"/>\n    <template v-else-if="panel===\'approvals\'">'),
 ])
edit('web/src/components/DomainPanel.vue',[
 (";options.value.source_subject_id=records.value.map(r=>({...r,label:r.number+' · '+stateLabels[r.status]}));options.value.previous_id=options.value.source_subject_id", ''),
 ("api('/users'),api('/master/suppliers?project_id='+project.value),api('/master/customers')", "props.permissions.includes('user.manage')?api('/users'):Promise.resolve([]),project.value?api('/master/suppliers?project_id='+project.value):Promise.resolve([]),props.permissions.includes('master.manage')?api('/master/customers'):Promise.resolve([])"),
 ("await refresh();definition.value=", "await refresh();await loadOptions();definition.value="),
 ])
edit('web/src/components/WorkflowPanel.vue',[
 ("const nodes=", "const businessTypes=ref<any[]>([]),businessType=ref('purchase_request')\nconst nodes="),
 ("users.value=await api('/users')", "users.value=await api('/users');businessTypes.value=[{key:'purchase_request',name:'采购申请'},...await api('/business/catalog')]"),
 ("function copy(t:any){name.value=", "function copy(t:any){businessType.value=t.business_type;name.value="),
 ("business_type:'purchase_request',nodes", "business_type:businessType.value,nodes"),
 ('<div class="flow-start">申请提交</div>', '<label>适用业务<select v-model="businessType"><option v-for="item in businessTypes" :key="item.key" :value="item.key">{{item.name}}</option></select></label><div class="flow-start">申请提交</div>'),
 ('<option value="quantity">数量</option>', '<option value="amount">总金额</option><option value="currency">币种</option><option value="quantity">数量</option>'),
 ])
edit('web/src/components/PurchasePanel.vue',[("t.status==='PUBLISHED'", "t.status==='PUBLISHED'&&t.business_type==='purchase_request'")])
edit('web/src/components/ApprovalPanel.vue',[
 ("const props =", "import BusinessFacts from './BusinessFacts.vue'\nconst props ="),
 ('<span class="muted">采购申请</span>', '<span class="muted">{{detail.definition.name}}</span>'),
 ('<section class="surface"><h3>采购申请明细</h3>', '<section v-if="detail.business_type===\'purchase_request\'" class="surface"><h3>采购申请明细</h3>'),
 ('<section class="surface"><h3>申请备注</h3>', '<section v-else class="surface"><h3>提交审批的业务材料</h3><BusinessFacts :value="detail.snapshot.detail"/></section>\n      <section class="surface"><h3>申请备注</h3>'),
 ])
