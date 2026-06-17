import sys
from cc.extract.endpoints import extract_endpoints
from cc.oracle import compare_oracle
from tests.conftest import SIMPLE_API


def test_oracle_recovery_rate():
    sys.path.insert(0, str(SIMPLE_API.parent))
    try:
        ep_nodes, _ = extract_endpoints(SIMPLE_API)
        result = compare_oracle(SIMPLE_API, ep_nodes)

        assert "recovery_rate" in result
        assert "static_count" in result
        assert "oracle_count" in result
        assert result["recovery_rate"] >= 0.5  # at least 50% recovery
    finally:
        sys.path.pop(0)


def test_oracle_finds_all_routes_in_fixture():
    sys.path.insert(0, str(SIMPLE_API.parent))
    try:
        ep_nodes, _ = extract_endpoints(SIMPLE_API)
        result = compare_oracle(SIMPLE_API, ep_nodes)

        assert result["recovery_rate"] == 1.0  # fixture is simple, should be 100%
    finally:
        sys.path.pop(0)
