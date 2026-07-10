import pytest

from agentproof.autofix import autofix
from agentproof.scenarios import generate_scenarios
from agentproof.simulator import run_suite
from agentproof.spec import parse_spec
from agentproof.studio import DEFAULT_SPEC
from agentproof.synthesis import synthesize


@pytest.fixture(scope="session")
def spec():
    return parse_spec(DEFAULT_SPEC)


@pytest.fixture(scope="session")
def naive_graph(spec):
    return synthesize(spec)


@pytest.fixture(scope="session")
def scenarios(spec):
    return generate_scenarios(spec)


@pytest.fixture(scope="session")
def naive_results(naive_graph, spec, scenarios):
    return run_suite(naive_graph, spec, scenarios)


@pytest.fixture(scope="session")
def fix_report(naive_graph, spec, naive_results):
    return autofix(naive_graph, spec, naive_results)


@pytest.fixture(scope="session")
def fixed_graph(fix_report):
    return fix_report.graph


@pytest.fixture(scope="session")
def fixed_results(fixed_graph, spec, scenarios):
    return run_suite(fixed_graph, spec, scenarios)
