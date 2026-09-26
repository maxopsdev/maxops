"""Action execution tracking model."""
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, Index
from sqlalchemy.sql import func
from app.database import Base


class ActionExecution(Base):
    """Tracks action executions on resources to prevent duplicate actions."""
    __tablename__ = "action_executions"
    
    id = Column(Integer, primary_key=True, index=True)
    check_id = Column(String(100), nullable=False, index=True)
    resource_id = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(100), nullable=True)
    action = Column(String(100), nullable=False)  # e.g., "Modify performance mode", "Delete file system"
    status = Column(String(20), default="running")  # running, completed, failed, cancelled
    account_id = Column(String(50), nullable=True)
    region = Column(String(50), nullable=True)
    parameters_json = Column(JSON, nullable=True)  # Action parameters
    details_json = Column(JSON, nullable=True)  # Response details from action
    message = Column(Text, nullable=True)  # Status message
    error_message = Column(Text, nullable=True)  # Error message if failed
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # For long-running actions (like EFS migration with DataSync)
    requires_polling = Column(String(20), nullable=True)  # e.g., "datasync_task_execution_arn"
    polling_identifier = Column(String(500), nullable=True)  # e.g., DataSync task execution ARN
    
    def __repr__(self):
        return f"<ActionExecution(id={self.id}, resource_id='{self.resource_id}', action='{self.action}', status='{self.status}')>"


Index(
    "idx_action_execution_resource_status",
    ActionExecution.resource_id,
    ActionExecution.status,
)
