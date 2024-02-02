# Airflow imports as necessary
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook
import pandas as pd
from io import StringIO
from datetime import datetime, timedelta
import json

def process_and_save_to_gcs(ti, bucket_name, object_name_prefix, execution_date):
    # Fetch the data pushed to XComs by the fetch_stock_data task
    stock_data_json = ti.xcom_pull(task_ids='fetch_stock_data')
    
    # Parse the JSON into a DataFrame
    stock_data = json.loads(stock_data_json)
    print('------------------DATA-------------:', stock_data)
    df = pd.DataFrame.from_dict(stock_data["Time Series (Daily)"], orient='index')

    # Apply transformations
    df.index = pd.to_datetime(df.index)
    df.reset_index(inplace=True)
    df.rename(columns={'index': 'date'}, inplace=True)
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors='coerce')
    df['moving_average'] = df['4. close'].rolling(window=5).mean()
    df['daily_return'] = df['4. close'].pct_change()
    df['volatility'] = df['daily_return'].rolling(window=30).std() * (252 ** 0.5)
    df.fillna(0, inplace=True)
    df.rename(columns={
        "1. open": "open",
        "2. high": "high",
        "3. low": "low",
        "4. close": "close",
        "5. volume": "volume"
    }, inplace=True)
    
    # Convert the DataFrame to a CSV string
    csv_data = df.to_csv(index=False)

    # Initialize GCS hook
    gcs_hook = GCSHook()

    # Save the CSV data to GCS
    object_name = f"{object_name_prefix}/{execution_date}.csv"
    gcs_hook.upload(bucket_name=bucket_name, object_name=object_name, data=csv_data)

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
    'stock_analysis_to_gcs_dag',
    default_args=default_args,
    schedule_interval='@daily',
    catchup=False
) as dag_process_and_store_csv:

    is_api_available = HttpSensor(
        task_id='is_api_available',
        http_conn_id='alpha_vantage_api',
        endpoint='/query?function=TIME_SERIES_DAILY&symbol=IBM&apikey=RHT92DTOG8WTULV9',  # Update with the correct endpoint
        request_params={},  # Update with any necessary parameters
        response_check=lambda response: response.status_code == 200,
        poke_interval=5,
        timeout=20
    )

    fetch_stock_data = SimpleHttpOperator(
        task_id='fetch_stock_data',
        http_conn_id='alpha_vantage_api',
        endpoint='/query?function=TIME_SERIES_DAILY&symbol=IBM&apikey=RHT92DTOG8WTULV9',
        method='GET',
        headers={"Content-Type": "application/json"},
        response_filter=lambda response: response.text,
        do_xcom_push=True
    )

    process_and_save_data_to_gcs = PythonOperator(
        task_id='process_and_save_data_to_gcs',
        python_callable=process_and_save_to_gcs,
        op_kwargs={
            'bucket_name': 'de-final-bucket-1',  # Your GCS bucket name
            'object_name_prefix': 'processed_stock_data',
            'execution_date': '{{ ds }}'
        },
        provide_context=True,
    )

    is_api_available >> fetch_stock_data >> process_and_save_data_to_gcs
