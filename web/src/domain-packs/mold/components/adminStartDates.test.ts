import {describe,expect,it} from 'vitest'
import {normalizeAdminStartDate,isAdminStartDateField} from './adminStartDates'

describe('内部开工日期输入',()=>{
 it('将有完整年月日的来源日期规范为 date input 格式',()=>{
  expect(normalizeAdminStartDate('2025年08月10日前')).toBe('2025-08-10')
  expect(normalizeAdminStartDate('2025-08-10')).toBe('2025-08-10')
 })
 it('不猜测缺少年份或非法日期',()=>{
  expect(normalizeAdminStartDate('8月5号')).toBe('')
  expect(normalizeAdminStartDate('2025年13月40日')).toBe('')
 })
 it('只把内部开工日期和客户交期设为日期字段',()=>{
  expect(isAdminStartDateField('effective_date')).toBe(true)
  expect(isAdminStartDateField('customer_due_date')).toBe(true)
  expect(isAdminStartDateField('project_number')).toBe(false)
 })
})
