def test_seed_adds_only_finished_history(tmp_path):
    from evals.seed_demo import seed
    from server.services.execution.registry import AgentRegistry, set_agent_registry

    reg = AgentRegistry(tmp_path / "agents.db")
    reg.get_or_create("Email to Linda – Sunday call")
    set_agent_registry(reg)
    try:
        assert seed(300) == 300
        assert reg.count() == 301 and reg.count_open() == 0
        assert reg.get(1).name == "Email to Linda – Sunday call"  # existing agents untouched
    finally:
        set_agent_registry(None)
