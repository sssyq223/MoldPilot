export const initialProduct={
 id:'mold',
 product_name:'MoldPilot',
 display_name:'模具项目智能工作台',
 tagline:'从一个任务开始，让业务能力协同工作。',
 workspace_tabs:[
  {key:'approvals',name:'审批材料',hint:'查看待审批事项、节点和依据'},
  {key:'contacts',name:'联络单材料',hint:'查看工程联络单、附件和协作进度'},
 ],
 proposal_presentation:{
  action_prefixes:['登记','确认','准备'],
  action_suffixes:['证据登记','证据'],
  detail_links:{contact:{target:'contacts',receipt_field:'case_id',label:'查看材料'}},
  value_names:{},
 },
}
