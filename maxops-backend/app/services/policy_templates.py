"""Pre-built policy templates."""
from typing import List, Dict, Any


POLICY_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "idle-ec2-instances",
        "name": "Idle EC2 Instances",
        "description": "Find EC2 instances that have been idle (low CPU/network) for 7+ days",
        "category": "idle-resources",
        "resource_type": "ec2",
        "filters_json": [
            {
                "type": "idle",
                "operator": "greater_than",
                "value": 7
            }
        ],
        # Keep YAML for backward compatibility
        "policy_yaml": """name: Idle EC2 Instances
description: Identify EC2 instances that have been idle for 7+ days
resource: aws.ec2
filters:
  - type: idle
    days: 7
    metrics: [CPUUtilization, NetworkIn, NetworkOut]
"""
    },
    {
        "id": "unattached-ebs-volumes",
        "name": "Unattached EBS Volumes",
        "description": "Find EBS volumes that are not attached to any instance",
        "category": "unused-resources",
        "resource_type": "ebs",
        "filters_json": [
            {
                "type": "unattached",
                "operator": "equals",
                "value": True
            }
        ],
        "policy_yaml": """name: Unattached EBS Volumes
description: Identify EBS volumes that are not attached to any EC2 instance
resource: aws.ebs
filters:
  - type: unattached
"""
    },
    {
        "id": "old-snapshots",
        "name": "Old EBS Snapshots",
        "description": "Find EBS snapshots older than 90 days",
        "category": "unused-resources",
        "resource_type": "snapshot",
        "filters_json": [
            {
                "type": "age",
                "operator": "greater_than",
                "value": 90
            }
        ],
        "policy_yaml": """name: Old EBS Snapshots
description: Identify EBS snapshots older than 90 days
resource: aws.snapshot
filters:
  - type: age
    days: 90
"""
    },
    {
        "id": "dev-environment-idle",
        "name": "Development Environment Idle Resources",
        "description": "Find idle EC2 instances in development environment",
        "category": "idle-resources",
        "resource_type": "ec2",
        "filters_json": [
            {
                "type": "tags",
                "operator": "equals",
                "value": {
                    "key": "Environment",
                    "value": "Development"
                }
            },
            {
                "type": "idle",
                "operator": "greater_than",
                "value": 3
            }
        ],
        "policy_yaml": """name: Development Environment Idle Resources
description: Identify idle EC2 instances in development environment
resource: aws.ec2
filters:
  - type: tag
    key: Environment
    value: Development
  - type: idle
    days: 3
    metrics: [CPUUtilization]
"""
    },
    {
        "id": "stopped-instances",
        "name": "Stopped EC2 Instances",
        "description": "Find EC2 instances that are stopped",
        "category": "unused-resources",
        "resource_type": "ec2",
        "filters_json": [
            {
                "type": "state",
                "operator": "equals",
                "value": "stopped"
            }
        ],
        "policy_yaml": """name: Stopped EC2 Instances
description: Identify EC2 instances that are currently stopped
resource: aws.ec2
filters:
  - type: state
    value: stopped
"""
    },
    {
        "id": "missing-cost-center-tag",
        "name": "Resources Missing Cost Center Tag",
        "description": "Find resources missing the required CostCenter tag",
        "category": "tag-compliance",
        "resource_type": "ec2",
        "filters_json": [
            {
                "type": "tags",
                "operator": "not_exists",
                "value": {
                    "key": "CostCenter"
                }
            }
        ],
        "policy_yaml": """name: Resources Missing Cost Center Tag
description: Identify resources missing the required CostCenter tag
resource: aws.ec2
filters:
  - type: tag
    key: CostCenter
    value: null
"""
    }
]


def get_template(template_id: str) -> Dict[str, Any]:
    """Get a policy template by ID."""
    for template in POLICY_TEMPLATES:
        if template["id"] == template_id:
            return template
    return None


def list_templates(category: str = None) -> List[Dict[str, Any]]:
    """List all policy templates, optionally filtered by category."""
    if category:
        return [t for t in POLICY_TEMPLATES if t.get("category") == category]
    return POLICY_TEMPLATES

