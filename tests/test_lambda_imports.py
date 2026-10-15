"""The approval Lambda ships without LangGraph, PyYAML or Typst; its code must not import them."""

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
CHECK = """
import sys
class Block:
    def find_spec(self, name, path, target=None):
        if name.split('.')[0] in BLOCKED:
            raise ImportError('not in the Lambda package: ' + name)
sys.meta_path.insert(0, Block())
import approval_api.handler, approval_api.api, approval_api.control, runner.handler
"""


def test_lambda_code_imports_without_agent_only_packages():
    blocked = (
        "BLOCKED = {'langgraph', 'langchain_core', 'langgraph_checkpoint_aws', 'yaml', 'typst'}"
    )
    result = subprocess.run(
        [sys.executable, "-c", blocked + CHECK], cwd=SRC, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_lambda_code_has_no_lazy_agent_only_imports():
    for path in [*(SRC / "approval_api").glob("*.py"), SRC / "runner" / "handler.py"]:
        text = path.read_text()
        for module in ("agent.graphs", "agent.tailoring", "agent.documents", "agent.profile"):
            assert module not in text, f"{path.name} imports {module}"
