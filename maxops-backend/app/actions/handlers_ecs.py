"""ECS action handlers used by the action factory."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse


def handle_ecs_delete_service(context: ActionExecutionContext) -> CheckActionResponse:
    payload = context.payload
    check_id = context.check_id
    if "/" not in payload.resource_id:
        raise HTTPException(
            status_code=400,
            detail="ECS service resource_id must be in '<cluster>/<service>' format.",
        )
    cluster_name, service_name = payload.resource_id.split("/", 1)
    ecs = context.aws_adapter.session.client("ecs", region_name=payload.region)
    ecs.update_service(cluster=cluster_name, service=service_name, desiredCount=0)
    response = ecs.delete_service(cluster=cluster_name, service=service_name, force=True)
    return CheckActionResponse(
        check_id=check_id,
        action=payload.action,
        status="submitted",
        message=f"ECS service '{service_name}' delete request submitted.",
        details={"cluster": cluster_name, "service": service_name, "response": response},
    )


def handle_ecs_rightsize_task_definition(context: ActionExecutionContext) -> CheckActionResponse:
    payload = context.payload
    check_id = context.check_id
    if "/" not in payload.resource_id:
        raise HTTPException(
            status_code=400,
            detail="ECS service resource_id must be in '<cluster>/<service>' format.",
        )
    cluster_name, service_name = payload.resource_id.split("/", 1)
    ecs = context.aws_adapter.session.client("ecs", region_name=payload.region)

    service_response = ecs.describe_services(cluster=cluster_name, services=[service_name])
    services = service_response.get("services", [])
    if not services:
        raise HTTPException(status_code=404, detail="ECS service not found.")
    service_obj = services[0]
    task_def_arn = service_obj.get("taskDefinition")
    if not task_def_arn:
        raise HTTPException(status_code=400, detail="ECS service has no task definition.")

    td_response = ecs.describe_task_definition(taskDefinition=task_def_arn)
    task_definition = td_response.get("taskDefinition", {})
    current_cpu = int(task_definition.get("cpu") or 0)
    current_memory = int(task_definition.get("memory") or 0)
    if current_cpu <= 0 or current_memory <= 0:
        raise HTTPException(
            status_code=400,
            detail="Task definition cpu/memory reservations are required for rightsizing.",
        )

    start_date = datetime.utcnow() - timedelta(days=14)
    end_date = datetime.utcnow()
    util = context.aws_adapter.get_resource_utilization(
        resource_id=payload.resource_id,
        resource_type="ecs",
        start_date=start_date,
        end_date=end_date,
        region=payload.region,
    )
    cpu_util = float(util.get("cpu_utilization") or 0.0)
    memory_util = float(util.get("memory_utilization") or 0.0)

    target_utilization = 60.0
    cpu_ratio = max(0.5, (cpu_util / target_utilization) * 1.2)
    memory_ratio = max(0.5, (memory_util / target_utilization) * 1.2)

    recommended_cpu = max(256, int(((int(current_cpu * cpu_ratio) + 255) // 256) * 256))
    recommended_memory = max(512, int(((int(current_memory * memory_ratio) + 511) // 512) * 512))
    launch_type = str(service_obj.get("launchType") or "").upper()
    if launch_type == "FARGATE":
        valid_memory = {
            256: [512, 1024, 2048],
            512: [1024, 2048, 3072, 4096],
            1024: list(range(2048, 8193, 1024)),
            2048: list(range(4096, 16385, 1024)),
            4096: list(range(8192, 30721, 1024)),
        }
        cpu_options = sorted(valid_memory.keys())
        selected_cpu = cpu_options[-1]
        for option in cpu_options:
            if option >= recommended_cpu:
                selected_cpu = option
                break
        selected_memory = valid_memory[selected_cpu][-1]
        for option in valid_memory[selected_cpu]:
            if option >= recommended_memory:
                selected_memory = option
                break
        recommended_cpu = selected_cpu
        recommended_memory = selected_memory

    if recommended_cpu >= current_cpu and recommended_memory >= current_memory:
        raise HTTPException(
            status_code=400,
            detail="Service utilization does not currently indicate a smaller safe reservation.",
        )

    register_payload = {
        "family": task_definition.get("family"),
        "taskRoleArn": task_definition.get("taskRoleArn"),
        "executionRoleArn": task_definition.get("executionRoleArn"),
        "networkMode": task_definition.get("networkMode"),
        "containerDefinitions": task_definition.get("containerDefinitions", []),
        "volumes": task_definition.get("volumes", []),
        "placementConstraints": task_definition.get("placementConstraints", []),
        "requiresCompatibilities": task_definition.get("requiresCompatibilities", []),
        "cpu": str(recommended_cpu),
        "memory": str(recommended_memory),
        "pidMode": task_definition.get("pidMode"),
        "ipcMode": task_definition.get("ipcMode"),
        "proxyConfiguration": task_definition.get("proxyConfiguration"),
        "inferenceAccelerators": task_definition.get("inferenceAccelerators"),
        "ephemeralStorage": task_definition.get("ephemeralStorage"),
        "runtimePlatform": task_definition.get("runtimePlatform"),
    }
    register_payload = {k: v for k, v in register_payload.items() if v is not None}

    register_response = ecs.register_task_definition(**register_payload)
    new_task_def_arn = register_response.get("taskDefinition", {}).get("taskDefinitionArn")
    update_response = ecs.update_service(
        cluster=cluster_name,
        service=service_name,
        taskDefinition=new_task_def_arn,
        forceNewDeployment=True,
    )

    return CheckActionResponse(
        check_id=check_id,
        action=payload.action,
        status="submitted",
        message=f"Task definition rightsize submitted for service '{service_name}'.",
        details={
            "cluster": cluster_name,
            "service": service_name,
            "previous_task_definition": task_def_arn,
            "new_task_definition": new_task_def_arn,
            "current_cpu": current_cpu,
            "current_memory": current_memory,
            "recommended_cpu": recommended_cpu,
            "recommended_memory": recommended_memory,
            "cpu_utilization_pct": round(cpu_util, 2),
            "memory_utilization_pct": round(memory_util, 2),
            "update_response": update_response,
        },
    )


def handle_ecs_delete_cluster(context: ActionExecutionContext) -> CheckActionResponse:
    payload = context.payload
    check_id = context.check_id
    cluster_name = payload.resource_id
    ecs = context.aws_adapter.session.client("ecs", region_name=payload.region)
    cluster_response = ecs.describe_clusters(clusters=[cluster_name])
    clusters = cluster_response.get("clusters", [])
    if not clusters:
        raise HTTPException(status_code=404, detail="ECS cluster not found.")
    cluster_obj = clusters[0]
    if (
        int(cluster_obj.get("activeServicesCount", 0)) > 0
        or int(cluster_obj.get("runningTasksCount", 0)) > 0
        or int(cluster_obj.get("pendingTasksCount", 0)) > 0
    ):
        raise HTTPException(
            status_code=400,
            detail="Cluster still has active services/tasks; delete dependencies first.",
        )
    response = ecs.delete_cluster(cluster=cluster_name)
    return CheckActionResponse(
        check_id=check_id,
        action=payload.action,
        status="submitted",
        message=f"ECS cluster '{cluster_name}' delete request submitted.",
        details={"cluster": cluster_name, "response": response},
    )

