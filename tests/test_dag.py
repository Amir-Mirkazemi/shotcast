"""The DAG file must import cleanly and keep its task order. Skipped when Airflow isn't installed.

Imports the file directly (rather than via DagBag) so the test works across Airflow versions.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("airflow")

DAG_FILE = Path(__file__).resolve().parents[1] / "orchestration" / "dags" / "shotcast_daily.py"


def load_dag():
    spec = importlib.util.spec_from_file_location("shotcast_daily", DAG_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.dag


def test_dag_imports_and_has_expected_shape():
    dag = load_dag()
    assert dag.dag_id == "shotcast_daily"
    order = {t.task_id: sorted(t.downstream_task_ids) for t in dag.tasks}
    assert order == {
        "ingest_raw": ["build_bronze"],
        "build_bronze": ["dbt_build"],
        "dbt_build": ["dbt_summary"],
        "dbt_summary": [],
    }
    assert dag.catchup is False and dag.max_active_runs == 1


def test_summary_runs_even_if_dbt_fails():
    dag = load_dag()
    rule = dag.get_task("dbt_summary").trigger_rule
    assert getattr(rule, "value", rule) == "all_done"
