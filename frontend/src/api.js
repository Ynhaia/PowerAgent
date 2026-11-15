const API_PREFIX = '/api/v1'

async function request(path, options = {}) {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { Accept: 'application/json', ...options.headers },
    ...options,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.detail || `请求失败：HTTP ${response.status}`)
  }
  return body
}

export const powerAgentApi = {
  health: () => request('/health'),
  units: () => request('/units'),
  trend: (unitId, tags, windowMinutes) => request(`/telemetry/trend?unit_id=${encodeURIComponent(unitId)}&tags=${encodeURIComponent(tags.join(','))}&window_minutes=${encodeURIComponent(windowMinutes)}`),
  alerts: (unitId, windowMinutes) => request(`/alerts?unit_id=${encodeURIComponent(unitId)}&window_minutes=${encodeURIComponent(windowMinutes)}`),
  diagnoseByChat: (message, unitId, windowMinutes) => request('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, unit_id: unitId, window_minutes: windowMinutes, input_mode: 'text' }),
  }),
  trace: (traceId) => request(`/traces/${encodeURIComponent(traceId)}`),
  report: async (unitId, windowMinutes, sourceTraceId) => {
    const response = await fetch(`${API_PREFIX}/reports/condenser-vacuum.docx`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unit_id: unitId, window_minutes: windowMinutes, source_trace_id: sourceTraceId || null }),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(body?.detail || `报告生成失败：HTTP ${response.status}`)
    }
    return {
      blob: await response.blob(),
      fileName: response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/)?.[1] || 'PowerAgent诊断报告.docx',
    }
  },
}
