"""Policy ID generator for unique 6-character identifiers."""
import random
import string
from sqlalchemy.orm import Session
from app.models.policy import Policy


def generate_policy_code(db: Session, max_attempts: int = 100) -> str:
    """
    Generate a unique 6-character policy code.
    
    Format: POL + 3 alphanumeric characters (e.g., POLA1B, POLX9Z)
    
    Args:
        db: Database session
        max_attempts: Maximum attempts to generate unique ID
        
    Returns:
        Unique policy code
        
    Raises:
        ValueError: If unable to generate unique ID after max_attempts
    """
    pending_codes = {
        item.policy_code
        for item in db.new
        if isinstance(item, Policy) and item.policy_code
    }

    for _ in range(max_attempts):
        # Generate 3 random alphanumeric characters
        chars = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
        policy_code = f"POL{chars}"

        if policy_code in pending_codes:
            continue
        
        # Check uniqueness
        existing = db.query(Policy).filter(Policy.policy_code == policy_code).first()
        if not existing:
            return policy_code
    
    raise ValueError(f"Unable to generate unique policy code after {max_attempts} attempts")

