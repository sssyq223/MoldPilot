const ACTIVE_RUN_STATUSES = new Set(['QUEUED', 'RUNNING'])

export function activeRunElapsedSeconds(run: any, nowMs = Date.now()): number {
  const serverSeconds = Math.max(0, Number(run?.progress?.elapsed_seconds) || 0)
  if (!ACTIVE_RUN_STATUSES.has(String(run?.status || ''))) return serverSeconds
  const createdAtMs = Date.parse(String(run?.created_at || ''))
  if (!Number.isFinite(createdAtMs)) return serverSeconds
  return Math.max(serverSeconds, Math.floor(Math.max(0, nowMs - createdAtMs) / 1000))
}

export function shouldRefreshRunProjection(
  running: boolean,
  conversationId: string,
  lastSyncAt: number,
  nowMs = Date.now(),
  staleAfterMs = 3000,
): boolean {
  if (!running || !conversationId) return false
  return !Number.isFinite(lastSyncAt) || lastSyncAt <= 0 || nowMs - lastSyncAt >= staleAfterMs
}
