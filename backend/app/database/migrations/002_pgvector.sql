-- 可选迁移：只有数据库已安装 pgvector 扩展时才执行。
-- 维度必须与后续选定的 Embedding 模型保持一致；本设计暂以 1536 为契约。

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE knowledge_chunk
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_embedding_hnsw
    ON knowledge_chunk USING hnsw (embedding vector_cosine_ops);

COMMIT;

