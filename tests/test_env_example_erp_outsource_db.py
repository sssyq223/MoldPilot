from pathlib import Path


def _example_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, raw = text.split("=", 1)
        values[key.strip()] = raw.strip()
    return values


def test_env_example_ships_lan_erp_readonly_database_without_local_erp_checkout():
    env = _example_env()
    assert env.get("MOLD_ERP_ENV_FILE", "") == ""
    assert env["MOLD_ERP_DB_HOST"] == "192.168.3.60"
    assert env["MOLD_ERP_DB_PORT"] == "5432"
    assert env["MOLD_ERP_DB_USERNAME"]
    assert env["MOLD_ERP_DB_PASSWORD"]
    assert env["MOLD_ERP_DB_DATABASE"] == "erp"
