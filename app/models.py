from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum
import secrets

# Organisation Model
class Organization(Base):
    __tablename__ = "organizations"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String, nullable=False)
    iot_api_key = Column(String, unique=True, nullable=True, default=lambda: secrets.token_hex(32))
    plan       = Column(String, nullable=False, default="free", server_default="free")
    stripe_customer_id = Column(String, nullable=True)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    users           = relationship("User",          back_populates="organization")
    energy_readings = relationship("EnergyReading", back_populates="organization")
    waste_logs      = relationship("WasteLog",      back_populates="organization")
    insights        = relationship("Insight",       back_populates="organization")
    notifications   = relationship("Notification",  back_populates="organization")
    goals           = relationship("Goal",          back_populates="organization")


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    VIEWER = "VIEWER"

class InsightCategory(str, enum.Enum):
    ENERGY = "ENERGY"
    WASTE = "WASTE"
    OPERATIONS = "OPERATIONS"

class InsightStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    DISMISSED = "DISMISSED"

class NotificationType(str, enum.Enum):
    ALERT = "ALERT"
    WARNING = "WARNING"
    INSIGHT = "INSIGHT"
    SUCCESS = "SUCCESS"
    SYSTEM = "SYSTEM"

# User Model
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False, default="")
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    job_title = Column(String, nullable=True)
    department = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    oauth_provider = Column(String, nullable=True)
    oauth_sub = Column(String, nullable=True, index=True)
    role = Column(Enum(UserRole), default=UserRole.VIEWER)
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, nullable=False, default=False, server_default="0")
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    organization     = relationship("Organization",  back_populates="users")
    insights_actions = relationship("InsightAction", back_populates="user")

    @property
    def organization_name(self):
        return self.organization.name if self.organization else None

    @property
    def organization_iot_api_key(self):
        return self.organization.iot_api_key if self.organization else None

    @property
    def organization_plan(self):
        return self.organization.plan if self.organization else "free"

# Energy Reading Model
class EnergyReading(Base):
    __tablename__ = "energy_readings"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    consumption_kwh = Column(Float, nullable=False)
    zone = Column(String, nullable=False)
    facility_id = Column(Integer, default=1)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="energy_readings")

# Waste Log Model
class WasteLog(Base):
    __tablename__ = "waste_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    stream = Column(String, nullable=False)
    weight_kg = Column(Float, nullable=False)
    location = Column(String, nullable=False)
    contamination_detected = Column(Boolean, default=False)
    resolved = Column(Boolean, default=False)
    facility_id = Column(Integer, default=1)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="waste_logs")

# AI Insight Model
class Insight(Base):
    __tablename__ = "insights"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    category = Column(Enum(InsightCategory), nullable=False)
    confidence_score = Column(Float, nullable=False)
    estimated_savings = Column(Float, nullable=False)
    status = Column(Enum(InsightStatus), default=InsightStatus.PENDING)
    facility_id = Column(Integer, default=1)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="insights")
    actions      = relationship("InsightAction", back_populates="insight")

# Notification Model
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    type = Column(Enum(NotificationType), default=NotificationType.SYSTEM)
    read = Column(Boolean, default=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="notifications")


class GoalCategory(str, enum.Enum):
    ENERGY = "ENERGY"
    WASTE  = "WASTE"
    CARBON = "CARBON"


# Goal Model
class Goal(Base):
    __tablename__ = "goals"

    id              = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name            = Column(String, nullable=False)
    category        = Column(Enum(GoalCategory), nullable=False)
    target_value    = Column(Float, nullable=False)
    unit            = Column(String, nullable=False)
    period_start    = Column(DateTime(timezone=True), nullable=False)
    period_end      = Column(DateTime(timezone=True), nullable=False)
    created_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="goals")


# Team Invite Model
class TeamInvite(Base):
    __tablename__ = "team_invites"

    id              = Column(Integer, primary_key=True, index=True)
    email           = Column(String, nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    role            = Column(Enum(UserRole), default=UserRole.VIEWER)
    token_hash      = Column(String, nullable=False)
    invited_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    accepted_at     = Column(DateTime(timezone=True), nullable=True)
    expires_at      = Column(DateTime(timezone=True), nullable=False)


# Insight Action Model
class InsightAction(Base):
    __tablename__ = "insight_actions"
    
    id = Column(Integer, primary_key=True, index=True)
    insight_id = Column(Integer, ForeignKey("insights.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    insight = relationship("Insight", back_populates="actions")
    user = relationship("User", back_populates="insights_actions")