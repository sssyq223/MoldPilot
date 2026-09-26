import {afterEach, describe, expect, it} from 'vitest'
import {effectScope, ref, type EffectScope} from 'vue'
import {useDocumentFieldPagination} from './documentFieldPagination'

const scopes:EffectScope[]=[]
afterEach(()=>{scopes.splice(0).forEach(scope=>scope.stop())})
function setup(count:number){
 const group=ref({id:'contract-1',fields:Array.from({length:count},(_,i)=>({id:i+1}))})
 const scope=effectScope();scopes.push(scope)
 const pagination=scope.run(()=>useDocumentFieldPagination(group))!
 return {group,...pagination}
}

describe('合同候选字段分页',()=>{
 it('112 个字段分为 12 页，首页 10 个、末页 2 个，翻页不漏不重',()=>{
  const view=setup(112)
  expect(view.pageCount.value).toBe(12)
  expect(view.visibleFields.value.map(field=>field.id)).toEqual([1,2,3,4,5,6,7,8,9,10])
  const visited:number[]=[]
  for(let page=1;page<=12;page++){
   view.goTo(page);visited.push(...view.visibleFields.value.map(field=>field.id))
  }
  expect(view.visibleFields.value.map(field=>field.id)).toEqual([111,112])
  expect([view.start.value,view.end.value]).toEqual([111,112])
  expect(visited).toHaveLength(112)
  expect(new Set(visited).size).toBe(112)
 })
 it.each([0,1,10,11])('%i 个字段不产生多余空白页',count=>{
  const view=setup(count)
  expect(view.pageCount.value).toBe(count===11?2:1)
  expect(view.start.value).toBe(count===0?0:1)
  expect(view.visibleFields.value).toHaveLength(Math.min(count,10))
 })
 it('同一分组刷新保留当前页并展示新值',()=>{
  const view=setup(112);view.goTo(3)
  view.group.value={id:'contract-1',fields:Array.from({length:112},(_,i)=>({id:1000+i+1}))}
  expect(view.page.value).toBe(3)
  expect(view.visibleFields.value[0].id).toBe(1021)
 })
 it('字段缩减时回到最后有效页，清空时显示空态',()=>{
  const view=setup(112);view.goTo(12)
  view.group.value.fields=view.group.value.fields.slice(0,25)
  expect(view.page.value).toBe(3)
  expect(view.visibleFields.value.map(field=>field.id)).toEqual([21,22,23,24,25])
  view.group.value.fields=[]
  expect([view.page.value,view.start.value,view.end.value]).toEqual([1,0,0])
  expect(view.visibleFields.value).toEqual([])
 })
 it('切换合同分组从第一页开始',()=>{
  const view=setup(112);view.goTo(8)
  view.group.value={id:'contract-2',fields:Array.from({length:112},(_,i)=>({id:i+201}))}
  expect(view.page.value).toBe(1)
  expect(view.visibleFields.value[0].id).toBe(201)
 })
 it('前后翻页不能越界',()=>{
  const view=setup(11)
  view.goTo(0);expect(view.page.value).toBe(1)
  view.goTo(3);expect(view.page.value).toBe(2)
 })
})
