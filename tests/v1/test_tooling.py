"""静态检查镜像内容与工程入口边界；不会构建镜像或启动数据库服务。"""

import json
from pathlib import Path
import shlex


ROOT = Path(__file__).resolve().parents[2]


def test_docker_image_only_copies_explicit_runtime_sources():
    instructions = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    copied = []
    for line in instructions:
        if line.startswith("COPY "):
            sources = shlex.split(line)[1:-1]
            assert sources
            copied.extend(sources)
    assert "src/order_pipeline/" in copied
    assert "sql/" in copied
    assert "scripts/verify_mysql.py" in copied
    for source in copied:
        assert (ROOT / source).exists(), f"镜像依赖不存在：{source}"
        assert source not in {".", "./", "src/", "scripts/"}
        assert not source.startswith(("data/", "learning/", "docs/", ".git", ".env", "tests/"))
    assert not any("day0" in source for source in copied)


def test_build_context_does_not_admit_raw_data_credentials_or_learning_history():
    patterns = [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]
    assert patterns[0] == "**", "构建上下文应默认拒绝，再显式开放必要文件"
    for pattern in patterns[1:]:
        if pattern.startswith("!"):
            assert not pattern[1:].startswith(("data", "learning", "docs", ".git", ".env", ".venv", "tests"))
    assert "!src/order_pipeline/**" in patterns
    assert "!scripts/verify_mysql.py" in patterns


def test_docker_default_invocation_uses_independent_raw_and_processed_paths():
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    entrypoint = json.loads(next(line[11:] for line in lines if line.startswith("ENTRYPOINT ")))
    command = json.loads(next(line[4:] for line in lines if line.startswith("CMD ")))
    assert entrypoint == ["python", "-m", "src.order_pipeline"]
    assert command[0] == "run"
    assert command[command.index("--input") + 1] == "/data/raw/orders.csv"
    assert command[command.index("--output") + 1].startswith("/data/processed/")
    assert command[command.index("--database") + 1].startswith("/data/processed/")
    assert "USER 10001:10001" in lines


def test_ci_checks_production_tests_and_has_separate_mysql_and_container_workflows():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.10", "3.12"]' in workflow
    assert "python -m pytest tests/v1 -q" in workflow
    assert "python -m ruff check src/order_pipeline scripts tests/v1" in workflow
    assert "python -m scripts.verify_mysql" in workflow
    assert 'MYSQL_INTEGRATION: "1"' in workflow
    assert "image: mysql:8.4" in workflow
    assert "docker build --tag order-pipeline:ci ." in workflow
    assert "target=/data/raw,readonly" in workflow
    assert "persist-credentials: false" in workflow
    assert "docker restart" in workflow
    assert "${{ secrets." not in workflow

