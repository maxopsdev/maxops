"""ElastiCache action handlers."""
from __future__ import annotations

import logging
from datetime import datetime
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def _get_elasticache_resource(elasticache, resource_id: str):
    """Helper function to identify and fetch ElastiCache resource type."""
    resource_kind = None
    replication_group = None
    cache_cluster = None
    target_resource_id = resource_id
    
    try:
        rg_response = elasticache.describe_replication_groups(ReplicationGroupId=resource_id)
        if rg_response.get("ReplicationGroups"):
            resource_kind = "replication_group"
            replication_group = rg_response["ReplicationGroups"][0]
    except Exception:
        pass

    if resource_kind is None:
        try:
            cluster_response = elasticache.describe_cache_clusters(
                CacheClusterId=resource_id,
                ShowCacheNodeInfo=True,
            )
            if cluster_response.get("CacheClusters"):
                cache_cluster = cluster_response["CacheClusters"][0]
                cluster_rg_id = cache_cluster.get("ReplicationGroupId")
                if cluster_rg_id:
                    try:
                        rg_response = elasticache.describe_replication_groups(
                            ReplicationGroupId=cluster_rg_id
                        )
                        if rg_response.get("ReplicationGroups"):
                            resource_kind = "replication_group"
                            replication_group = rg_response["ReplicationGroups"][0]
                            target_resource_id = cluster_rg_id
                    except Exception:
                        resource_kind = "cache_cluster"
                else:
                    resource_kind = "cache_cluster"
        except Exception:
            pass

    if resource_kind is None:
        raise HTTPException(status_code=404, detail="ElastiCache resource not found.")
    
    return resource_kind, replication_group, cache_cluster, target_resource_id


def handle_elasticache_delete(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete ElastiCache cluster or replication group."""
    try:
        elasticache = context.aws_adapter.session.client("elasticache", region_name=context.payload.region)
        resource_kind, replication_group, cache_cluster, target_resource_id = _get_elasticache_resource(
            elasticache, context.payload.resource_id
        )

        if resource_kind == "replication_group":
            delete_response = elasticache.delete_replication_group(
                ReplicationGroupId=target_resource_id,
                RetainPrimaryCluster=False,
            )
        else:
            delete_response = elasticache.delete_cache_cluster(
                CacheClusterId=target_resource_id,
            )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="ElastiCache deletion submitted.",
            details={"response": delete_response},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to delete ElastiCache resource",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete ElastiCache resource: {str(exc)}"
        )


def handle_elasticache_downsize(context: ActionExecutionContext) -> CheckActionResponse:
    """Downsize ElastiCache node type or node count."""
    try:
        params = context.payload.parameters or {}
        elasticache = context.aws_adapter.session.client("elasticache", region_name=context.payload.region)
        resource_kind, replication_group, cache_cluster, target_resource_id = _get_elasticache_resource(
            elasticache, context.payload.resource_id
        )

        target_node_type = params.get("target_node_type")
        if not target_node_type:
            raise HTTPException(status_code=400, detail="Missing target_node_type.")

        node_count = params.get("num_cache_nodes")
        if node_count is not None:
            try:
                requested_nodes = int(node_count)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid num_cache_nodes.")
            if requested_nodes <= 0:
                raise HTTPException(status_code=400, detail="num_cache_nodes must be positive.")

            current_nodes = None
            if resource_kind == "replication_group" and replication_group:
                member_clusters = replication_group.get("MemberClusters", [])
                if isinstance(member_clusters, list) and member_clusters:
                    current_nodes = len(member_clusters)
            if current_nodes is None and cache_cluster:
                current_nodes = cache_cluster.get("NumCacheNodes")

            if current_nodes is not None and requested_nodes > int(current_nodes):
                raise HTTPException(
                    status_code=400,
                    detail="num_cache_nodes cannot exceed the current node count.",
                )

        if resource_kind == "replication_group":
            modify_params = {
                "ReplicationGroupId": target_resource_id,
                "CacheNodeType": target_node_type,
                "ApplyImmediately": True,
            }
            replicas = params.get("replicas_per_node_group")
            if replicas is not None:
                modify_params["ReplicasPerNodeGroup"] = int(replicas)
            modify_response = elasticache.modify_replication_group(**modify_params)
        else:
            modify_params = {
                "CacheClusterId": target_resource_id,
                "CacheNodeType": target_node_type,
                "ApplyImmediately": True,
            }
            if node_count is not None:
                modify_params["NumCacheNodes"] = int(node_count)
            modify_response = elasticache.modify_cache_cluster(**modify_params)

        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="ElastiCache node type modification submitted.",
            details={"response": modify_response, "target_node_type": target_node_type},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to downsize ElastiCache resource",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to downsize ElastiCache resource: {str(exc)}"
        )


# Alias for migrate_graviton which uses the same logic as downsize
def handle_elasticache_migrate_graviton(context: ActionExecutionContext) -> CheckActionResponse:
    """Migrate ElastiCache to Graviton node type."""
    return handle_elasticache_downsize(context)


def handle_elasticache_upgrade_valkey(context: ActionExecutionContext) -> CheckActionResponse:
    """Upgrade ElastiCache from Redis to Valkey engine (complex multi-step process)."""
    try:
        params = context.payload.parameters or {}
        elasticache = context.aws_adapter.session.client("elasticache", region_name=context.payload.region)
        resource_kind, replication_group, cache_cluster, target_resource_id = _get_elasticache_resource(
            elasticache, context.payload.resource_id
        )

        target_engine_version = params.get("target_engine_version")
        transit_encryption_enabled = params.get("transit_encryption_enabled")
        
        # Determine current engine
        current_engine = None
        if replication_group:
            current_engine = replication_group.get("Engine")
        if not current_engine and cache_cluster:
            current_engine = cache_cluster.get("Engine")
        current_engine = str(current_engine).lower() if current_engine else None
        if current_engine in {"redis", "redis oss", "redisos"}:
            current_engine = "redis"
        if current_engine in {"valkey"}:
            current_engine = "valkey"
        
        target_replication_group_id = params.get("target_replication_group_id")
        if not target_replication_group_id:
            base_id = f"{target_resource_id}-valkey"
            if len(base_id) > 40:
                trimmed_base = base_id[:40].rstrip("-")
                target_replication_group_id = trimmed_base
            else:
                target_replication_group_id = base_id
        else:
            if len(target_replication_group_id) > 40:
                target_replication_group_id = target_replication_group_id[:40].rstrip("-")
        
        def parse_version(value: str):
            parts = []
            for chunk in str(value).split("."):
                try:
                    parts.append(int(chunk))
                except ValueError:
                    parts.append(0)
            return tuple(parts)
        
        # Determine target engine version if not provided
        if not target_engine_version:
            try:
                valkey_response = elasticache.describe_cache_engine_versions(
                    Engine="valkey",
                    DefaultOnly=False,
                )
                valkey_versions = [
                    v.get("EngineVersion")
                    for v in valkey_response.get("CacheEngineVersions", [])
                    if v.get("EngineVersion")
                ]
                redis_versions = []
                if current_engine == "redis":
                    redis_response = elasticache.describe_cache_engine_versions(
                        Engine="redis",
                        DefaultOnly=False,
                    )
                    redis_versions = [
                        v.get("EngineVersion")
                        for v in redis_response.get("CacheEngineVersions", [])
                        if v.get("EngineVersion")
                    ]
                
                if redis_versions:
                    candidates = sorted(
                        set(valkey_versions).intersection(redis_versions),
                        key=parse_version,
                        reverse=True,
                    )
                    if not candidates:
                        target_engine_version = None
                    else:
                        target_engine_version = candidates[0]
                elif valkey_versions:
                    valkey_versions.sort(key=parse_version, reverse=True)
                    target_engine_version = valkey_versions[0]
            except Exception:
                target_engine_version = None
        
        if current_engine == "redis" and target_engine_version:
            try:
                redis_response = elasticache.describe_cache_engine_versions(
                    Engine="redis",
                    DefaultOnly=False,
                )
                redis_versions = {
                    v.get("EngineVersion")
                    for v in redis_response.get("CacheEngineVersions", [])
                    if v.get("EngineVersion")
                }
                if target_engine_version not in redis_versions:
                    target_engine_version = None
            except HTTPException:
                raise
            except Exception:
                pass
        
        # For Redis replication groups without compatible version, use snapshot-based migration
        if resource_kind == "replication_group" and current_engine == "redis" and not target_engine_version:
            snapshot_prefix = f"{target_resource_id}-valkey-snap-"
            snapshot_cluster_id = None
            if replication_group and not replication_group.get("ClusterEnabled"):
                member_clusters = replication_group.get("MemberClusters", [])
                if member_clusters:
                    snapshot_cluster_id = member_clusters[0]

            existing_snapshots = []
            try:
                if snapshot_cluster_id:
                    snap_resp = elasticache.describe_snapshots(CacheClusterId=snapshot_cluster_id)
                else:
                    snap_resp = elasticache.describe_snapshots(ReplicationGroupId=target_resource_id)
                for snap in snap_resp.get("Snapshots", []):
                    if snap.get("SnapshotName", "").startswith(snapshot_prefix):
                        existing_snapshots.append(snap)
            except Exception:
                existing_snapshots = []

            available_snapshots = [
                snap for snap in existing_snapshots if snap.get("SnapshotStatus") == "available"
            ]
            available_snapshots.sort(
                key=lambda snap: snap.get("SnapshotCreateTime") or datetime.min, reverse=True
            )

            if not available_snapshots:
                snapshot_name = f"{snapshot_prefix}{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                snapshot_params = {"SnapshotName": snapshot_name}
                if snapshot_cluster_id:
                    cluster_response = elasticache.describe_cache_clusters(
                        CacheClusterId=snapshot_cluster_id,
                        ShowCacheNodeInfo=False,
                    )
                    clusters = cluster_response.get("CacheClusters", [])
                    status = clusters[0].get("CacheClusterStatus") if clusters else None
                    if status != "available":
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Cache cluster {snapshot_cluster_id} is not available for snapshot. "
                                "Wait for it to become available, then rerun the upgrade."
                            ),
                        )
                    snapshot_params["CacheClusterId"] = snapshot_cluster_id
                else:
                    snapshot_params["ReplicationGroupId"] = target_resource_id
                snapshot_response = elasticache.create_snapshot(**snapshot_params)
                return CheckActionResponse(
                    check_id=context.check_id,
                    action=context.payload.action,
                    status="submitted",
                    message=(
                        "Snapshot creation submitted. This can take several minutes. "
                        "Rerun the upgrade once the snapshot is available to create the Valkey replication group. "
                        "After validating the Valkey group, delete the original Redis group if desired."
                    ),
                    details={
                        "source_replication_group_id": target_resource_id,
                        "snapshot_name": snapshot_name,
                        "response": snapshot_response,
                    },
                )

            snapshot_name = available_snapshots[0].get("SnapshotName")
            try:
                elasticache.describe_replication_groups(
                    ReplicationGroupId=target_replication_group_id
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Target replication group {target_replication_group_id} already exists. "
                        "Provide a different target_replication_group_id."
                    ),
                )
            except HTTPException:
                raise
            except Exception:
                pass

            try:
                valkey_response = elasticache.describe_cache_engine_versions(
                    Engine="valkey",
                    DefaultOnly=False,
                )
                valkey_versions = [
                    v.get("EngineVersion")
                    for v in valkey_response.get("CacheEngineVersions", [])
                    if v.get("EngineVersion")
                ]
                valkey_versions.sort(key=parse_version, reverse=True)
            except Exception:
                valkey_versions = []

            selected_valkey_version = None
            if target_engine_version:
                if valkey_versions and target_engine_version not in valkey_versions:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Engine version {target_engine_version} is not valid for valkey. "
                            "Provide a valid valkey engine version or leave it blank to use the latest."
                        ),
                    )
                selected_valkey_version = target_engine_version
            elif valkey_versions:
                selected_valkey_version = valkey_versions[0]

            create_params = {
                "ReplicationGroupId": target_replication_group_id,
                "ReplicationGroupDescription": (
                    f"Valkey copy of {target_resource_id} created by MaxOps"
                ),
                "Engine": "valkey",
                "SnapshotName": snapshot_name,
                "CacheNodeType": replication_group.get("CacheNodeType"),
            }
            valkey_major = None
            if selected_valkey_version:
                try:
                    valkey_major = int(str(selected_valkey_version).split(".")[0])
                except (ValueError, IndexError):
                    valkey_major = None
            if not valkey_major:
                valkey_major = 8 if valkey_versions and valkey_versions[0].startswith("8") else 7
            use_cluster_on = bool(replication_group.get("ClusterEnabled"))
            default_valkey_pg = (
                f"default.valkey{valkey_major}.cluster.on"
                if use_cluster_on
                else f"default.valkey{valkey_major}"
            )
            if selected_valkey_version:
                create_params["EngineVersion"] = selected_valkey_version
            if replication_group.get("CacheSubnetGroupName"):
                create_params["CacheSubnetGroupName"] = replication_group.get("CacheSubnetGroupName")
            if replication_group.get("SecurityGroupIds"):
                create_params["SecurityGroupIds"] = replication_group.get("SecurityGroupIds")
            create_params["CacheParameterGroupName"] = default_valkey_pg
            if replication_group.get("SnapshotRetentionLimit") is not None:
                create_params["SnapshotRetentionLimit"] = replication_group.get("SnapshotRetentionLimit")
            if replication_group.get("SnapshotWindow"):
                create_params["SnapshotWindow"] = replication_group.get("SnapshotWindow")
            if replication_group.get("TransitEncryptionEnabled") is not None:
                create_params["TransitEncryptionEnabled"] = bool(
                    replication_group.get("TransitEncryptionEnabled")
                )
            if transit_encryption_enabled is not None:
                create_params["TransitEncryptionEnabled"] = bool(transit_encryption_enabled)
            if replication_group.get("MultiAZ") == "enabled":
                create_params["MultiAZEnabled"] = True
            if replication_group.get("AutomaticFailover") == "enabled":
                create_params["AutomaticFailoverEnabled"] = True
            if replication_group.get("ClusterEnabled"):
                create_params["ClusterMode"] = "enabled"
                node_groups = replication_group.get("NodeGroups", [])
                if node_groups:
                    create_params["NumNodeGroups"] = len(node_groups)
                    members = node_groups[0].get("NodeGroupMembers", [])
                    if members:
                        create_params["ReplicasPerNodeGroup"] = max(len(members) - 1, 0)
            else:
                member_clusters = replication_group.get("MemberClusters", [])
                if member_clusters:
                    create_params["NumCacheClusters"] = len(member_clusters)

            create_response = elasticache.create_replication_group(**create_params)
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="submitted",
                message=(
                    "Valkey replication group creation submitted (snapshot-based). "
                    "This can take several minutes. After validating the Valkey group, "
                    "delete the original Redis group if desired."
                ),
                details={
                    "source_replication_group_id": target_resource_id,
                    "target_replication_group_id": target_replication_group_id,
                    "snapshot_name": snapshot_name,
                    "response": create_response,
                },
            )
        
        # For compatible versions or cache clusters, perform in-place upgrade
        if resource_kind == "replication_group":
            modify_params = {
                "ReplicationGroupId": target_resource_id,
                "ApplyImmediately": True,
            }
            if target_engine_version:
                modify_params["EngineVersion"] = target_engine_version
            modify_response = elasticache.modify_replication_group(**modify_params)
        else:
            if not target_engine_version:
                try:
                    valkey_response = elasticache.describe_cache_engine_versions(
                        Engine="valkey",
                        DefaultOnly=False,
                    )
                    valkey_versions = [
                        v.get("EngineVersion")
                        for v in valkey_response.get("CacheEngineVersions", [])
                        if v.get("EngineVersion")
                    ]
                    valkey_versions.sort(key=parse_version, reverse=True)
                    if valkey_versions:
                        target_engine_version = valkey_versions[0]
                except Exception:
                    target_engine_version = None
            if not target_engine_version:
                raise HTTPException(
                    status_code=400,
                    detail="Missing target_engine_version for cache cluster upgrade.",
                )
            try:
                valkey_response = elasticache.describe_cache_engine_versions(
                    Engine="valkey",
                    DefaultOnly=False,
                )
                valkey_versions = {
                    v.get("EngineVersion")
                    for v in valkey_response.get("CacheEngineVersions", [])
                    if v.get("EngineVersion")
                }
            except Exception:
                valkey_versions = set()
            if valkey_versions and target_engine_version not in valkey_versions:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Engine version {target_engine_version} is not valid for valkey. "
                        "Provide a valid valkey engine version or leave it blank to use the latest."
                    ),
                )
            valkey_major = None
            try:
                valkey_major = int(str(target_engine_version).split(".")[0])
            except (ValueError, IndexError):
                valkey_major = None
            if not valkey_major:
                valkey_major = 8
            default_valkey_pg = f"default.valkey{valkey_major}"
            modify_response = elasticache.modify_cache_cluster(
                CacheClusterId=target_resource_id,
                EngineVersion=target_engine_version,
                ApplyImmediately=True,
                CacheParameterGroupName=default_valkey_pg,
            )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="ElastiCache upgrade submitted.",
            details={
                "target_engine": "valkey",
                "target_engine_version": target_engine_version,
                "response": modify_response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to upgrade ElastiCache to Valkey",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upgrade ElastiCache to Valkey: {str(exc)}"
        )
