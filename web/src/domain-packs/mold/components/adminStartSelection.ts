type ProjectCandidate={id:string}
type MaterialSnapshot={project_id?:string;project_version?:number;internal_mold_numbers?:string[]}

export function validateAdminStartSelection(
 material:MaterialSnapshot,
 projects:ProjectCandidate[]|undefined,
 mode:'update'|'decision',
){
 const projectId=String(material.project_id||'')
 const moldNumbers=Array.isArray(material.internal_mold_numbers)?material.internal_mold_numbers.filter(Boolean):[]
 if(!projectId&&moldNumbers.length)return '请先选择系统项目。'
 if(!projectId)return ''
 const project=projects?.find(item=>item.id===projectId)
 if(!project)return '所选项目不在当前系统候选中，请重新选择。'
 return ''
}
