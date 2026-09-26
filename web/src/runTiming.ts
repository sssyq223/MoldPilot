export function runDurationSeconds(run: any, nowMs = Date.now()): number {
  if (run?.status === 'RUNNING' || run?.status === 'QUEUED') {
    const reported = Math.max(0, Number(run?.progress?.elapsed_seconds) || 0)
    const startedAt = new Date(run?.created_at).getTime()
    const local = Number.isFinite(startedAt)
      ? Math.max(0, Math.floor((nowMs - startedAt) / 1000))
      : 0
    return Math.max(reported, local)
  }

  if (Number.isFinite(Number(run?.duration_seconds))) {
    return Math.max(0, Number(run.duration_seconds))
  }

  const modelElapsedMs = Number(run?.progress?.model_elapsed_ms || 0)
  return modelElapsedMs > 0 ? Math.max(1, Math.round(modelElapsedMs / 1000)) : 0
}

export function durationText(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0))
  const minutes = Math.floor(total / 60)
  const remainingSeconds = total % 60
  return minutes > 0 ? `${minutes}分${remainingSeconds}秒` : `${remainingSeconds}秒`
}

export function shouldPollActiveRun(runEventsReady: boolean, tick: number): boolean {
  return !runEventsReady || Math.max(0, Math.floor(tick)) % 4 === 0
}
