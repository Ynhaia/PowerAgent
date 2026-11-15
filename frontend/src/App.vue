<script setup>
import { computed, onMounted, ref } from 'vue'
import { powerAgentApi } from './api'

const DIAGNOSIS_PROMPT = '分析 1 号机最近两小时凝汽器真空为什么下降，并给出处置建议'
const HISTORY_STORAGE_KEY = 'poweragent.condenser-diagnosis-history.v1'
const unitId = ref('U01')
const windowMinutes = ref(120)
const units = ref([])
const health = ref(null)
const trend = ref(null)
const alerts = ref([])
const chat = ref(null)
const trace = ref([])
const loading = ref(false)
const loadingTrace = ref(false)
const loadingReport = ref(false)
const error = ref('')
const refreshedAt = ref('')
const question = ref(DIAGNOSIS_PROMPT)
const showTrace = ref(false)
const diagnosisHistory = ref(loadHistory())

const diagnosis = computed(() => chat.value?.data?.diagnosis ?? null)
const canDownloadReport = computed(() => diagnosis.value && diagnosis.value.result_status !== 'insufficient_data')
const resultLabel = computed(() => ({ candidate_found: '发现候选原因', normal: '未触发专题异常', inconclusive: '根因证据不足', insufficient_data: '数据不足，暂停诊断' })[diagnosis.value?.result_status] ?? '待执行')
const primaryCause = computed(() => diagnosis.value?.candidates?.[0] ?? null)
const citations = computed(() => diagnosis.value?.knowledge_citations ?? [])
const pressureSeries = computed(() => trend.value?.series?.find((item) => item.tag_code === 'COND_ABS_PRESSURE'))
const flowSeries = computed(() => trend.value?.series?.find((item) => item.tag_code === 'CW_FLOW'))
const latestPressure = computed(() => lastPoint(pressureSeries.value)?.value)
const latestFlow = computed(() => lastPoint(flowSeries.value)?.value)
const riskLabel = computed(() => ({ low: '低', medium: '中', high: '高', critical: '紧急' })[diagnosis.value?.risk_level] ?? '--')
const runMode = computed(() => health.value?.run_mode ?? '--')
const pressureLabel = computed(() => latestPressure.value == null ? '等待有效数据' : latestPressure.value > 5.55 ? '高于演示预警阈值 5.55 kPa(a)' : '未超过演示预警阈值 5.55 kPa(a)')
const flowLabel = computed(() => latestFlow.value == null ? '等待有效数据' : latestFlow.value < 30000 ? '低于演示阈值 30,000 t/h' : '未低于演示阈值 30,000 t/h')
const pressureState = computed(() => latestPressure.value == null ? '待加载' : latestPressure.value > 5.55 ? '压力越限' : '未越限')
const chartDomain = computed(() => {
  const values = pressureSeries.value?.points?.map((item) => item.value) ?? []
  const min = Math.min(5.55, ...values) - 0.05
  const max = Math.max(5.55, ...values) + 0.05
  return { min, max }
})
const thresholdY = computed(() => 188 - ((5.55 - chartDomain.value.min) / (chartDomain.value.max - chartDomain.value.min)) * 176)
const chartPoints = computed(() => makeChartPoints(pressureSeries.value?.points ?? []))
const chartLabels = computed(() => {
  const points = pressureSeries.value?.points ?? []
  return points.length ? [formatTime(points[0].timestamp), formatTime(points.at(-1).timestamp)] : ['--', '--']
})

function lastPoint(series) {
  return series?.points?.at(-1)
}

function number(value, digits = 2) {
  return typeof value === 'number' ? value.toFixed(digits) : '--'
}

function formatTime(value) {
  if (!value) return '--'
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}

function formatDateTime(value) {
  if (!value) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

function makeChartPoints(points) {
  if (!points.length) return ''
  const { min, max } = chartDomain.value
  const width = 720
  const height = 200
  const padding = 12
  return points.map((point, index) => {
    const x = padding + (index / Math.max(points.length - 1, 1)) * (width - padding * 2)
    const y = height - padding - ((point.value - min) / Math.max(max - min, 0.001)) * (height - padding * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

async function loadMonitoring() {
  loading.value = true
  error.value = ''
  try {
    const [healthData, unitData, trendData, alertData] = await Promise.all([
      powerAgentApi.health(),
      powerAgentApi.units(),
      powerAgentApi.trend(unitId.value, ['COND_ABS_PRESSURE', 'CW_FLOW'], windowMinutes.value),
      powerAgentApi.alerts(unitId.value, windowMinutes.value),
    ])
    health.value = healthData
    units.value = unitData
    trend.value = trendData
    alerts.value = alertData
    refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (exception) {
    trend.value = null
    alerts.value = []
    error.value = exception instanceof Error ? exception.message : '无法连接 PowerAgent 后端服务。'
  } finally {
    loading.value = false
  }
}

async function runDiagnosis() {
  loading.value = true
  error.value = ''
  trace.value = []
  showTrace.value = false
  chat.value = null
  try {
    chat.value = await powerAgentApi.diagnoseByChat(question.value || DIAGNOSIS_PROMPT, unitId.value, windowMinutes.value)
    addHistory(chat.value)
    refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : '诊断任务执行失败。'
  } finally {
    loading.value = false
  }
}

function loadHistory() {
  try {
    const stored = JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) || '[]')
    return Array.isArray(stored) ? stored.slice(0, 8) : []
  } catch {
    return []
  }
}

function addHistory(result) {
  const primary = result?.data?.diagnosis?.candidates?.[0]
  if (!primary) return
  const entry = {
    traceId: result.trace_id,
    createdAt: new Date().toISOString(),
    windowMinutes: result.plan.window_minutes,
    riskLevel: result.data.diagnosis.risk_level,
    causeName: primary.cause_name,
    ruleScore: primary.rule_score,
    question: question.value || DIAGNOSIS_PROMPT,
    result,
  }
  diagnosisHistory.value = [entry, ...diagnosisHistory.value.filter((item) => item.traceId !== entry.traceId)].slice(0, 8)
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(diagnosisHistory.value))
  } catch {
    // 浏览器隐私模式或配额不足时，保留本次会话历史，不影响诊断。
  }
}

function restoreHistory(entry) {
  chat.value = entry.result
  windowMinutes.value = entry.windowMinutes
  question.value = entry.question || DIAGNOSIS_PROMPT
  trace.value = []
  showTrace.value = false
  loadMonitoring()
}

function changeContext() {
  chat.value = null
  trace.value = []
  showTrace.value = false
  loadMonitoring()
}

async function downloadReport() {
  if (!canDownloadReport.value) return
  loadingReport.value = true
  error.value = ''
  try {
    const { blob, fileName } = await powerAgentApi.report(chat.value.plan.unit_id, chat.value.plan.window_minutes, chat.value.trace_id)
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = fileName
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(href)
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : '诊断报告下载失败。'
  } finally {
    loadingReport.value = false
  }
}

async function loadTrace() {
  if (!chat.value?.trace_id) return
  loadingTrace.value = true
  error.value = ''
  try {
    trace.value = (await powerAgentApi.trace(chat.value.trace_id)).events
    showTrace.value = true
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : 'Trace 查询失败。'
  } finally {
    loadingTrace.value = false
  }
}

onMounted(loadMonitoring)
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">P</div>
        <div>
          <p class="eyebrow">POWERAGENT · 本科毕业设计 · 演示环境</p>
          <h1>电厂智能预警与故障诊断</h1>
        </div>
      </div>
      <div class="header-status">
        <span class="status-dot"></span>
        <span>服务 {{ health?.status === 'ok' ? '正常' : '待连接' }}</span>
        <span class="divider"></span>
        <span>数据源：{{ runMode }}</span>
      </div>
    </header>

    <section class="hero panel">
      <div>
        <p class="eyebrow">专题工作台 / U01</p>
        <h2>1 号机凝汽器真空下降</h2>
        <p class="hero-copy">监盘、预警、规则诊断与知识引用在同一条可回放链路中完成。</p>
      </div>
      <div class="hero-actions">
        <label class="field-label" for="unit-select">当前机组</label>
        <select id="unit-select" v-model="unitId" :disabled="loading" @change="changeContext">
          <option v-for="unit in units" :key="unit.unit_id" :value="unit.unit_id">{{ unit.unit_name }}（{{ unit.unit_id }}）</option>
          <option v-if="!units.length" value="U01">1 号机组（U01）</option>
        </select>
        <label class="field-label" for="window-select">分析窗口</label>
        <select id="window-select" v-model.number="windowMinutes" :disabled="loading" @change="changeContext">
          <option :value="30">最近 30 分钟</option>
          <option :value="60">最近 60 分钟</option>
          <option :value="120">最近 120 分钟</option>
          <option :value="240">最近 240 分钟</option>
        </select>
        <button class="primary-button" :disabled="loading" @click="runDiagnosis">
          {{ loading ? '正在执行…' : '执行诊断' }}
        </button>
      </div>
    </section>

    <p v-if="error" class="error-banner" role="alert">{{ error }}。请确认后端已运行在 <code>127.0.0.1:8000</code>。</p>

    <section class="metric-grid" aria-label="核心运行指标">
      <article class="metric-card pressure-card">
        <p>凝汽器绝对压力</p>
        <strong>{{ number(latestPressure) }}</strong><span> kPa(a)</span>
        <small>{{ pressureLabel }}</small>
      </article>
      <article class="metric-card flow-card">
        <p>循环水流量</p>
        <strong>{{ number(latestFlow, 0) }}</strong><span> t/h</span>
        <small>{{ flowLabel }}</small>
      </article>
      <article class="metric-card risk-card" :class="`risk-${diagnosis?.risk_level ?? 'unknown'}`">
        <p>当前风险等级</p>
        <strong>{{ riskLabel }}</strong>
        <small>需人工确认，系统不执行自动控制</small>
      </article>
      <article class="metric-card agent-card">
        <p>Agent 规划来源</p>
        <strong class="agent-value">{{ chat?.planner_source ?? '--' }}</strong>
        <small>{{ health?.llm_enabled ? '云端规划器可用，失败时自动降级' : '当前使用确定性规则降级' }}</small>
      </article>
    </section>

    <section class="workbench-grid">
      <article class="panel trend-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">趋势监盘</p>
            <h3>凝汽器绝对压力 · 最近 {{ windowMinutes }} 分钟</h3>
          </div>
          <span class="pill neutral">{{ pressureState }}</span>
        </div>
        <div class="chart-wrap" aria-label="凝汽器绝对压力趋势图">
          <svg viewBox="0 0 720 200" preserveAspectRatio="none" role="img">
            <line x1="12" y1="34" x2="708" y2="34" class="grid-line" />
            <line x1="12" y1="100" x2="708" y2="100" class="grid-line" />
            <line x1="12" y1="166" x2="708" y2="166" class="grid-line" />
            <polyline v-if="chartPoints" :points="chartPoints" class="pressure-line" />
            <line x1="12" :y1="thresholdY" x2="708" :y2="thresholdY" stroke="#e6b871" stroke-dasharray="6 4" />
          </svg>
          <small>虚线：演示预警阈值 5.55 kPa(a)；纵轴范围 {{ number(chartDomain.min) }} 至 {{ number(chartDomain.max) }} kPa(a)</small>
          <div class="chart-axis"><span>{{ chartLabels[0] }}</span><span>{{ chartLabels[1] }}</span></div>
        </div>
        <div class="trend-summary">
          <div><span>变化斜率</span><strong>{{ number(trend?.analysis?.COND_ABS_PRESSURE?.slope_per_minute, 4) }} kPa/min</strong></div>
          <div><span>异常点</span><strong>{{ trend?.analysis?.COND_ABS_PRESSURE?.anomaly_points ?? '--' }} 个</strong></div>
          <div><span>循环水流量</span><strong>{{ number(latestFlow, 0) }} t/h</strong></div>
        </div>
      </article>

      <article class="panel alert-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">关联预警</p>
            <h3>{{ alerts.length }} 条待核查事件</h3>
          </div>
          <span class="pill neutral">同一专题</span>
        </div>
        <ol class="alert-list">
          <li v-for="alert in alerts" :key="alert.alert_id" :class="`severity-${alert.severity}`">
            <span class="alert-time">{{ formatTime(alert.start_time) }}</span>
            <div>
              <strong>{{ alert.message }}</strong>
              <p>{{ alert.tag_code }} · 观测值 {{ number(alert.observed_value) }} / 阈值 {{ number(alert.threshold_value) }}</p>
            </div>
            <span class="severity-label">{{ alert.severity }}</span>
          </li>
          <li v-if="!alerts.length" class="empty-state">暂无符合当前窗口的关联预警。</li>
        </ol>
      </article>
    </section>

    <section class="panel diagnosis-panel">
      <div class="panel-heading diagnosis-heading">
        <div>
          <p class="eyebrow">受控诊断结论</p>
          <h3>{{ primaryCause?.cause_name ?? '等待诊断结果' }}</h3>
          <p class="summary-copy">{{ diagnosis?.summary ?? '系统将先获取趋势与预警，再运行确定性专题规则。' }}</p>
        </div>
        <div class="score-box">
          <span>规则评分</span>
          <strong>{{ primaryCause?.rule_score ?? '--' }}</strong>
          <small>规则支持度 {{ primaryCause ? `${Math.round(primaryCause.confidence * 100)}%` : '--' }}（非故障概率）</small>
          <small>{{ resultLabel }}</small>
        </div>
      </div>

      <div class="diagnosis-columns">
        <div>
          <h4>诊断证据</h4>
          <ul class="evidence-list">
            <li v-for="evidence in primaryCause?.evidence ?? []" :key="`${evidence.evidence_type}-${evidence.label}`">
              <span class="evidence-type">{{ evidence.evidence_type }}</span>
              <div><strong>{{ evidence.label }}</strong><p>{{ evidence.detail }}</p></div>
            </li>
            <li v-if="!primaryCause" class="empty-state">尚未取得可展示的诊断证据。</li>
          </ul>
        </div>
        <div>
          <h4>人工核查建议</h4>
          <ol class="steps-list">
            <li v-for="step in primaryCause?.verification_steps ?? []" :key="step">{{ step }}</li>
            <li v-if="!primaryCause" class="empty-state">等待规则诊断完成。</li>
          </ol>
          <p class="safety-notice">{{ diagnosis?.safety_notice ?? '所有处置建议均须由现场授权人员确认，不向 DCS、SIS 或 PLC 下发指令。' }}</p>
        </div>
      </div>
    </section>

    <section class="lower-grid">
      <article class="panel knowledge-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">RAG 知识依据</p><h3>版本化、可追溯引用</h3></div>
          <span class="pill neutral">{{ citations.length }} 条</span>
        </div>
        <div v-for="citation in citations" :key="citation.document_id + citation.section_path" class="citation-card">
          <div><span class="document-id">{{ citation.document_id }}</span><strong>{{ citation.title }}</strong></div>
          <p>{{ citation.snippet }}</p>
          <small>{{ citation.version }} · {{ citation.section_path }} · <b>{{ citation.source_status }}</b></small>
        </div>
        <p v-if="!citations.length" class="empty-state">没有可展示的知识引用时，页面不会生成规程性结论。</p>
      </article>

      <article class="panel agent-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">Agent 执行链路</p><h3>可解释、可回放</h3></div>
          <span class="pill success">{{ chat?.trace_id ? '已生成 Trace' : '待执行' }}</span>
        </div>
        <label class="field-label" for="question">诊断任务</label>
        <textarea id="question" v-model="question" rows="3" :disabled="loading"></textarea>
        <div class="agent-actions">
          <button class="secondary-button" :disabled="loadingTrace || !chat?.trace_id" @click="loadTrace">
            {{ loadingTrace ? '读取中…' : '回放 Trace' }}
          </button>
          <button class="secondary-button" :disabled="loadingReport || !canDownloadReport" @click="downloadReport">
            {{ loadingReport ? '正在生成…' : diagnosis?.result_status === 'insufficient_data' ? '数据不足，暂不可导出' : '下载 DOCX 报告' }}
          </button>
          <button class="text-button" :disabled="loading" @click="runDiagnosis">执行此任务</button>
        </div>
        <p class="agent-answer">{{ chat?.answer ?? '系统将执行受控工具调用，不允许模型直接访问数据库或控制系统。' }}</p>
        <div v-if="showTrace" class="trace-list">
          <div v-for="event in trace" :key="`${event.node_name}-${event.created_at}`" class="trace-event">
            <span class="trace-node">{{ event.node_name }}</span>
            <span>{{ event.status }}</span>
            <time>{{ formatDateTime(event.created_at) }}</time>
          </div>
          <p v-if="!trace.length" class="empty-state">当前 Trace 尚无可显示节点。</p>
        </div>
        <div class="history-list">
          <div class="history-heading"><span>本浏览器诊断历史</span><small>最多保留 8 条，不上传服务器</small></div>
          <button v-for="entry in diagnosisHistory" :key="entry.traceId" class="history-item" @click="restoreHistory(entry)">
            <span class="history-risk" :class="`history-${entry.riskLevel}`">{{ ({ low: '低', medium: '中', high: '高', critical: '紧急' })[entry.riskLevel] }}</span>
            <span><b>{{ entry.causeName }}</b><small>{{ entry.windowMinutes }} 分钟 · 评分 {{ entry.ruleScore }}</small></span>
            <time>{{ formatDateTime(entry.createdAt) }}</time>
          </button>
          <p v-if="!diagnosisHistory.length" class="empty-state">执行诊断后将在此保留本浏览器的最近记录。</p>
        </div>
      </article>
    </section>

    <footer>
      <span>PowerAgent V4.1 · {{ unitId }} 凝汽器真空下降专题</span>
      <span>最近刷新：{{ refreshedAt || '--' }}</span>
      <span>演示数据与 <code>demo_verified</code> 知识仅用于开发验证</span>
    </footer>
  </main>
</template>
