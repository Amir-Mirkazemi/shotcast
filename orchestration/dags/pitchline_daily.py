"""Daily Pitchline pipeline: download new raw files, build Parquet, run dbt (models + tests).

Each step is a separate task so a failure shows exactly where it happened and can be retried
on its own. The summary task runs even when dbt tests fail, so the app's health page stays current.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

HOME = os.environ.get("PITCHLINE_HOME", "/opt/pitchline")
PY = os.environ.get("PITCHLINE_PYTHON", "python")
DBT = os.environ.get("PITCHLINE_DBT", "dbt")
ENV = {
    "PYTHONPATH": f"{HOME}/src",
    "DBT_PROFILES_DIR": f"{HOME}/dbt",
    "PITCHLINE_ROOT": os.environ.get("PITCHLINE_ROOT", f"{HOME}/data"),
    "PITCHLINE_BRONZE": os.environ.get("PITCHLINE_BRONZE", f"{HOME}/data/bronze"),
    "PITCHLINE_WAREHOUSE": os.environ.get("PITCHLINE_WAREHOUSE", f"{HOME}/warehouse/pitchline.duckdb"),
    "PITCHLINE_HEALTH": os.environ.get("PITCHLINE_HEALTH", f"{HOME}/health"),
}

with DAG(
    dag_id="pitchline_daily",
    description="Ingest StatsBomb open data, build bronze Parquet, run dbt build",
    schedule="0 9 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5), "env": ENV, "cwd": HOME},
    tags=["pitchline"],
    doc_md=__doc__,
) as dag:
    ingest_raw = BashOperator(task_id="ingest_raw", bash_command=f"{PY} -m pitchline ingest")
    build_bronze = BashOperator(task_id="build_bronze", bash_command=f"{PY} -m pitchline bronze")
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {HOME}/dbt && {DBT} build --exclude tag:espn",
    )
    dbt_summary = BashOperator(
        task_id="dbt_summary",
        bash_command=f"{PY} -m pitchline dbt-summary --dbt-results {HOME}/dbt/target/run_results.json",
        trigger_rule="all_done",
    )

    ingest_raw >> build_bronze >> dbt_build >> dbt_summary
