<template>
  <section class="review-panel" data-testid="grain-review-panel">
    <div class="review-head">
      <div>
        <h3>复核待办 · 汇总层批量确认</h3>
        <p class="hint">
          汇总员按同一粒度声明建批后，会跨模块生成复核项。只有当前关联声明对应的评审组在有效处理范围内。
        </p>
      </div>
      <div class="review-controls">
        <label>
          <span>复核窗口/身份</span>
          <select v-model="reviewerGroup" data-testid="reviewer-group-select" @change="reloadAll">
            <option value="grain-common">共有声明评审组（GRN-001、GRN-002）</option>
            <option value="grain-other">其他声明评审组（GRN-003）</option>
            <option value="">全部评审组（调试视图）</option>
          </select>
        </label>
        <label>
          <span>复核人</span>
          <input v-model="reviewer" data-testid="reviewer-name" />
        </label>
        <label>
          <span>待办范围</span>
          <select v-model="scope" data-testid="review-scope-filter" @change="loadTodos">
            <option value="todo">有效待办</option>
            <option value="all">全部记录</option>
            <option value="invalid">失效/冲突</option>
            <option value="done">已办结果</option>
          </select>
        </label>
        <button class="btn" type="button" data-testid="refresh-reviews" @click="reloadAll">刷新待办</button>
        <button class="btn ghost" type="button" data-testid="switch-common-group" @click="switchGroup('grain-common')">
          共有声明评审组
        </button>
        <button class="btn ghost" type="button" data-testid="switch-other-group" @click="switchGroup('grain-other')">
          其他声明评审组
        </button>
      </div>
    </div>

    <p class="scope-bar" data-testid="review-scope">{{ scopeMessage }}</p>
    <p v-if="message" class="workflow-message" :class="{ error: !messageOk }" data-testid="review-message">
      {{ message }}
    </p>

    <section v-if="conflicts.length" class="conflict-panel" data-testid="review-conflicts">
      <h4>归属冲突与失效回执（旧组不能继续签收）</h4>
      <div
        v-for="item in conflicts"
        :key="`conflict-${item.id}`"
        class="conflict-row"
        :data-testid="`conflict-row-${item.id}`"
      >
        <strong>{{ item.review_no }} · {{ item.summary_no }}</strong>
        <p>{{ conflictText(item) || '该回执已因粒度声明关联变更而失效。' }}</p>
        <p v-if="item.rerouted_to" class="reroute-line">
          已转入新复核项 <code>{{ item.rerouted_to }}</code>，请切换到对应新评审组处理。
        </p>
      </div>
    </section>

    <table class="data-table review-table">
      <thead>
        <tr>
          <th>复核编号</th>
          <th>来源汇总条目</th>
          <th>当前归属声明</th>
          <th>有效评审组</th>
          <th>批次/回执</th>
          <th>状态</th>
          <th>处理范围说明</th>
          <th>复核动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in reviews"
          :key="String(item.id)"
          :data-testid="`review-row-${item.id}`"
          :data-status="String(item.status)"
          :data-in-scope="String(item.in_scope)"
        >
          <td>{{ item.review_no }}</td>
          <td>{{ item.summary_no }}<br /><small>#{{ item.source_id }} · {{ item.source_module }}</small></td>
          <td>
            {{ item.grain_id }}
            <br /><small>{{ item.grain_category }} · v{{ item.association_version }}</small>
            <small v-if="item.rerouted_from"><br />由 {{ item.rerouted_from }} 改派</small>
          </td>
          <td>{{ item.reviewer_group }}</td>
          <td>
            {{ item.batch_id }}
            <br /><small>派单 {{ item.dispatch_receipt_no || '—' }}</small>
            <small v-if="item.receive_receipt_no"><br />接收 {{ item.receive_receipt_no }}</small>
            <small v-if="item.sign_receipt_no"><br />签收 {{ item.sign_receipt_no }}</small>
          </td>
          <td>
            <strong>{{ item.status }}</strong>
            <br /><small v-if="item.received_by">{{ item.received_by }} 已接收</small>
            <small v-if="item.signed_by">{{ item.signed_by }} 已签收</small>
          </td>
          <td>
            <span :class="['scope-pill', item.in_scope ? 'valid' : 'stale']">
              {{ item.scope_state || (item.in_scope ? '有效处理范围' : '不在范围') }}
            </span>
            <p v-if="conflictText(item)" class="conflict-text" :data-testid="`conflict-${item.id}`">
              {{ conflictText(item) }}
            </p>
            <p v-if="resultText(item)" class="accepted-text" :data-testid="`accepted-result-${item.source_id}`">
              已留结果：{{ resultText(item) }}
            </p>
          </td>
          <td class="row-actions">
            <button
              v-if="item.status === '待接收' && canAct(item)"
              class="link"
              type="button"
              :data-testid="`receive-review-${item.id}`"
              @click="receive(item)"
            >
              接收
            </button>
            <button
              v-if="item.status === '已接收' && canAct(item)"
              class="link"
              type="button"
              :data-testid="`sign-review-${item.id}`"
              @click="sign(item, '通过')"
            >
              签收
            </button>
            <button
              v-if="item.status === '已接收' && canAct(item)"
              class="link danger"
              type="button"
              :data-testid="`reject-review-${item.id}`"
              @click="sign(item, '驳回')"
            >
              驳回
            </button>
            <span v-if="!canAct(item)" class="muted" :data-testid="`out-of-scope-${item.id}`">
              非本组范围
            </span>
          </td>
        </tr>
        <tr v-if="!reviews.length">
          <td colspan="8" class="empty-state" data-testid="review-empty">当前范围暂无复核项；请先在汇总层选择同一声明的条目并批量确认。</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  getReviewScope,
  listReviewTodos,
  receiveReview,
  signReview,
  type WorkflowResult,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const queryGroup = String(route.query.reviewer_group || route.query.group || 'grain-common')
const initialGroup = queryGroup.includes('其他')
  ? 'grain-other'
  : queryGroup.includes('共有')
    ? 'grain-common'
    : queryGroup
const reviewerGroup = ref(initialGroup)
const reviewer = ref(String(route.query.reviewer || '李复核'))
const scope = ref('todo')
const reviews = ref<EntryRow[]>([])
const conflicts = ref<EntryRow[]>([])
const scopeInfo = ref<WorkflowResult | null>(null)
const message = ref('')
const messageOk = ref(true)
let timer: number | undefined

const scopeMessage = computed(() => {
  if (scopeInfo.value?.message) return `复核人实际拥有的处理范围：${String(scopeInfo.value.message)}`
  return '正在核对复核人实际拥有的处理范围…'
})

function canAct(item: EntryRow): boolean {
  if (!reviewerGroup.value) return Boolean(item.in_scope)
  const group = String((item as any).group_code || '')
  const groupName = String(item.reviewer_group || '')
  return Boolean(item.in_scope) && (group === reviewerGroup.value || groupName === reviewerGroup.value)
}

function conflictText(item: EntryRow): string {
  const conflict = (item as any).conflict
  return conflict?.explanation ? String(conflict.explanation) : ''
}

function resultText(item: EntryRow): string {
  const result = (item as any).result
  if (!result) return ''
  const owner = result.accepted_by || result.rejected_by || ''
  return `${result.message || result.state || '已有结果'}${owner ? `（${owner}）` : ''}`
}

async function loadScope() {
  try {
    scopeInfo.value = await getReviewScope(reviewerGroup.value)
  } catch (error) {
    messageOk.value = false
    message.value = error instanceof Error ? error.message : '复核范围读取失败'
  }
}

function switchGroup(group: string) {
  reviewerGroup.value = group
  void reloadAll()
}

async function loadTodos() {
  try {
    const payload = await listReviewTodos({
      reviewer_group: reviewerGroup.value,
      scope: scope.value,
    })
    reviews.value = payload.items
  } catch (error) {
    messageOk.value = false
    message.value = error instanceof Error ? error.message : '复核待办读取失败'
  }
}

async function loadConflicts() {
  try {
    const payload = await listReviewTodos({
      reviewer_group: reviewerGroup.value,
      scope: 'invalid',
    })
    conflicts.value = payload.items
  } catch {
    // 冲突区是辅助信息，主待办的错误仍由 loadTodos 展示。
  }
}

async function reloadAll() {
  message.value = ''
  await Promise.all([loadScope(), loadTodos(), loadConflicts()])
  emit('changed')
}

async function receive(item: EntryRow) {
  message.value = ''
  const result = await receiveReview(String(item.id), {
    reviewer_group: reviewerGroup.value,
    reviewer: reviewer.value,
  })
  messageOk.value = result.ok
  message.value = result.message
  await reloadAll()
}

async function sign(item: EntryRow, decision: string) {
  message.value = ''
  const result = await signReview(String(item.id), {
    reviewer_group: reviewerGroup.value,
    reviewer: reviewer.value,
    decision,
    receipt_no: String(item.receive_receipt_no || item.receipt_no || ''),
  })
  messageOk.value = result.ok
  message.value = result.message
  await reloadAll()
}

onMounted(() => {
  void reloadAll()
  timer = window.setInterval(() => {
    void loadTodos()
    void loadConflicts()
  }, 2000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.review-panel { margin: 14px 0; padding: 14px; background: #f8fafc; border: 1px solid var(--border); border-radius: 10px; }
.review-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
.review-head h3 { margin: 0 0 4px; }
.hint { color: var(--muted); font-size: 12px; margin: 0; max-width: 680px; }
.review-controls { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.review-controls label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.review-controls select, .review-controls input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.scope-bar { margin: 10px 0; padding: 8px 10px; background: #eef6ff; border: 1px solid #b9dcff; border-radius: 6px; font-size: 13px; }
.workflow-message { margin: 8px 0; padding: 8px 10px; border-radius: 6px; background: #ecfdf3; color: #027a48; font-size: 13px; }
.workflow-message.error { background: #fef3f2; color: #b42318; }
.conflict-panel { margin: 8px 0 12px; padding: 10px 12px; border: 1px solid #fecdca; background: #fffbfa; border-radius: 8px; }
.conflict-panel h4 { margin: 0 0 8px; color: #b42318; font-size: 14px; }
.conflict-row { padding: 8px 0; border-top: 1px solid #fee4e2; font-size: 13px; }
.conflict-row:first-of-type { border-top: 0; }
.conflict-row p { margin: 4px 0; color: #7a271a; }
.reroute-line code { padding: 1px 5px; background: #fef3f2; border-radius: 4px; }
.review-table { background: #fff; }
.scope-pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.scope-pill.valid { background: #ecfdf3; color: #027a48; }
.scope-pill.stale { background: #fffaeb; color: #b54708; }
.conflict-text, .accepted-text { font-size: 12px; margin: 6px 0 0; max-width: 320px; }
.conflict-text { color: #b54708; }
.accepted-text { color: #027a48; }
.muted { color: var(--muted); font-size: 12px; }
.link.danger { color: #b42318; }
</style>
