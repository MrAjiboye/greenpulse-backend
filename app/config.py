from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # App
    APP_NAME: str = "GreenPulse API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Database
    DATABASE_URL: str = "sqlite:///./greenpulse.db"
    
    # Security
    SECRET_KEY: str = "your-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # CORS — comma-separated list of allowed origins
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # AI
    OPENAI_API_KEY: Optional[str] = None

    # ML Engine
    ML_AUTO_RETRAIN: bool = True
    ML_RETRAIN_EVERY_N_READINGS: int = 100
    IOT_API_KEY: Optional[str] = None

    # Google Cloud ML (Vertex AI + BigQuery ML)
    GOOGLE_CLOUD_PROJECT: Optional[str] = None
    GOOGLE_CLOUD_REGION: str = "europe-west2"
    GOOGLE_CLOUD_CREDENTIALS_JSON: Optional[str] = None
    VERTEX_AI_ENDPOINT_ID: Optional[str] = None
    BIGQUERY_DATASET: Optional[str] = None

    # AWS SageMaker
    AWS_REGION: Optional[str] = None
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    SAGEMAKER_ENDPOINT_NAME: Optional[str] = None

    # OAuth — Google
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"

    # OAuth — Microsoft
    MICROSOFT_CLIENT_ID: Optional[str] = None
    MICROSOFT_CLIENT_SECRET: Optional[str] = None
    MICROSOFT_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/microsoft/callback"

    # Frontend base URL (used to redirect after OAuth)
    FRONTEND_URL: str = "http://localhost:5173"

    # Email — Resend
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "no-reply@support.greenpulseanalytics.com"
    FROM_NAME: str = "GreenPulse"

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()