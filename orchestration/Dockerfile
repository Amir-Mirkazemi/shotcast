FROM apache/airflow:3.0.0-python3.12

# dbt and the pipeline code live in their own virtualenv so their dependencies
# never clash with Airflow's pinned ones.
COPY --chown=airflow:root requirements-pipeline.txt /tmp/requirements-pipeline.txt
RUN python -m venv /home/airflow/shotcast-venv \
 && /home/airflow/shotcast-venv/bin/pip install --no-cache-dir -r /tmp/requirements-pipeline.txt

ENV SHOTCAST_HOME=/opt/shotcast \
    SHOTCAST_PYTHON=/home/airflow/shotcast-venv/bin/python \
    SHOTCAST_DBT=/home/airflow/shotcast-venv/bin/dbt
