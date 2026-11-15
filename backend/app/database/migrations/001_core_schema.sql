-- PowerAgent V4.1 第二阶段基础数据模型。
-- 执行账号需要目标数据库的 DDL 权限；运行账号不得拥有该权限。

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS plant_unit (
    unit_id VARCHAR(32) PRIMARY KEY,
    unit_name VARCHAR(100) NOT NULL,
    unit_type VARCHAR(50) NOT NULL DEFAULT 'thermal',
    rated_capacity_mw NUMERIC(10, 2),
    status VARCHAR(20) NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'stopped', 'maintenance')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tag_definition (
    tag_code VARCHAR(80) PRIMARY KEY,
    unit_id VARCHAR(32) NOT NULL REFERENCES plant_unit(unit_id),
    tag_name VARCHAR(160) NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    system_name VARCHAR(80) NOT NULL,
    unit_name VARCHAR(30),
    min_value NUMERIC,
    max_value NUMERIC,
    warning_low NUMERIC,
    warning_high NUMERIC,
    alarm_low NUMERIC,
    alarm_high NUMERIC,
    setpoint_value NUMERIC,
    is_core_diagnosis_tag BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tag_definition_unit_system ON tag_definition(unit_id, system_name);

CREATE TABLE IF NOT EXISTS telemetry_point (
    id BIGSERIAL PRIMARY KEY,
    unit_id VARCHAR(32) NOT NULL REFERENCES plant_unit(unit_id),
    tag_code VARCHAR(80) NOT NULL REFERENCES tag_definition(tag_code),
    ts TIMESTAMPTZ NOT NULL,
    value NUMERIC(16, 5) NOT NULL,
    quality_code VARCHAR(20) NOT NULL DEFAULT 'GOOD'
        CHECK (quality_code IN ('GOOD', 'BAD', 'UNCERTAIN')),
    source VARCHAR(30) NOT NULL DEFAULT 'gateway',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(unit_id, tag_code, ts)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_lookup ON telemetry_point(unit_id, tag_code, ts DESC);

CREATE TABLE IF NOT EXISTS alert_event (
    alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id VARCHAR(32) NOT NULL REFERENCES plant_unit(unit_id),
    tag_code VARCHAR(80) REFERENCES tag_definition(tag_code),
    alert_type VARCHAR(30) NOT NULL
        CHECK (alert_type IN ('threshold', 'residual', 'rate_of_change', 'equipment')),
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
    observed_value NUMERIC,
    predicted_value NUMERIC,
    setpoint_value NUMERIC,
    threshold_value NUMERIC,
    residual_value NUMERIC,
    rate_of_change NUMERIC,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cleared', 'acknowledged')),
    message TEXT NOT NULL,
    related_alert_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_time IS NULL OR end_time >= start_time)
);
CREATE INDEX IF NOT EXISTS idx_alert_event_window ON alert_event(unit_id, start_time DESC, severity);

CREATE TABLE IF NOT EXISTS fault_case (
    case_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_name VARCHAR(200) NOT NULL,
    system_name VARCHAR(80) NOT NULL,
    symptoms JSONB NOT NULL,
    evidence_pattern JSONB NOT NULL,
    root_causes JSONB NOT NULL,
    verification_steps JSONB NOT NULL,
    recommended_actions JSONB NOT NULL,
    risk_level VARCHAR(20) NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    source_status VARCHAR(20) NOT NULL DEFAULT 'verified' CHECK (source_status IN ('draft', 'verified', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_document (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(300) NOT NULL,
    doc_type VARCHAR(40) NOT NULL CHECK (doc_type IN ('operating_procedure', 'technical_manual', 'fault_case', 'safety_notice')),
    version VARCHAR(40),
    source_uri TEXT,
    effective_date DATE,
    access_level VARCHAR(30) NOT NULL DEFAULT 'operator',
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_chunk (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES knowledge_document(document_id),
    section_path VARCHAR(300),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_document ON knowledge_chunk(document_id);

CREATE TABLE IF NOT EXISTS conversation_session (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(80) NOT NULL,
    unit_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS execution_trace (
    trace_event_id BIGSERIAL PRIMARY KEY,
    trace_id UUID NOT NULL,
    request_id UUID NOT NULL,
    session_id UUID REFERENCES conversation_session(session_id),
    user_id VARCHAR(80),
    intent VARCHAR(60),
    node_name VARCHAR(60) NOT NULL,
    input_payload JSONB,
    output_payload JSONB,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(20) NOT NULL CHECK (status IN ('started', 'succeeded', 'failed', 'blocked')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX IF NOT EXISTS idx_execution_trace_request ON execution_trace(request_id, started_at);
CREATE INDEX IF NOT EXISTS idx_execution_trace_trace ON execution_trace(trace_id, trace_event_id);

CREATE TABLE IF NOT EXISTS model_registry (
    model_version VARCHAR(80) PRIMARY KEY,
    model_type VARCHAR(40) NOT NULL,
    target_tag VARCHAR(80) NOT NULL REFERENCES tag_definition(tag_code),
    train_data_ref JSONB NOT NULL,
    metrics JSONB NOT NULL,
    baseline_version VARCHAR(80),
    improvement_pct NUMERIC,
    status VARCHAR(20) NOT NULL CHECK (status IN ('candidate', 'active', 'retired', 'rejected')),
    approved_by VARCHAR(80),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
