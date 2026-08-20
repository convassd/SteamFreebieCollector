from pathlib import Path


def test_registration_script_has_logon_and_daily_triggers():
    script = (Path(__file__).parents[1] / "scripts" / "Register-Tasks.ps1").read_text(encoding="utf-8")
    assert "New-ScheduledTaskTrigger -AtLogOn" in script
    assert "New-ScheduledTaskTrigger -Daily -At '21:00'" in script
    assert "New-ScheduledTaskTrigger -Daily -At '20:55'" not in script
    assert script.count("-WakeToRun") == 1
    assert "-MultipleInstances IgnoreNew" in script
    assert "-RestartCount 3" in script
    assert "Disable-ScheduledTask" in script
    assert "Remove legacy standalone ASF task" in script


def test_collector_task_uses_automatic_mode_and_project_venv():
    script = (Path(__file__).parents[1] / "scripts" / "Register-Tasks.ps1").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in script
    assert "-m steam_freebie_collector run --mode automatic --scheduled" in script
