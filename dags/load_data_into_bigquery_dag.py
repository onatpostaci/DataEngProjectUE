# Airflow imports as necessary
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 1, 31),
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5)
}

with DAG(
    'load_data_into_bigquery_dag',
    default_args=default_args,
    schedule_interval='@daily',
    catchup=False
) as dag_load_data:

    load_data_to_bq = GCSToBigQueryOperator(
        task_id='load_data_to_bq',
        bucket='de-final-bucket-1',  # Your GCS bucket name
        source_objects=['processed_stock_data/{{ ds }}.csv'],  # Correct path to the source data in CSV format
        destination_project_dataset_table='dataengineeringfinal-411911:ibm_stock.raw_stock_data',
        schema_fields=[
            {'name': 'date', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'open', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'high', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'low', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'close', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'volume', 'type': 'INTEGER', 'mode': 'NULLABLE'},
            {'name': 'moving_average', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'daily_return', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'volatility', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            # Include other fields as per your CSV file
        ],
        write_disposition='WRITE_TRUNCATE',
        source_format='CSV',  # Change source format to CSV
        skip_leading_rows=1,  # Assuming the first row is headers
        create_disposition='CREATE_IF_NEEDED',
        field_delimiter=',',  # Assuming comma as field delimiter in CSV
        dag=dag_load_data
    )




    # Task to perform transformations after data load, can be a SQL operation in BigQuery
    def perform_transformations(**kwargs):
        # Perform your transformations here
        # This is just a placeholder function
        print("Performing transformations in BigQuery...")

    transform_data = PythonOperator(
        task_id='transform_data',
        python_callable=perform_transformations,
        provide_context=True,
    )

    load_data_to_bq >> transform_data
