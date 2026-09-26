import {describe,expect,it} from 'vitest'
import {validateAdminStartSelection} from './adminStartSelection'

const projects=[{id:'project-1',molds:[{id:'mold-1'}]}]

describe('内部开工项目与模具选择',()=>{
 it('填写内部模具但没有选择项目时拒绝提交',()=>{
  expect(validateAdminStartSelection({internal_mold_numbers:['M260063-P3']},projects,'update')).toBe('请先选择系统项目。')
 })
 it('选择项目后允许提交人工内部模具编号',()=>{
  expect(validateAdminStartSelection({project_id:'project-1',project_version:1,internal_mold_numbers:['M260063-P3']},projects,'update')).toBe('')
 })
 it('承接决定阶段不要求项目和内部模具资料',()=>{
  expect(validateAdminStartSelection({},projects,'decision')).toBe('')
  expect(validateAdminStartSelection({project_id:'project-1',project_version:1},projects,'decision')).toBe('')
 })
})
