"""Every workflow argument must be accepted by the script it is passed to.

A wrong flag in a workflow only fails in CI, minutes into a scheduled run, and
only for the job that happens to execute that step. Parsing the workflows and
checking each invocation against the script's own parser catches it here
instead.
"""
from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))

# Scripts whose parsers this test knows how to build.
SCRIPT_MODULES = {
    "scripts/build_live_payload.py": "scripts.build_live_payload",
    "scripts/capture_consensus_odds.py": "scripts.capture_consensus_odds",
    "scripts/record_quotes.py": "scripts.record_quotes",
    "scripts/update_journal.py": "scripts.update_journal",
    "scripts/fetch_extra_fixtures.py": "scripts.fetch_extra_fixtures",
    "scripts/fetch_top5_fixtures.py": "scripts.fetch_top5_fixtures",
}


def _invocations() -> list[tuple[str, str, list[str]]]:
    """Yield (workflow, script path, flags) for each python script step."""
    found: list[tuple[str, str, list[str]]] = []
    for workflow in WORKFLOWS:
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job in (document.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                command = step.get("run")
                if not isinstance(command, str) or "python scripts/" not in command:
                    continue
                # Collapse the folded-scalar line breaks back into one command.
                flat = " ".join(command.split())
                match = re.search(r"python (scripts/[\w_]+\.py)", flat)
                if not match:
                    continue
                script = match.group(1)
                flags = re.findall(r"(?<!\S)(--[a-z0-9][\w-]*)", flat)
                found.append((workflow.name, script, flags))
    return found


def _known_flags(module_name: str) -> set[str]:
    """Build the script's parser and collect every option string it accepts."""
    module = importlib.import_module(module_name)
    captured: dict[str, argparse.ArgumentParser] = {}
    real_parse = argparse.ArgumentParser.parse_args

    def capture(self, *args, **kwargs):  # pragma: no cover - control flow only
        captured["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = capture
    try:
        module.main([])
    except SystemExit:
        pass
    finally:
        argparse.ArgumentParser.parse_args = real_parse
    parser = captured.get("parser")
    assert parser is not None, f"{module_name} did not build a parser"
    return {
        option
        for action in parser._actions
        for option in action.option_strings
    }


@pytest.mark.parametrize("workflow,script,flags", _invocations())
def test_workflow_flags_are_accepted_by_the_script(
    workflow: str, script: str, flags: list[str]
) -> None:
    module_name = SCRIPT_MODULES.get(script)
    if module_name is None:
        pytest.skip(f"{script} not covered by this check")
    known = _known_flags(module_name)
    unknown = [flag for flag in flags if flag not in known]
    assert not unknown, (
        f"{workflow} passes {unknown} to {script}, which does not accept them"
    )


def test_the_check_actually_found_invocations() -> None:
    """Guard against the parsing silently matching nothing."""
    covered = [row for row in _invocations() if row[1] in SCRIPT_MODULES]
    assert len(covered) >= 3
