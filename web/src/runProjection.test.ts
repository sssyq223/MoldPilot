import { describe, expect, it } from 'vitest'

import { activeRunElapsedSeconds, shouldRefreshRunProjection } from './runProjection'

describe('run projection reconciliation', () => {
  it('keeps active elapsed time moving when no live event arrives', () => {
    const createdAt = '2026-09-19T14:00:00+08:00'
    const now = Date.parse('2026-09-19T14:00:12+08:00')
    expect(activeRunElapsedSeconds({
      status: 'RUNNING', created_at: createdAt, progress: { elapsed_seconds: 2 },
    }, now)).toBe(12)
  })

  it('keeps the newer server elapsed value when it is ahead', () => {
    const createdAt = '2026-09-19T14:00:00+08:00'
    const now = Date.parse('2026-09-19T14:00:05+08:00')
    expect(activeRunElapsedSeconds({
      status: 'QUEUED', created_at: createdAt, progress: { elapsed_seconds: 9 },
    }, now)).toBe(9)
  })

  it('reconciles an active run after the live projection becomes stale', () => {
    expect(shouldRefreshRunProjection(true, 'conversation-1', 10_000, 12_999)).toBe(false)
    expect(shouldRefreshRunProjection(true, 'conversation-1', 10_000, 13_000)).toBe(true)
    expect(shouldRefreshRunProjection(false, 'conversation-1', 0, 13_000)).toBe(false)
    expect(shouldRefreshRunProjection(true, '', 0, 13_000)).toBe(false)
  })
})
