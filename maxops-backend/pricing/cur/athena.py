"""Athena execution helpers for CUR aggregate refreshes."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import pandas as pd


@dataclass(frozen=True)
class AthenaConfig:
    database: str
    output_location: str
    workgroup: str = "primary"
    region: str = "us-east-1"
    profile: Optional[str] = None


def create_session(profile: Optional[str] = None):
    import boto3

    if profile:
        return boto3.Session(profile_name=profile)
    return boto3.Session()


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Expected S3 URI, got: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def run_athena_query(session, query: str, config: AthenaConfig, poll_seconds: int = 2) -> str:
    athena = session.client("athena", region_name=config.region)
    response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": config.database},
        ResultConfiguration={"OutputLocation": config.output_location},
        WorkGroup=config.workgroup,
    )
    query_execution_id = response["QueryExecutionId"]

    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_execution_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            return execution["ResultConfiguration"]["OutputLocation"]
        if state in {"FAILED", "CANCELLED"}:
            reason = execution["Status"].get("StateChangeReason", "No reason provided")
            raise RuntimeError(f"Athena query {query_execution_id} {state}: {reason}")
        time.sleep(poll_seconds)


def download_athena_csv(session, output_location: str, region: str) -> pd.DataFrame:
    bucket, key = parse_s3_uri(output_location)
    s3 = session.client("s3", region_name=region)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pd.read_csv(io.BytesIO(body))
