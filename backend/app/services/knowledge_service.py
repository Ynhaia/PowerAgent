"""版本化 Markdown 知识库的可解释检索服务。

当前版本使用本地关键词/短语评分，保证离线开发可测试和每条结论可解释。接口返回
KnowledgeCitation；后续接入 Embedding 与 pgvector 时只替换此服务的评分与存储实现，
不允许更改 Agent 的诊断根因、安全规则或引用契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from ..domain.schemas import KnowledgeCitation


class KnowledgeValidationError(ValueError):
    """知识文档缺少可追溯元数据或格式不合规。"""


@dataclass(frozen=True)
class KnowledgeChunk:
    document_id: str
    title: str
    version: str
    doc_type: str
    source_status: str
    applicable_units: tuple[str, ...]
    system: str
    access_level: str
    section_path: str
    content: str


class KnowledgeService:
    REQUIRED_METADATA = {
        "document_id",
        "title",
        "doc_type",
        "version",
        "applicable_units",
        "system",
        "access_level",
        "source_status",
    }
    ALLOWED_DOC_TYPES = {"operating_procedure", "technical_manual", "fault_case", "safety_notice"}
    ALLOWED_SOURCE_STATUSES = {"approved", "demo_verified"}
    _DOMAIN_TERMS = (
        "凝汽器",
        "真空",
        "绝对压力",
        "循环水",
        "循环水流量",
        "循环水泵",
        "抽气器",
        "空气泄漏",
        "换热",
        "结垢",
        "核查",
        "处置",
        "安全",
        "控制命令",
    )

    def __init__(self, knowledge_root: Path) -> None:
        self.knowledge_root = knowledge_root
        self._chunks = self._load_documents()

    @classmethod
    def default(cls) -> "KnowledgeService":
        project_root = Path(__file__).resolve().parents[3]
        return cls(project_root / "knowledge")

    def search(
        self,
        query: str,
        unit_id: str,
        system: str,
        top_k: int = 3,
        access_level: str = "operator",
    ) -> list[KnowledgeCitation]:
        if not query.strip():
            raise ValueError("知识检索问题不能为空")
        terms = self._terms(query)
        candidates: list[tuple[float, KnowledgeChunk]] = []
        for chunk in self._chunks:
            if unit_id not in chunk.applicable_units:
                continue
            if chunk.system != system:
                continue
            if not self._access_allowed(access_level, chunk.access_level):
                continue
            score = self._score(terms, chunk)
            if score > 0:
                candidates.append((score, chunk))
        candidates.sort(key=lambda item: (-item[0], item[1].document_id, item[1].section_path))
        return [self._citation(score, chunk) for score, chunk in candidates[:top_k]]

    def document_count(self) -> int:
        return len({chunk.document_id for chunk in self._chunks})

    def chunk_count(self) -> int:
        return len(self._chunks)

    def _load_documents(self) -> list[KnowledgeChunk]:
        if not self.knowledge_root.exists():
            raise KnowledgeValidationError(f"知识库目录不存在：{self.knowledge_root}")
        chunks: list[KnowledgeChunk] = []
        for path in sorted(self.knowledge_root.rglob("*.md")):
            if path.name.upper() == "README.MD":
                continue
            metadata, body = self._parse_document(path)
            chunks.extend(self._chunk_document(metadata, body, path))
        return chunks

    def _parse_document(self, path: Path) -> tuple[dict[str, Any], str]:
        text = path.read_text(encoding="utf-8")
        matched = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, flags=re.DOTALL)
        if not matched:
            raise KnowledgeValidationError(f"知识文档缺少 YAML Front Matter：{path.name}")
        metadata = yaml.safe_load(matched.group(1))
        if not isinstance(metadata, dict):
            raise KnowledgeValidationError(f"知识文档元数据必须为对象：{path.name}")
        missing = self.REQUIRED_METADATA - set(metadata)
        if missing:
            raise KnowledgeValidationError(f"知识文档缺少元数据 {sorted(missing)}：{path.name}")
        if metadata["doc_type"] not in self.ALLOWED_DOC_TYPES:
            raise KnowledgeValidationError(f"不支持的 doc_type：{path.name}")
        if metadata["source_status"] not in self.ALLOWED_SOURCE_STATUSES:
            raise KnowledgeValidationError(f"不允许的 source_status：{path.name}")
        if not isinstance(metadata["applicable_units"], list) or not metadata["applicable_units"]:
            raise KnowledgeValidationError(f"applicable_units 必须为非空列表：{path.name}")
        return metadata, matched.group(2).strip()

    def _chunk_document(self, metadata: dict[str, Any], body: str, path: Path) -> list[KnowledgeChunk]:
        sections = re.split(r"(?m)^(#{1,3})\s+(.+?)\s*$", body)
        chunks: list[KnowledgeChunk] = []
        if len(sections) == 1:
            sections = ["", "#", "正文", body]
        for index in range(1, len(sections), 3):
            heading = sections[index + 1].strip()
            content = sections[index + 2].strip()
            if not content:
                continue
            chunks.append(
                KnowledgeChunk(
                    document_id=str(metadata["document_id"]),
                    title=str(metadata["title"]),
                    version=str(metadata["version"]),
                    doc_type=str(metadata["doc_type"]),
                    source_status=str(metadata["source_status"]),
                    applicable_units=tuple(str(unit) for unit in metadata["applicable_units"]),
                    system=str(metadata["system"]),
                    access_level=str(metadata["access_level"]),
                    section_path=heading,
                    content=content,
                )
            )
        if not chunks:
            raise KnowledgeValidationError(f"知识文档没有可检索正文：{path.name}")
        return chunks

    def _terms(self, text: str) -> set[str]:
        normalized = text.lower()
        terms = {term.lower() for term in self._DOMAIN_TERMS if term.lower() in normalized}
        terms.update(token.lower() for token in re.findall(r"[a-zA-Z_]{3,}|[\u4e00-\u9fff]{2,}", text))
        return terms

    @staticmethod
    def _access_allowed(requested: str, document: str) -> bool:
        levels = {"operator": 1, "engineer": 2, "admin": 3}
        return levels.get(requested, 0) >= levels.get(document, 99)

    def _score(self, terms: set[str], chunk: KnowledgeChunk) -> float:
        haystack = f"{chunk.title}\n{chunk.section_path}\n{chunk.content}".lower()
        title = chunk.title.lower()
        section = chunk.section_path.lower()
        score = 0.0
        for term in terms:
            if term in haystack:
                weight = 1.0 + min(len(term), 8) / 10
                score += weight
                # 故障专篇与直接回答问题的章节应优于仅提及该词的总表。
                if term in title:
                    score += weight * 0.25
                if term in section:
                    score += weight * 0.5
        if score == 0:
            return 0.0
        if chunk.doc_type == "operating_procedure":
            score += 0.15
        if chunk.doc_type == "fault_case":
            score += 0.10
        return score

    @staticmethod
    def _citation(score: float, chunk: KnowledgeChunk) -> KnowledgeCitation:
        snippet = re.sub(r"\s+", " ", chunk.content).strip()
        return KnowledgeCitation(
            document_id=chunk.document_id,
            title=chunk.title,
            version=chunk.version,
            doc_type=chunk.doc_type,
            source_status=chunk.source_status,
            section_path=chunk.section_path,
            snippet=snippet[:240],
            score=round(score, 2),
        )
