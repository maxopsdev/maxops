"""Records for cost-data setup jobs."""
from sqlalchemy import Column, DateTime, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.database import Base


class CurSetupJob(Base):
    """One run of a cost-data setup step.

    Setup work happens in the background — creating the export talks to four
    AWS services, and refreshing the cache runs Athena queries that can take
    minutes. The page polls these rows for progress, so a browser refresh or a
    backend restart never loses track of what was started.
    """

    __tablename__ = "cur_setup_jobs"

    id = Column(Integer, primary_key=True, index=True)
    # "export" (create the CUR export and Athena table) or "refresh" (rebuild
    # the local Parquet cost cache).
    job_type = Column(String(32), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="running")  # running, completed, failed
    # {"phase": str, "message": str, "current": int, "total": int, "updated_at": iso}
    progress_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<CurSetupJob(id={self.id}, job_type='{self.job_type}', status='{self.status}')>"
