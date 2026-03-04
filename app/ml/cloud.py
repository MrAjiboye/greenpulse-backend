"""
Cloud ML Integration
────────────────────
Pluggable cloud providers for GreenPulse.

Supported providers
───────────────────
  Google Vertex AI     — online prediction endpoint
  Google BigQuery ML   — batch SQL-based analysis
  AWS SageMaker        — online prediction endpoint

Usage
─────
  client = get_cloud_client()
  if client:
      result = client.predict(features)
  else:
      # fall back to local sklearn models

Configuration (via .env)
────────────────────────
  GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, VERTEX_AI_ENDPOINT_ID
  BIGQUERY_DATASET
  AWS_REGION, SAGEMAKER_ENDPOINT_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("greenpulse.ml.cloud")


# ── Abstract base ──────────────────────────────────────────────────────────────

class CloudMLClient(ABC):
    """Common interface for all cloud ML providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is configured and reachable."""

    @abstractmethod
    def predict(self, instances: list[list[float]]) -> list[float]:
        """
        Send feature vectors to the cloud endpoint and return predictions.

        Parameters
        ----------
        instances : list of feature vectors (each = list[float])

        Returns
        -------
        list[float] — one prediction per instance
        """

    @abstractmethod
    def health(self) -> dict:
        """Return provider health / metadata dict."""


# ── Google Vertex AI ───────────────────────────────────────────────────────────

class VertexAIClient(CloudMLClient):
    """
    Sends prediction requests to a deployed Vertex AI endpoint.

    Required config
    ───────────────
    GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, VERTEX_AI_ENDPOINT_ID
    GOOGLE_CLOUD_CREDENTIALS_JSON  (service-account JSON as a string)
    """

    provider_name = "Google Vertex AI"

    def __init__(self, project: str, region: str, endpoint_id: str, credentials_json: str | None):
        self.project       = project
        self.region        = region
        self.endpoint_id   = endpoint_id
        self._creds_json   = credentials_json
        self._endpoint     = None

    def _get_endpoint(self):
        if self._endpoint is not None:
            return self._endpoint
        try:
            from google.cloud import aiplatform
            from google.oauth2 import service_account

            creds = None
            if self._creds_json:
                info = json.loads(self._creds_json)
                creds = service_account.Credentials.from_service_account_info(info)

            aiplatform.init(project=self.project, location=self.region, credentials=creds)
            self._endpoint = aiplatform.Endpoint(self.endpoint_id)
        except Exception as e:
            logger.error("Vertex AI init failed: %s", e)
            self._endpoint = None
        return self._endpoint

    def is_available(self) -> bool:
        return self._get_endpoint() is not None

    def predict(self, instances: list[list[float]]) -> list[float]:
        ep = self._get_endpoint()
        if ep is None:
            raise RuntimeError("Vertex AI endpoint not initialised.")
        response = ep.predict(instances=instances)
        return [float(p) for p in response.predictions]

    def health(self) -> dict:
        return {
            "provider":    self.provider_name,
            "project":     self.project,
            "region":      self.region,
            "endpoint_id": self.endpoint_id,
            "available":   self.is_available(),
        }


# ── Google BigQuery ML ─────────────────────────────────────────────────────────

class BigQueryMLClient(CloudMLClient):
    """
    Runs ML predictions via BigQuery ML (SQL-based).
    Suitable for batch analysis of large historical datasets.

    Required config
    ───────────────
    GOOGLE_CLOUD_PROJECT, BIGQUERY_DATASET
    GOOGLE_CLOUD_CREDENTIALS_JSON
    """

    provider_name = "Google BigQuery ML"

    def __init__(self, project: str, dataset: str, credentials_json: str | None):
        self.project         = project
        self.dataset         = dataset
        self._creds_json     = credentials_json
        self._bq_client: Any = None

    def _get_client(self):
        if self._bq_client is not None:
            return self._bq_client
        try:
            from google.cloud import bigquery
            from google.oauth2 import service_account

            creds = None
            if self._creds_json:
                info = json.loads(self._creds_json)
                creds = service_account.Credentials.from_service_account_info(info)

            self._bq_client = bigquery.Client(project=self.project, credentials=creds)
        except Exception as e:
            logger.error("BigQuery client init failed: %s", e)
            self._bq_client = None
        return self._bq_client

    def is_available(self) -> bool:
        return self._get_client() is not None

    def predict(self, instances: list[list[float]]) -> list[float]:
        """
        Calls ML.PREDICT on a BigQuery ML model.
        The model must already be trained in BigQuery (CREATE MODEL ...).
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError("BigQuery client not initialised.")

        # Build a VALUES clause from instances
        rows = ", ".join(
            f"({', '.join(str(v) for v in row)})"
            for row in instances
        )
        query = f"""
            SELECT predicted_consumption_kwh
            FROM ML.PREDICT(
                MODEL `{self.project}.{self.dataset}.energy_forecast_model`,
                (SELECT * FROM UNNEST([STRUCT<f0 FLOAT64, f1 FLOAT64>
                    {rows}
                ]))
            )
        """
        job = client.query(query)
        return [float(row["predicted_consumption_kwh"]) for row in job.result()]

    def run_sql(self, sql: str) -> list[dict]:
        """Execute arbitrary BigQuery SQL and return rows as dicts."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("BigQuery client not initialised.")
        job = client.query(sql)
        return [dict(row) for row in job.result()]

    def health(self) -> dict:
        return {
            "provider":  self.provider_name,
            "project":   self.project,
            "dataset":   self.dataset,
            "available": self.is_available(),
        }


# ── AWS SageMaker ──────────────────────────────────────────────────────────────

class SageMakerClient(CloudMLClient):
    """
    Sends prediction requests to an AWS SageMaker real-time endpoint.

    Required config
    ───────────────
    AWS_REGION, SAGEMAKER_ENDPOINT_NAME
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    """

    provider_name = "AWS SageMaker"

    def __init__(
        self,
        region: str,
        endpoint_name: str,
        access_key: str,
        secret_key: str,
    ):
        self.region        = region
        self.endpoint_name = endpoint_name
        self._access_key   = access_key
        self._secret_key   = secret_key
        self._runtime: Any = None

    def _get_runtime(self):
        if self._runtime is not None:
            return self._runtime
        try:
            import boto3

            self._runtime = boto3.client(
                "sagemaker-runtime",
                region_name=self.region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
        except Exception as e:
            logger.error("SageMaker client init failed: %s", e)
            self._runtime = None
        return self._runtime

    def is_available(self) -> bool:
        return self._get_runtime() is not None

    def predict(self, instances: list[list[float]]) -> list[float]:
        runtime = self._get_runtime()
        if runtime is None:
            raise RuntimeError("SageMaker runtime not initialised.")

        payload = json.dumps({"instances": instances})
        response = runtime.invoke_endpoint(
            EndpointName=self.endpoint_name,
            ContentType="application/json",
            Body=payload,
        )
        body = json.loads(response["Body"].read())
        return [float(p) for p in body.get("predictions", [])]

    def health(self) -> dict:
        return {
            "provider":      self.provider_name,
            "region":        self.region,
            "endpoint_name": self.endpoint_name,
            "available":     self.is_available(),
        }


# ── Factory ────────────────────────────────────────────────────────────────────

def get_cloud_client() -> CloudMLClient | None:
    """
    Return the first configured cloud client, or None for local-only mode.
    Priority: Vertex AI → BigQuery ML → SageMaker
    """
    from app.config import settings

    # Google Vertex AI
    if getattr(settings, "VERTEX_AI_ENDPOINT_ID", None) and \
       getattr(settings, "GOOGLE_CLOUD_PROJECT", None):
        client = VertexAIClient(
            project=settings.GOOGLE_CLOUD_PROJECT,
            region=getattr(settings, "GOOGLE_CLOUD_REGION", "europe-west2"),
            endpoint_id=settings.VERTEX_AI_ENDPOINT_ID,
            credentials_json=getattr(settings, "GOOGLE_CLOUD_CREDENTIALS_JSON", None),
        )
        logger.info("Cloud provider: Vertex AI (project=%s)", settings.GOOGLE_CLOUD_PROJECT)
        return client

    # Google BigQuery ML
    if getattr(settings, "BIGQUERY_DATASET", None) and \
       getattr(settings, "GOOGLE_CLOUD_PROJECT", None):
        client = BigQueryMLClient(
            project=settings.GOOGLE_CLOUD_PROJECT,
            dataset=settings.BIGQUERY_DATASET,
            credentials_json=getattr(settings, "GOOGLE_CLOUD_CREDENTIALS_JSON", None),
        )
        logger.info("Cloud provider: BigQuery ML (project=%s)", settings.GOOGLE_CLOUD_PROJECT)
        return client

    # AWS SageMaker
    if getattr(settings, "SAGEMAKER_ENDPOINT_NAME", None) and \
       getattr(settings, "AWS_ACCESS_KEY_ID", None):
        client = SageMakerClient(
            region=getattr(settings, "AWS_REGION", "eu-west-1"),
            endpoint_name=settings.SAGEMAKER_ENDPOINT_NAME,
            access_key=settings.AWS_ACCESS_KEY_ID,
            secret_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        logger.info("Cloud provider: SageMaker (endpoint=%s)", settings.SAGEMAKER_ENDPOINT_NAME)
        return client

    logger.info("Cloud provider: none — using local sklearn models.")
    return None


def cloud_health() -> dict:
    """Return health info for all configured providers."""
    client = get_cloud_client()
    if client is None:
        return {"provider": "local", "available": True}
    return client.health()
