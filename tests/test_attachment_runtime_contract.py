from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_launcher_starts_document_worker():
    launcher = (ROOT / "一键启动.bat").read_text(encoding="utf-8")

    assert "import app.document_worker" in launcher
    assert '"%PYTHON_EXE%" -m app.document_worker' in launcher


def test_sales_contract_skill_keeps_tool_choice_with_the_model():
    skill = (
        ROOT
        / "backend"
        / "domain_packs"
        / "mold"
        / "skills"
        / "erp"
        / "commercial"
        / "sales_contract_intake"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "由模型根据当前 Run 的意图和数据库状态自行判断是否调用工具" in skill
    assert "宿主不得代替模型调用上述工具" in skill
