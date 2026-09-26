import {computed, readonly, ref, watch, type Ref} from 'vue'

const PAGE_SIZE=10

export function useDocumentFieldPagination<T>(group:Readonly<Ref<{id:string;fields:T[]}>>){
 const page=ref(1)
 const total=computed(()=>group.value.fields.length)
 const pageCount=computed(()=>Math.max(1,Math.ceil(total.value/PAGE_SIZE)))
 function goTo(target:number){
  page.value=Math.min(pageCount.value,Math.max(1,Number.isFinite(target)?Math.floor(target):1))
 }
 // 轮询替换同一分组对象时不重置页码；数量缩减时修正越界页。
 watch(()=>[group.value.id,pageCount.value] as const,([id],[previousId])=>{
  goTo(id===previousId?page.value:1)
 },{flush:'sync'})
 const start=computed(()=>total.value?(page.value-1)*PAGE_SIZE+1:0)
 const end=computed(()=>Math.min(page.value*PAGE_SIZE,total.value))
 const visibleFields=computed(()=>group.value.fields.slice((page.value-1)*PAGE_SIZE,end.value))
 return {page:readonly(page),total,pageCount,start,end,visibleFields,goTo}
}
