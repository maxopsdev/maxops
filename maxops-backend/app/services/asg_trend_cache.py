"""Process-local cache for ASG confidence trends."""

from app.services.ec2_trend_cache import EC2TrendCache


asg_trend_cache = EC2TrendCache()
