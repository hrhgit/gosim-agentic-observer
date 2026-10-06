"""Organizer-tunable dispatcher limits (20261004100000)."""
import psycopg
import pytest

from test_project_database import database, query, rpc, setup  # noqa: F401


def test_defaults_rollback_and_bounds(setup):
    uri = setup["uri"]
    limits = rpc(uri, "observer_dispatch_limits")
    assert {k: limits[k] for k in ("runs", "jobs", "scores", "max_passes")} == {"runs": 10, "jobs": 20, "scores": 20, "max_passes": 8}
    old = query(uri, "select public.observer_set_dispatch_limits(3,5,5,10,1,40)", role="service_role")[0][0]
    assert [old[k] for k in ("preparations", "runs", "scores", "jobs", "max_passes", "pass_seconds")] == [3, 5, 5, 10, 1, 40]
    with pytest.raises(psycopg.Error, match="check"):
        query(uri, "select public.observer_set_dispatch_limits(p_runs=>11)", role="service_role")
    with pytest.raises(psycopg.Error, match="check"):
        query(uri, "select public.observer_set_dispatch_limits(p_pass_seconds=>50)", role="service_role")
    for role in ("authenticated", "anon"):
        with pytest.raises(psycopg.Error, match="permission denied"):
            query(uri, "select public.observer_dispatch_limits()", role=role, user=setup["user"])
    query(uri, "select public.observer_set_dispatch_limits(6,10,20,20,8,40)", role="service_role")
