import re
from pydantic import BaseModel, EmailStr, Field, computed_field, field_validator
from typing import Optional, List
from datetime import datetime
from app.models import UserRole, InsightCategory, InsightStatus, NotificationType

# ===== AUTH SCHEMAS =====
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str
    organization_name: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        errors = []
        if len(v) < 8:
            errors.append("at least 8 characters")
        if not re.search(r"[A-Z]", v):
            errors.append("one uppercase letter")
        if not re.search(r"[a-z]", v):
            errors.append("one lowercase letter")
        if not re.search(r"\d", v):
            errors.append("one number")
        if not re.search(r"[^A-Za-z0-9]", v):
            errors.append("one special character (!@#$%^&* etc.)")
        if errors:
            raise ValueError("Password must contain " + ", ".join(errors))
        return v

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    job_title: Optional[str] = None
    department: Optional[str] = None
    company_name: Optional[str] = None
    role: UserRole
    is_active: bool
    email_verified: bool
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    organization_iot_api_key: Optional[str] = None
    created_at: datetime

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    company_name: Optional[str] = None
    organization_name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = Field(None, min_length=8)

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)

# ===== ADMIN SCHEMAS =====
class UserRoleUpdate(BaseModel):
    role: UserRole

class UserAdminResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    role: UserRole
    is_active: bool
    created_at: datetime

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    class Config:
        from_attributes = True

# ===== DASHBOARD SCHEMAS =====
class DashboardStats(BaseModel):
    current_energy_kwh: float
    total_savings: float
    insights_applied: int
    carbon_reduced_tons: float

# ===== ENERGY SCHEMAS =====
class EnergyReadingCreate(BaseModel):
    timestamp: datetime
    consumption_kwh: float
    zone: str
    facility_id: int = 1

class EnergyReadingResponse(BaseModel):
    id: int
    timestamp: datetime
    consumption_kwh: float
    zone: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# ===== WASTE SCHEMAS =====
class WasteLogCreate(BaseModel):
    timestamp: datetime
    stream: str
    weight_kg: float
    location: str
    contamination_detected: bool = False

class WasteLogResponse(BaseModel):
    id: int
    timestamp: datetime
    stream: str
    weight_kg: float
    location: str
    contamination_detected: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

# ===== INSIGHT SCHEMAS =====
class InsightCreate(BaseModel):
    title: str
    description: str
    category: InsightCategory
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    estimated_savings: float

class InsightResponse(BaseModel):
    id: int
    title: str
    description: str
    category: InsightCategory
    confidence_score: float
    estimated_savings: float
    status: InsightStatus
    created_at: datetime
    
    class Config:
        from_attributes = True

class InsightActionCreate(BaseModel):
    action: str
    reason: Optional[str] = None

# ===== NOTIFICATION SCHEMAS =====
class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: NotificationType
    read: bool
    created_at: datetime

    class Config:
        from_attributes = True