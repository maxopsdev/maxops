"""Live ASG telemetry fixture. See asg_rightsizer_telemetry_test_spec.md."""
from __future__ import annotations
import time
from pathlib import Path
from botocore.exceptions import ClientError
from rightsizers.common.telemetry_testing import *

FOLDER = Path(__file__).resolve().parent
DEFS = {
 "cpu_percent": ("AWS/EC2", "CPUUtilization", "Average"),
 "desired_capacity": ("AWS/AutoScaling", "GroupDesiredCapacity", "Maximum"),
 "in_service_instances": ("AWS/AutoScaling", "GroupInServiceInstances", "Maximum"),
}

def cleanup(store, session=None):
 session=session or new_session(store.data["profile"],store.data["region"]); assert_state_identity(store,session); errors=[]
 asg=store.maybe_resource("asg"); auto=session.client("autoscaling",region_name=store.data["region"])
 if asg and asg["delete_status"] not in {"DELETED","ALREADY_ABSENT"}:
  try:
   auto.update_auto_scaling_group(AutoScalingGroupName=asg["id_or_arn"],MinSize=0,DesiredCapacity=0)
   auto.delete_auto_scaling_group(AutoScalingGroupName=asg["id_or_arn"],ForceDelete=True)
   for _ in range(60):
    if not auto.describe_auto_scaling_groups(AutoScalingGroupNames=[asg["id_or_arn"]]).get("AutoScalingGroups"): break
    time.sleep(10)
   else: raise RuntimeError("ASG deletion did not finish")
   store.mark_deleted("asg")
  except ClientError as exc:
   if is_not_found(exc,{"ValidationError"}): store.mark_deleted("asg","ALREADY_ABSENT")
   else: errors.append(cleanup_error(asg,"delete_auto_scaling_group",exc))
 lt=store.maybe_resource("launch_template")
 if lt and lt["delete_status"] not in {"DELETED","ALREADY_ABSENT"}:
  try: session.client("ec2",region_name=store.data["region"]).delete_launch_template(LaunchTemplateId=lt["id_or_arn"]); store.mark_deleted("launch_template")
  except ClientError as exc:
   if is_not_found(exc,{"InvalidLaunchTemplateId.NotFound"}): store.mark_deleted("launch_template","ALREADY_ABSENT")
   else: errors.append(cleanup_error(lt,"delete_launch_template",exc))
 cleanup_instance_role(session,store,errors); return finalize_cleanup(store,errors)

def create_main():
 args=common_create_parser("Create live ASG telemetry fixture").parse_args(); run=args.run_id or make_run_id("asg"); path=args.state or state_path_for(FOLDER,run)
 require_apply(args,{"rightsizer":"asg","run_id":run,"state":str(path),"profile":args.profile,"region":args.region})
 session=new_session(args.profile,args.region); store=StateStore.create(path,rightsizer="asg",run_id=run,profile=args.profile,region=args.region,identity=caller_identity(session,args.region))
 try:
  ec2=session.client("ec2",region_name=args.region); ssm=session.client("ssm",region_name=args.region); auto=session.client("autoscaling",region_name=args.region)
  net=default_network(ec2); add_reused_network(store,net); tags=aws_tags("asg",run,store.data["expires_at"])
  profile=create_instance_role(session,store,name_prefix=f"maxops-{run}",tags=tags); wait_for_instance_profile(session.client("iam"),profile,store.resource("instance_role")["id_or_arn"])
  typ=select_offered_type(ec2,("t3a.small","t3.small")); name=f"maxops-{run}"[:128]
  response=ec2.create_launch_template(LaunchTemplateName=name,TagSpecifications=[{"ResourceType":"launch-template","Tags":tag_list(tags)}],LaunchTemplateData={"ImageId":latest_al2023_ami(ssm),"InstanceType":typ,"IamInstanceProfile":{"Name":profile},"Monitoring":{"Enabled":True},"SecurityGroupIds":[net["security_group_id"]],"MetadataOptions":{"HttpTokens":"required"},"TagSpecifications":[{"ResourceType":"instance","Tags":tag_list(tags)},{"ResourceType":"volume","Tags":tag_list(tags)}]})
  ltid=response["LaunchTemplate"]["LaunchTemplateId"]; store.add_resource("launch_template","ec2","launch_template",ltid,dependencies=["instance_profile"])
  for attempt in range(12):
   try:
    auto.create_auto_scaling_group(AutoScalingGroupName=name,LaunchTemplate={"LaunchTemplateId":ltid,"Version":"$Latest"},MinSize=2,MaxSize=2,DesiredCapacity=2,VPCZoneIdentifier=net["subnet_id"],HealthCheckType="EC2",Tags=[{"Key":k,"Value":v,"PropagateAtLaunch":True} for k,v in tags.items()]); break
   except ClientError as exc:
    if "Invalid IAM Instance Profile name" not in str(exc) or attempt==11: raise
    time.sleep(10)
  store.add_resource("asg","autoscaling","auto_scaling_group",name,dependencies=["launch_template","subnet"])
  auto.enable_metrics_collection(AutoScalingGroupName=name,Granularity="1Minute",Metrics=["GroupDesiredCapacity","GroupInServiceInstances"])
  ids=[]
  for _ in range(60):
   group=auto.describe_auto_scaling_groups(AutoScalingGroupNames=[name])["AutoScalingGroups"][0]; ids=[i["InstanceId"] for i in group.get("Instances",[]) if i.get("LifecycleState")=="InService" and i.get("HealthStatus")=="Healthy"]
   if len(ids)==2: break
   time.sleep(10)
  if len(ids)!=2: raise TimeoutError("ASG did not reach two healthy InService members")
  store.mark_ready("asg",instance_ids=ids,instance_type=typ,metrics_enabled=["GroupDesiredCapacity","GroupInServiceInstances"])
  wait_for_ssm(ssm,ids); configure_cloudwatch_agent(ssm,store,ids,cloudwatch_agent_config(asg=True)); submit_host_workload(ssm,store,ids); store.set_phase("READY")
  print_result({"result":"CREATED","state":str(store.path),"run_id":run})
 except Exception: store.set_phase("CREATE_FAILED"); cleanup(store,session); raise

def validate_main():
 args=common_state_parser("Validate ASG telemetry",validate=True).parse_args(); store=StateStore.load(args.state); session=new_session(store.data["profile"],store.data["region"]); assert_state_identity(store,session)
 def validate(start,end):
  name=store.resource("asg")["id_or_arn"]; groups=session.client("autoscaling",region_name=store.data["region"]).describe_auto_scaling_groups(AutoScalingGroupNames=[name]).get("AutoScalingGroups",[])
  if not groups or len([i for i in groups[0].get("Instances",[]) if i.get("LifecycleState")=="InService"])!=2: raise NotReady("ASG is not at two InService members")
  command_readiness(session.client("ssm",region_name=store.data["region"]),store); adapter,rec=production_adapter(session,store.data["region"])
  raw=adapter.get_asg_rightsizing_metrics(name,start,end,region=store.data["region"],period_seconds=300); src=raw["metrics"]["memory_percent"].get("source") or {}
  expected={k:(ns,m,[("AutoScalingGroupName",name)],300,stat) for k,(ns,m,stat) in DEFS.items()}; expected["memory_percent"]=(src.get("namespace","CWAgent"),src.get("metric_name","mem_used_percent"),[(x["Name"],x["Value"]) for x in src.get("dimensions",[])],300,"Average")
  assert_query_map(metric_data_calls(rec),expected); assert_complete_responses(metric_data_calls(rec)); results={k:require_series(k,v,maximum=100 if "percent" in k else None,positive=k=="cpu_percent") for k,v in raw["metrics"].items()}
  before=len(rec.calls); adapter.get_asg_confidence_trend(name,start,end,region=store.data["region"],memory_source=src)
  ids={q["Id"] for c in rec.calls[before:] if c["operation"]=="get_metric_data" for q in c["request"].get("MetricDataQueries",[])}
  if len(ids)!=30: raise ValidationFailure(f"ASG trend query shape incomplete: {len(ids)} queries")
  return {"decision_metrics":results,"trend_query_count":len(ids),"stable_member_fallback":"NOT_APPLICABLE"}
 report,code=run_validation(store,args.window_minutes,validate); print_result(report); raise SystemExit(code)

def cleanup_main():
 args=common_state_parser("Clean up ASG telemetry fixture").parse_args(); result=cleanup(StateStore.load(args.state)); print_result(result); raise SystemExit(0 if result["result"]=="SUCCESS" else 1)
