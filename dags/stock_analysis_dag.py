import os
import json
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.google.cloud.operators.bigquery import BigQueryCreateEmptyTableOperator, BigQueryInsertJobOperator
from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook
from airflow.models import Variable
import pandas as pd
import openai


def get_and_store_gpt_advice(ti):
    #openai organization key
    openai.organizatio = Variable.get("openai_organizatio")
    #openai api key as env variable
    openai.api_key = Variable.get("openai_api_key")
    
    descriptive_data = ti.xcom_pull(task_ids='perform_descriptive_analysis')
    
    # Prepare the prompt for GPT-3 based on descriptive_data
    prompt = f"Given the following stock descriptive statistics: {json.dumps(descriptive_data, indent=2)}, please give insights about the stock This stock is IBM you can use your own knowledge."
    
    # Generate suggestion from GPT-3
    response = openai.Completion.create(
        engine='gpt-3.5-turbo-instruct',
        prompt=prompt,
        max_tokens=200,
    )
    
    advice = response["choices"][0]["text"].strip()
    
    # Store the advice in BigQuery
    hook = BigQueryHook(bigquery_conn_id='google_cloud_default', use_legacy_sql=False)
    client = hook.get_client()
    dataset_id = 'ibm_stock'
    table_id = 'ibm_stock_advice_table'
    project_id = 'dataengineeringfinal-411911'
    
    table_ref = client.dataset(dataset_id, project=project_id).table(table_id)
    rows_to_insert = [{
        "advice": advice,
        "date": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    }]
    
    errors = client.insert_rows_json(table_ref, rows_to_insert)
    if errors:
        raise Exception(f"Errors occurred while inserting rows: {errors}")

# Define the default arguments for the DAG
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2024, 1, 31),
    "email": ['onatpostac@gmail.com'],
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5)
}

# Define the DAG
with DAG(
    'stock_analysis_dag',
    default_args=default_args,
    schedule_interval='@daily',
    catchup=False
) as dag:
   
    # Create an HTTP sensor task to check Alpha Vantage API availability
    is_alpha_vantage_api_ready = HttpSensor(
        task_id='is_alpha_vantage_api_ready',
        http_conn_id='alpha_vantage_api',
        endpoint='/query?function=TIME_SERIES_DAILY&symbol=IBM&apikey=RHT92DTOG8WTULV9',
        method='GET',
        response_check=lambda response: response.status_code == 200,
    )
   
    # Task to fetch stock data from Alpha Vantage API
    fetch_stock_data = SimpleHttpOperator(
        task_id='fetch_stock_data',
        http_conn_id='alpha_vantage_api',
        endpoint='/query?function=TIME_SERIES_DAILY&symbol=IBM&apikey=RHT92DTOG8WTULV9',
        method='GET',
        headers={"Content-Type": "application/json"},
        response_filter=lambda response: response.text,
    )
    
    # Task to process the stock data and upload to BigQuery
    def process_stock_data(ti):
        # Fetch the data pushed to XComs by the previous task
        stock_data_json = ti.xcom_pull(task_ids='fetch_stock_data')
        print(stock_data_json)

        # Convert JSON to DataFrame
        stock_data = json.loads(stock_data_json)
       
        df = pd.DataFrame.from_dict(stock_data["Time Series (Daily)"], orient='index')

        # Convert index to datetime and reset index to make 'date' a column
        df.index = pd.to_datetime(df.index)
        df.reset_index(inplace=True)
        df.rename(columns={'index': 'date'}, inplace=True)

        # Convert 'date' column to string format to ensure JSON serializability
        df['date'] = df['date'].dt.strftime('%Y-%m-%d')

        # Continue with your processing...
        df.iloc[:, 1:] = df.iloc[:, 1:].apply(pd.to_numeric, errors='coerce')
        df['moving_average'] = df['4. close'].rolling(window=5).mean()
        df['daily_return'] = df['4. close'].pct_change()
        df['volatility'] = df['daily_return'].rolling(window=30).std() * (252 ** 0.5)
        df.fillna(0, inplace=True)

        # Rename columns to match BigQuery column names
        df.rename(columns={
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume"
        }, inplace=True)


        # Convert DataFrame to a list of dictionaries to facilitate upload to BigQuery
        records = df.to_dict('records')

        # Use the BigQuery Hook to insert data into BigQuery
        hook = BigQueryHook(bigquery_conn_id='google_cloud_default', use_legacy_sql=False)
        client = hook.get_client()
        dataset_id = 'ibm_stock'
        table_id = 'ibm_Stock_table'
        project_id = 'dataengineeringfinal-411911'
        dataset_ref = client.dataset(dataset_id, project=project_id)
        table_ref = dataset_ref.table(table_id)

        # Load the data to BigQuery
        errors = client.insert_rows_json(table_ref, records)
        if errors:
            raise Exception(f"Errors occurred while inserting rows: {errors}")



    def perform_descriptive_analysis(ti):
        # Fetch the data pushed to XComs by the fetch_stock_data task
        stock_data_json = ti.xcom_pull(task_ids='fetch_stock_data')
        
        # Convert JSON to DataFrame
        stock_data = json.loads(stock_data_json)
        df = pd.DataFrame.from_dict(stock_data["Time Series (Daily)"], orient='index')
        # Rename the columns to a valid name
        df.rename(columns={
                "1. open": "open",
                "2. high": "high",
                "3. low": "low",
                "4. close": "close",
                "5. volume": "volume"
        }, inplace=True)

        # Perform descriptive analysis
        statistics = df.describe()

        # Convert the statistics DataFrame to a dictionary for each column
        statistics_dict = statistics.to_dict()
        
        # Transform statistics_dict to the format compatible with BigQuery schema
        transformed_data = []
        for attribute, stats in statistics_dict.items():
            transformed_data.append({
                "attribute": attribute,
                "count": stats["count"],
                "unique": stats["unique"],  # Unique and Top are not derived from describe(), handling separately
                "top": stats["top"],
                "freq": stats["freq"]   
            })

        # Use the BigQuery Hook to insert statistics into ibm_statistics_table
        hook = BigQueryHook(bigquery_conn_id='google_cloud_default', use_legacy_sql=False)
        client = hook.get_client()
        dataset_id = 'ibm_stock'
        table_id = 'ibm_statistics_table'
        project_id = 'dataengineeringfinal-411911'
        dataset_ref = client.dataset(dataset_id, project=project_id)
        table_ref = dataset_ref.table(table_id)
        
        # Load the statistics data to BigQuery
        errors = client.insert_rows_json(table_ref, transformed_data)
        if errors:
            raise Exception(f"Errors occurred while inserting statistics: {errors}")
   
    process_stock_data_task = PythonOperator(
        task_id='process_stock_data',
        python_callable=process_stock_data,
        provide_context=True,
    )
   
    perform_descriptive_analysis_task = PythonOperator(
        task_id='perform_descriptive_analysis',
        python_callable=perform_descriptive_analysis,
        provide_context=True,
    )

    get_and_store_gpt_advice_task = PythonOperator(
        task_id='get_and_store_gpt_advice',
        python_callable=get_and_store_gpt_advice,
        provide_context=True,
    )

    # Set up dependencies
    is_alpha_vantage_api_ready >> fetch_stock_data >> process_stock_data_task >> perform_descriptive_analysis_task >> get_and_store_gpt_advice_task