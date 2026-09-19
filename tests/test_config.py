from config import load_config, reload_config


def test_defaults(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("JEV_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cfg = load_config()
    assert cfg.compaction_min_chars == 12000
    assert cfg.goal_satisfied_min == 0.90
    assert cfg.autonomous_plan_enabled is False
    assert cfg.model.startswith("typesafe:")


def test_jev_latest(monkeypatch):
    monkeypatch.setenv("JEV_MODEL", "jev-latest")
    cfg = reload_config()
    assert cfg.model == "typesafe:jev-latest"


def test_thresholds_env(monkeypatch):
    monkeypatch.setenv("JEV_GOAL_SATISFIED_MIN", "0.5")
    cfg = reload_config()
    assert cfg.goal_satisfied_min == 0.5
