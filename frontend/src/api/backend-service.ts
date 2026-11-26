import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export type WorkflowResult = ActionResult & Record<string, any>

async function postJson(path: string, body: Record<string, unknown>): Promise<WorkflowResult> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return await response.json()
}

export function batchConfirmSummary(body: {
  entry_ids: number[]
  grain_id?: string
  batch_id?: string
  idempotency_key?: string
}): Promise<WorkflowResult> {
  return postJson('/api/summary_layer/batches', body)
}

export function saveSummaryDraft(entry_ids: number[]): Promise<WorkflowResult> {
  return postJson('/api/summary_layer/batches/draft', { entry_ids })
}

export function retrySummaryBatch(body: {
  batch_id?: string
  receipt_no?: string
  entry_ids?: number[]
}): Promise<WorkflowResult> {
  if (body.receipt_no && !body.batch_id) {
    return postJson(`/api/summary_layer/receipts/${encodeURIComponent(body.receipt_no)}/retry`, {})
  }
  return postJson('/api/summary_layer/retry', body)
}

export function relinkSummary(
  id: number,
  grain_id: string,
  expected_version?: number,
): Promise<WorkflowResult> {
  return postJson(`/api/summary_layer/${id}/relink`, { grain_id, expected_version })
}

export async function listReviewTodos(params: {
  reviewer_group?: string
  scope?: string
  grain_id?: string
} = {}): Promise<{ items: EntryRow[]; total: number }> {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  const response = await request(`/api/grain_declare/review-todos?${query.toString()}`)
  if (!response.ok) throw new Error('复核待办读取失败')
  return await response.json()
}

export async function getReviewScope(reviewer_group: string): Promise<WorkflowResult> {
  const query = new URLSearchParams({ reviewer_group })
  const response = await request(`/api/grain_declare/review-scope?${query.toString()}`)
  if (!response.ok) throw new Error('复核范围读取失败')
  return await response.json()
}

export function receiveReview(
  reviewId: number | string,
  body: { reviewer_group: string; reviewer: string; receipt_no?: string },
): Promise<WorkflowResult> {
  return postJson(`/api/grain_declare/review-todos/${reviewId}/receive`, body)
}

export function signReview(
  reviewId: number | string,
  body: {
    reviewer_group: string
    reviewer: string
    decision?: string
    receipt_no?: string
    comment?: string
  },
): Promise<WorkflowResult> {
  return postJson(`/api/grain_declare/review-todos/${reviewId}/sign`, body)
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
