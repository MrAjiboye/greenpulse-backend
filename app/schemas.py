import re
from pydantic import BaseModel, EmailStr, Field, computed_field, field_validator, ConfigDict
from typing import Optional, List
from datetime import datetime
from app.models import UserRole, InsightCategory, InsightStatus, NotificationType, GoalCategory

# ===== AUTH SCHEMAS =====
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str
    organization_name: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) > 72:
            raise ValueError("Password must be 72 characters or fewer")
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
    organization_id: Optional[int] = None  # Admin override only

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
    organization_id: Optional[int] = None  # Admin override only

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

# ===== GOAL SCHEMAS =====
class GoalCreate(BaseModel):
    name: str
    category: GoalCategory
    target_value: float
    unit: str
    period_start: datetime
    period_end: datetime

class GoalUpdate(BaseModel):
    name: Optional[str] = None
    target_value: Optional[float] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None

class GoalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    category: GoalCategory
    target_value: float
    unit: str
    period_start: datetime
    period_end: datetime
    actual_value: float = 0.0
    progress_pct: float = 0.0
    status: str = "on_track"
    created_at: datetime

# ===== TEAM SCHEMAS =====
class TeamInviteCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.VIEWER

class TeamMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    first_name: str
    last_name: str
    role: UserRole
    organization_id: Optional[int] = None
    created_at: datetime

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

class TeamInviteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    role: UserRole
    created_at: datetime
    expires_at: datetime
    accepted_at: Optional[datetime] = None

class AcceptInviteRequest(BaseModel):
    token: str
    first_name: str
    last_name: str
    password: str = Field(..., min_length=8)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) > 72:
            raise ValueError("Password must be 72 characters or fewer")
        if not re.search(r"[A-Z]", v): raise ValueError("Password needs an uppercase letter")
        if not re.search(r"[a-z]", v): raise ValueError("Password needs a lowercase letter")
        if not re.search(r"\d", v):    raise ValueError("Password needs a number")
        return v

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