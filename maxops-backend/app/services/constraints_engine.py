"""Policy & Constraints Engine - applies guardrails and business rules."""
from typing import Dict, Any, List, Optional
from datetime import datetime


class ConstraintsEngine:
    """Engine for applying business rules and guardrails."""
    
    def __init__(self):
        """Initialize constraints engine."""
        # Default business rules
        self.default_rules = {
            "never_touch_prod": True,
            "min_observation_days": 14,
            "keep_backups_days": 30,
            "pci_compliance_no_auto_change": True,
            "ha_minimum_instances": 2,
            "quarantine_before_delete": True,
            "quarantine_days": 7,
        }
    
    def apply_constraints(
        self,
        resource: Dict[str, Any],
        finding: Dict[str, Any],
        business_rules: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Apply constraints and business rules to a finding.
        
        Args:
            resource: Resource information
            finding: Finding details
            business_rules: Optional custom business rules
            
        Returns:
            Dictionary with constraint results
        """
        rules = {**self.default_rules, **(business_rules or {})}
        
        constraints = {
            "can_modify": True,
            "requires_approval": False,
            "blocked_by": [],
            "warnings": [],
            "approval_reason": None
        }
        
        # Check production guardrail
        is_prod = self._is_production(resource)
        if is_prod and rules.get("never_touch_prod", True):
            constraints["can_modify"] = False
            constraints["blocked_by"].append("production_resource")
            constraints["approval_reason"] = "Production resource - manual approval required"
            return constraints
        
        # Check observation window
        observation_days = finding.get("observation_days", 0)
        min_days = rules.get("min_observation_days", 14)
        if observation_days < min_days:
            constraints["can_modify"] = False
            constraints["blocked_by"].append(f"insufficient_observation_window")
            constraints["approval_reason"] = f"Only {observation_days} days of data (minimum {min_days} required)"
            return constraints
        
        # Check compliance tags
        compliance_tags = self._get_compliance_tags(resource)
        if "PCI" in compliance_tags and rules.get("pci_compliance_no_auto_change", True):
            constraints["requires_approval"] = True
            constraints["warnings"].append("PCI-compliant resource - requires approval")
            constraints["approval_reason"] = "PCI compliance requirement"
        
        # Check HA minimum
        if finding.get("finding_type") == "right-size":
            current_count = resource.get("metadata", {}).get("instance_count", 1)
            ha_min = rules.get("ha_minimum_instances", 2)
            if current_count <= ha_min:
                constraints["warnings"].append(f"At HA minimum ({current_count} instances)")
                if current_count < ha_min:
                    constraints["can_modify"] = False
                    constraints["blocked_by"].append("below_ha_minimum")
        
        # Check for recent incidents
        if finding.get("recent_incident", False):
            constraints["requires_approval"] = True
            constraints["warnings"].append("Recent incident detected - extra caution required")
            if not constraints["approval_reason"]:
                constraints["approval_reason"] = "Recent incident - manual review required"
        
        # Check for unknown owner
        if not resource.get("tags", {}).get("Owner"):
            constraints["requires_approval"] = True
            constraints["warnings"].append("Unknown owner - approval required")
            if not constraints["approval_reason"]:
                constraints["approval_reason"] = "Resource owner unknown"
        
        return constraints
    
    def check_change_window(
        self,
        scheduled_time: Optional[datetime] = None,
        blackout_periods: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Check if change can be executed in the requested window.
        
        Args:
            scheduled_time: Proposed execution time
            blackout_periods: List of blackout periods
            
        Returns:
            Dictionary with window check results
        """
        if not scheduled_time:
            scheduled_time = datetime.utcnow()
        
        result = {
            "allowed": True,
            "reason": None,
            "next_available": None
        }
        
        if not blackout_periods:
            return result
        
        # Check against blackout periods
        for period in blackout_periods:
            start = period.get("start")
            end = period.get("end")
            
            if start and end and start <= scheduled_time <= end:
                result["allowed"] = False
                result["reason"] = f"Blackout period: {start} to {end}"
                result["next_available"] = end
                return result
        
        return result
    
    def validate_quarantine_requirements(
        self,
        resource_type: str,
        action: str,
        rules: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Validate quarantine requirements for delete operations.
        
        Args:
            resource_type: Type of resource
            action: Action to perform
            rules: Optional custom rules
            
        Returns:
            Dictionary with quarantine requirements
        """
        rules = {**self.default_rules, **(rules or {})}
        
        if action != "delete":
            return {"requires_quarantine": False}
        
        requires_quarantine = rules.get("quarantine_before_delete", True)
        quarantine_days = rules.get("quarantine_days", 7)
        
        return {
            "requires_quarantine": requires_quarantine,
            "quarantine_days": quarantine_days if requires_quarantine else 0,
            "steps": [
                "Detach resource from service",
                f"Create snapshot/backup",
                f"Retain for {quarantine_days} days",
                "Monitor for issues",
                "Delete after quarantine period"
            ] if requires_quarantine else []
        }
    
    def _is_production(self, resource: Dict[str, Any]) -> bool:
        """Check if resource is production."""
        tags = resource.get("tags", {})
        env = tags.get("Environment", "").lower()
        return env == "production" or tags.get("env", "").lower() == "prod"
    
    def _get_compliance_tags(self, resource: Dict[str, Any]) -> List[str]:
        """Extract compliance tags from resource."""
        tags = resource.get("tags", {})
        compliance = []
        
        compliance_keywords = ["PCI", "HIPAA", "SOC2", "GDPR", "ISO27001"]
        for keyword in compliance_keywords:
            if keyword in str(tags).upper():
                compliance.append(keyword)
        
        return compliance


