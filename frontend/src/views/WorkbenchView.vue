<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <GrainReviewPanel v-if="meta.key === 'grain_declare'" @changed="reload" />

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <section v-if="isSummary" class="batch-bar" data-testid="summary-batch-panel">
      <label class="batch-check">
        <input
          type="checkbox"
          data-testid="select-all-visible"
          :checked="allVisibleSelected"
          @change="toggleSelectAll"
        />
        全选本页
      </label>
      <span data-testid="selected-count">已选 {{ selectedIds.length }} 条</span>
      <span class="batch-grain-hint" data-testid="batch-grain-hint">{{ selectedGrainHint }}</span>
      <button
        class="btn primary"
        type="button"
        data-testid="batch-confirm"
        :disabled="batchLoading || selectedIds.length === 0"
        @click="submitBatch"
      >
        {{ batchLoading ? '提交中…' : '批量确认' }}
      </button>
      <span class="batch-grain-hint">确认后跨模块创建粒度声明复核项</span>
      <button
        class="btn"
        type="button"
        data-testid="batch-draft"
        :disabled="batchLoading || selectedIds.length === 0"
        @click="saveDraft"
      >
        保存为待提交批次
      </button>
      <button
        class="btn"
        type="button"
        data-testid="batch-continue"
        :disabled="batchLoading || !lastBatchId"
        @click="retryLastBatch()"
      >
        失败续做
      </button>
      <button
        class="btn"
        type="button"
        data-testid="batch-retry"
        :disabled="batchLoading || !lastBatchId"
        @click="retryLastBatch()"
      >
        按回执重试
      </button>
      <input
        v-model="receiptNo"
        class="receipt-input"
        data-testid="receipt-input"
        placeholder="可粘贴接收回执 RCPT-… 后续做"
      />
      <button
        class="btn"
        type="button"
        data-testid="retry-by-receipt"
        :disabled="batchLoading || !receiptNo"
        @click="retryByEnteredReceipt"
      >
        按粘贴回执续做
      </button>
      <span v-if="batchMessage" class="batch-message" :class="{ error: !batchOk }" data-testid="batch-message">
        {{ batchMessage }}
      </span>
    </section>

    <section v-if="isSummary && batchResult" class="batch-results" data-testid="batch-results">
      <div class="batch-result-head">
        <h4>批次回执 {{ lastBatchId }}</h4>
        <button
          v-if="String(batchResult.batch?.status || '') === '待提交'"
          class="btn primary"
          type="button"
          data-testid="submit-draft"
          :disabled="batchLoading"
          @click="submitDraft"
        >
          提交该待提交批次
        </button>
      </div>
      <div
        v-for="item in batchItems"
        :key="`batch-item-${item.entry_id}`"
        class="batch-item"
        :data-testid="`batch-item-${item.entry_id}`"
        :data-state="item.state"
        :data-case="item.case"
      >
        <label class="batch-item-title">
          <input
            type="checkbox"
            :data-testid="`batch-select-${item.entry_id}`"
            :checked="selectedIds.includes(Number(item.entry_id))"
            @change="toggleSelect(Number(item.entry_id))"
          />
          #{{ item.entry_id }} {{ item.summary_no || '' }} · {{ item.case }} · {{ item.state }}
        </label>
        <p>{{ item.message }}</p>
        <p class="receipt-line">
          <span v-if="item.review_no">复核项：{{ item.review_no }}（{{ item.reviewer_group }}）</span>
          <span v-if="item.receipt_no">回执：<code :data-testid="`receipt-${item.receipt_no}`">{{ item.receipt_no }}</code></span>
          <button
            v-if="item.retryable"
            class="link"
            type="button"
            :data-testid="`retry-item-${item.entry_id}`"
            @click="retryItem(item)"
          >
            按该回执续做
          </button>
        </p>
      </div>
    </section>

    <table class="data-table">
      <thead>
        <tr>
          <th v-if="isSummary" class="select-col">选择</th>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-if="isSummary" class="select-col">
            <input
              type="checkbox"
              :data-testid="`select-row-${row.id}`"
              :aria-label="`选择汇总条目 ${row.id}`"
              :checked="selectedIds.includes(Number(row.id))"
              @change="toggleSelect(Number(row.id))"
            />
          </td>
          <td v-for="column in meta.fields" :key="column">
            <div v-if="isSummary && column === '关联声明编号'" class="association-cell">
              <select
                v-model="relinkDrafts[Number(row.id)]"
                :data-testid="`association-select-${row.id}`"
                :aria-label="`修改第 ${row.id} 条关联声明`"
              >
                <option v-for="grain in grainOptions" :key="String(grain.声明编号)" :value="String(grain.声明编号)">
                  {{ grain.声明编号 }}｜{{ grain.声明类别 }}｜{{ grain.评审组 }}
                </option>
              </select>
              <button
                class="link"
                type="button"
                :data-testid="`relink-${row.id}`"
                @click="changeAssociation(row)"
              >
                修改关联
              </button>
              <small class="association-meta">
                v{{ Number(row.association_version || 1) }}<template v-if="row.复核组"> · {{ row.复核组 }}</template>
              </small>
            </div>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + (isSummary ? 3 : 2)" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="successMessage" class="success-text" data-testid="action-success">{{ successMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import GrainReviewPanel from '@/components/GrainReviewPanel.vue'
import {
  batchConfirmSummary,
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  relinkSummary,
  retrySummaryBatch,
  runAction as applyAction,
  saveSummaryDraft,
  type WorkflowResult,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const route = useRoute()
const meta = moduleMeta(props.moduleKey)
const queryIds = String(route.query.ids || route.query.entry_ids || '')
  .split(',')
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isInteger(value) && value > 0)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const successMessage = ref('')

const isSummary = computed(() => meta.key === 'summary_layer')
const selectedIds = ref<number[]>(queryIds)
const batchLoading = ref(false)
const batchResult = ref<WorkflowResult | null>(null)
const batchMessage = ref('')
const batchOk = ref(true)
const receiptNo = ref('')
const grainOptions = ref<EntryRow[]>([])
const relinkDrafts = reactive<Record<number, string>>({})

const batchItems = computed<Record<string, any>[]>(() => {
  return (batchResult.value?.items as Record<string, any>[] | undefined) ?? []
})
const lastBatchId = computed(() => String(batchResult.value?.batch_id || ''))
const allVisibleSelected = computed(() =>
  rows.value.length > 0 && rows.value.every((row) => selectedIds.value.includes(Number(row.id))),
)
const selectedGrainHint = computed(() => {
  if (!selectedIds.value.length) return '可勾选关联同一声明的条目成批'
  const grains = new Set(
    rows.value
      .filter((row) => selectedIds.value.includes(Number(row.id)))
      .map((row) => String(row['关联声明编号'] || '')),
  )
  if (grains.size === 1) return `本批归属：${[...grains][0]}`
  return `所选条目关联了 ${grains.size} 个声明，不能跨声明成批`
})
const stats = computed(() => {
  const confirmedStatus = isSummary.value ? '已派生' : meta.statuses[1]
  return [
    { label: meta.metrics[0], value: total.value },
    { label: meta.metrics[1], value: rows.value.filter((row) => row.status === confirmedStatus).length },
    {
      label: meta.metrics[2],
      value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
    },
  ]
})
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    successMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
    if (isSummary.value) {
      for (const row of rows.value) {
        const id = Number(row.id)
        if (!relinkDrafts[id]) relinkDrafts[id] = String(row['关联声明编号'] || '')
      }
      if (!grainOptions.value.length) {
        const grains = await listEntries('grain_declare')
        grainOptions.value = grains.items
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

function toggleSelect(id: number) {
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter((item) => item !== id)
  } else {
    selectedIds.value = [...selectedIds.value, id]
  }
}

function toggleSelectAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const visibleIds = rows.value.map((row) => Number(row.id))
  if (checked) {
    selectedIds.value = Array.from(new Set([...selectedIds.value, ...visibleIds]))
  } else {
    const visible = new Set(visibleIds)
    selectedIds.value = selectedIds.value.filter((id) => !visible.has(id))
  }
}

function ensureSameGrain(): string {
  const selectedRows = rows.value.filter((row) => selectedIds.value.includes(Number(row.id)))
  const grains = new Set(selectedRows.map((row) => String(row['关联声明编号'] || '')))
  if (grains.size !== 1 || ![...grains][0]) {
    throw new Error('只能将关联同一粒度声明的条目选成一批')
  }
  return [...grains][0]
}

async function submitBatch() {
  batchLoading.value = true
  batchMessage.value = ''
  errorMessage.value = ''
  try {
    const grainId = ensureSameGrain()
    const previousIds = ((batchResult.value?.batch as Record<string, any> | undefined)?.entry_ids as any[] | undefined)
      ?.map((value) => Number(value)) ?? []
    const sameAsPrevious = Boolean(lastBatchId.value)
      && previousIds.length === selectedIds.value.length
      && previousIds.every((id) => selectedIds.value.includes(id))
    const result = sameAsPrevious
      ? await batchConfirmSummary({ entry_ids: selectedIds.value, batch_id: lastBatchId.value })
      : await batchConfirmSummary({ entry_ids: selectedIds.value, grain_id: grainId })
    batchResult.value = result
    batchOk.value = result.ok
    batchMessage.value = result.message
    // 不清空选择：被改关联/被领走/失败的未送出项仍保留在原选择里。
    await reload()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '批量确认失败'
  } finally {
    batchLoading.value = false
  }
}

async function saveDraft() {
  batchLoading.value = true
  batchMessage.value = ''
  try {
    ensureSameGrain()
    const result = await saveSummaryDraft(selectedIds.value)
    batchResult.value = result
    batchOk.value = result.ok
    batchMessage.value = result.message
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '保存待提交批次失败'
  } finally {
    batchLoading.value = false
  }
}

async function submitDraft() {
  if (!lastBatchId.value) return
  batchLoading.value = true
  batchMessage.value = ''
  try {
    const result = await batchConfirmSummary({
      entry_ids: selectedIds.value,
      batch_id: lastBatchId.value,
    })
    batchResult.value = result
    batchOk.value = result.ok
    batchMessage.value = result.message
    await reload()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '提交待提交批次失败'
  } finally {
    batchLoading.value = false
  }
}

async function retryLastBatch() {
  if (!lastBatchId.value) return
  batchLoading.value = true
  batchMessage.value = ''
  try {
    const result = await retrySummaryBatch({ batch_id: lastBatchId.value })
    batchResult.value = result
    batchOk.value = result.ok
    batchMessage.value = result.message
    await reload()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '失败续做失败'
  } finally {
    batchLoading.value = false
  }
}

async function retryItem(item: Record<string, any>) {
  batchLoading.value = true
  batchMessage.value = ''
  try {
    const result = await retrySummaryBatch({
      batch_id: lastBatchId.value || undefined,
      receipt_no: String(item.receipt_no || ''),
      entry_ids: [Number(item.entry_id)],
    })
    batchResult.value = result
    batchOk.value = result.ok
    batchMessage.value = result.message
    await reload()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '按回执重试失败'
  } finally {
    batchLoading.value = false
  }
}

async function retryByEnteredReceipt() {
  if (!receiptNo.value.trim()) return
  batchLoading.value = true
  batchMessage.value = ''
  try {
    const result = await retrySummaryBatch({ receipt_no: receiptNo.value.trim() })
    batchResult.value = result
    batchOk.value = result.ok
    batchMessage.value = result.message
    receiptNo.value = ''
    await reload()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '按粘贴回执续做失败'
  } finally {
    batchLoading.value = false
  }
}

async function changeAssociation(row: EntryRow) {
  const id = Number(row.id)
  const nextGrain = relinkDrafts[id]
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await relinkSummary(id, nextGrain, Number(row.association_version || 1))
    if (!result.ok) {
      // 版本冲突时仍展示后端说明，并刷新到最新关联，便于按回执继续。
      errorMessage.value = result.message
      await reload()
      const freshAfterConflict = rows.value.find((item) => Number(item.id) === id)
      if (freshAfterConflict) {
        relinkDrafts[id] = String(freshAfterConflict['关联声明编号'] || '')
      }
      return
    }
    successMessage.value = result.message
    if (result.batch_id) batchResult.value = result
    await reload()
    const fresh = rows.value.find((item) => Number(item.id) === id)
    if (fresh) relinkDrafts[id] = String(fresh['关联声明编号'] || '')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '修改关联失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.select-col { width: 42px; text-align: center; }
.batch-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 12px; margin-bottom: 10px;
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
}
.batch-check { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.batch-grain-hint { color: var(--muted); font-size: 13px; }
.batch-message { font-size: 13px; color: #027a48; }
.receipt-input { min-width: 220px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.batch-message.error { color: #b42318; }
.batch-results {
  margin-bottom: 12px; padding: 10px 12px;
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
}
.batch-results h4 { margin: 0; }
.batch-result-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.batch-item { padding: 8px 0; border-top: 1px solid #eef2f6; font-size: 13px; }
.batch-item-title { display: flex; gap: 6px; align-items: center; font-weight: 600; }
.batch-item p { margin: 4px 0; }
.receipt-line { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; color: var(--muted); }
.receipt-line code { padding: 1px 5px; background: #f2f4f7; border-radius: 4px; }
.association-cell { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; min-width: 170px; }
.association-cell select { width: 100%; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; }
.association-meta { color: var(--muted); font-size: 11px; }
.success-text { color: #027a48; margin-right: 12px; }
</style>
