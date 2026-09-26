"""Policy-related database models."""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Policy(Base):
    """Policy model for storing cost optimization policies."""
    __tablename__ = "policies"
    
    id = Column(Integer, primary_key=True, index=True)
    policy_code = Column(String(6), unique=True, nullable=True, index=True)  # Unique 6-char ID (e.g., POLA1B)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    check_id = Column(String(100), nullable=True, index=True)  # Check function ID (e.g., 'ec2_idle_instances')
    parameters_json = Column(JSON, nullable=True)  # Check-specific parameters
    policy_yaml = Column(Text, nullable=True)  # DEPRECATED - kept for backward compatibility during migration
    filters_json = Column(JSON, nullable=True)  # DEPRECATED - kept for backward compatibility during migration
    resource_type = Column(String(100), nullable=False)  # e.g., ec2, rds, ebs
    status = Column(String(20), default="active")  # active, inactive
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    executions = relationship("PolicyExecution", back_populates="policy", cascade="all, delete-orphan")
    cost_savings = relationship("PolicyCostSavings", back_populates="policy", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Policy(id={self.id}, code={self.policy_code}, name='{self.name}', status='{self.status}')>"


class PolicyExecution(Base):
    """Policy execution record."""
    __tablename__ = "policy_executions"
    
    id = Column(Integer, primary_key=True, index=True)
    policy_id = Column(Integer, ForeignKey("policies.id"), nullable=False)
    execution_type = Column(String(20), default="dry-run")  # dry-run, apply (future)
    status = Column(String(20), default="running")  # running, completed, failed
    resources_found = Column(Integer, default=0)
    results_json = Column(JSON, nullable=True)  # Summary results
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    policy = relationship("Policy", back_populates="executions")
    results = relationship("PolicyExecutionResult", back_populates="execution", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<PolicyExecution(id={self.id}, policy_id={self.policy_id}, status='{self.status}')>"


class PolicyExecutionResult(Base):
    """Detailed results for each resource found by policy execution."""
    __tablename__ = "policy_execution_results"
    
    id = Column(Integer, primary_key=True, index=True)
    execution_id = Column(Integer, ForeignKey("policy_executions.id"), nullable=False)
    resource_id = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    resource_name = Column(String(255), nullable=True)
    region = Column(String(50), nullable=True)
    account_id = Column(String(50), nullable=True)
    reason = Column(Text, nullable=True)  # Why it matched the policy
    metadata_json = Column(JSON, nullable=True)  # Additional resource metadata
    
    # Relationships
    execution = relationship("PolicyExecution", back_populates="results")
    
    def __repr__(self):
        return f"<PolicyExecutionResult(id={self.id}, resource_id='{self.resource_id}')>"


class PolicyCostSavings(Base):
    """Cost savings tracking for policies over time."""
    __tablename__ = "policy_cost_savings"
    
    id = Column(Integer, primary_key=True, index=True)
    policy_id = Column(Integer, ForeignKey("policies.id"), nullable=False)
    execution_id = Column(Integer, ForeignKey("policy_executions.id"), nullable=True)  # Link to execution
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    cost_saved = Column(Float, nullable=False, default=0.0)  # Cost saved in USD
    resources_fixed = Column(Integer, default=0)  # Number of resources fixed/optimized
    notes = Column(Text, nullable=True)  # Optional notes about the savings
    
    # Relationships
    policy = relationship("Policy", back_populates="cost_savings")
    
    def __repr__(self):
        return f"<PolicyCostSavings(id={self.id}, policy_id={self.policy_id}, cost_saved={self.cost_saved})>"

