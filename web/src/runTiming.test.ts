import { describe, expect, it } from 'vitest'
import { durationText, runDurationSeconds, shouldPollActiveRun } from './runTiming'

describe('run timing', () => {
  it('advances an active run from its creation time without another server event', () => {
    expect(runDurationSeconds({
      status: 'RUNNING',
      created_at: '2026-09-19T00:00:00.000Z',
      progress: { elapsed_seconds: 0 },
    }, Date.parse('2026-09-19T00:00:12.900Z'))).toBe(12)
  })

  it('does not move backwards when the server reports a larger duration', () => {
    expect(runDurationSeconds({
      status: 'RUNNING',
      created_at: '2026-09-19T00:00:00.000Z',
      progress: { elapsed_seconds: 18 },
    }, Date.parse('2026-09-19T00:00:12.000Z'))).toBe(18)
  })

  it('keeps the final server duration and formats minutes', () => {
    expect(runDurationSeconds({ status: 'SUCCEEDED', duration_seconds: 27 })).toBe(27)
    expect(durationText(65)).toBe('1分5秒')
  })

  it('polls every four seconds as an SSE backstop and every second when SSE is down', () => {
    expect([1, 2, 3, 4, 5, 8].filter(tick => shouldPollActiveRun(true, tick))).toEqual([4, 8])
    expect([1, 2, 3, 4].filter(tick => shouldPollActiveRun(false, tick))).toEqual([1, 2, 3, 4])
  })
})
