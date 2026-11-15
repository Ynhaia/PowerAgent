"""自动化测试固定离线配置，避免学生已填写的 .env 导致测试扣费或访问外部库。"""

import os

for key, value in {
    "POWERAGENT_LLM_API_KEY": "",
    "POWERAGENT_DATA_SOURCE": "mock",
    "POWERAGENT_MOCK_SCENARIO": "cw_flow_insufficient",
    "POWERAGENT_TRACE_STORE": "memory",
    "POWERAGENT_DATABASE_URL": "",
}.items():
    os.environ[key] = value
