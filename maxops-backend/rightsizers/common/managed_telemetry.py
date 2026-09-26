"""ElastiCache and RDS live fixture implementation used by folder entrypoints."""
from __future__ import annotations
import base64, json, secrets, time
from botocore.exceptions import ClientError
from rightsizers.common.telemetry_testing import *

EC_DEFS={
"engine_cpu_percent":("EngineCPUUtilization","Average"),"host_cpu_percent":("CPUUtilization","Average"),"memory_percent":("DatabaseMemoryUsagePercentage","Average"),"bytes_used_for_cache":("BytesUsedForCache","Average"),"freeable_memory_bytes":("FreeableMemory","Average"),"network_in_bytes":("NetworkBytesIn","Sum"),"network_out_bytes":("NetworkBytesOut","Sum"),"evictions":("Evictions","Sum"),"get_type_cmds":("GetTypeCmds","Sum"),"set_type_cmds":("SetTypeCmds","Sum"),"swap_usage_bytes":("SwapUsage","Average"),"replication_lag_seconds":("ReplicationLag","Average"),"curr_connections":("CurrConnections","Average"),"is_master":("IsMaster","Average"),"traffic_management_active":("TrafficManagementActive","Maximum"),"network_bw_in_allowance_exceeded":("NetworkBandwidthInAllowanceExceeded","Sum"),"network_bw_out_allowance_exceeded":("NetworkBandwidthOutAllowanceExceeded","Sum"),"network_packets_per_second_allowance_exceeded":("NetworkPacketsPerSecondAllowanceExceeded","Sum"),"network_conntrack_allowance_exceeded":("NetworkConntrackAllowanceExceeded","Sum"),"cpu_credit_balance":("CPUCreditBalance","Average"),"cpu_credit_usage":("CPUCreditUsage","Sum")}
RDS_DEFS={"cpu_percent":("CPUUtilization","Average"),"freeable_memory_bytes":("FreeableMemory","Average"),"swap_bytes":("SwapUsage","Average"),"connections":("DatabaseConnections","Average"),"read_iops":("ReadIOPS","Average"),"write_iops":("WriteIOPS","Average"),"read_throughput_bps":("ReadThroughput","Average"),"write_throughput_bps":("WriteThroughput","Average"),"read_latency_seconds":("ReadLatency","Average"),"write_latency_seconds":("WriteLatency","Average"),"disk_queue_depth":("DiskQueueDepth","Average"),"free_storage_bytes":("FreeStorageSpace","Minimum"),"network_rx_bps":("NetworkReceiveThroughput","Average"),"network_tx_bps":("NetworkTransmitThroughput","Average"),"cpu_credit_balance":("CPUCreditBalance","Minimum"),"cpu_credit_usage":("CPUCreditUsage","Sum"),"burst_balance_percent":("BurstBalance","Minimum"),"ebs_io_balance_percent":("EBSIOBalance%","Minimum"),"ebs_byte_balance_percent":("EBSByteBalance%","Minimum"),"replica_lag_seconds":("ReplicaLag","Maximum")}

def submit_cache_workload(ssm,store,instance_id,endpoint,port):
 script=f'import socket,time\nend=time.time()+600\ni=0\nwhile time.time()<end:\n s=socket.create_connection(("{endpoint}",{port}),5); k=f"maxops:{{i}}".encode(); s.sendall(b"*3\\r\\n$3\\r\\nSET\\r\\n$"+str(len(k)).encode()+b"\\r\\n"+k+b"\\r\\n$1\\r\\nx\\r\\n"); s.recv(1024); s.sendall(b"*2\\r\\n$3\\r\\nGET\\r\\n$"+str(len(k)).encode()+b"\\r\\n"+k+b"\\r\\n"); s.recv(1024); s.close(); i+=1; time.sleep(1)'
 encoded=base64.b64encode(script.encode()).decode(); cmd=f"nohup bash -c 'echo {encoded} | base64 -d | python3' >/tmp/maxops-cache.log 2>&1 &"
 return send_shell_command(ssm,store,[instance_id],[cmd],purpose="elasticache-set-get-workload")

def _wait(describe, ready, timeout=2400):
 for _ in range(timeout//15):
  value=describe()
  if ready(value): return value
  time.sleep(15)
 raise TimeoutError("managed resource waiter expired")

def _network(session,store,tags,port):
 ec2=session.client("ec2",region_name=store.data["region"]); vpc,subs=default_vpc_subnets(ec2)
 net=default_network(ec2); add_reused_network(store,net)
 for index,subnet_id in enumerate(subs):
  if subnet_id!=net["subnet_id"]: store.add_resource(f"subnet_{index+1}","ec2","subnet",subnet_id,ownership="reused",dependencies=["vpc"],ready=True)
 client=create_security_group(session,store,logical_name="client_security_group",group_name=f"maxops-{store.data['run_id']}-client",description="telemetry client",vpc_id=vpc,tags=tags)
 target=create_security_group(session,store,logical_name="target_security_group",group_name=f"maxops-{store.data['run_id']}-target",description="telemetry target",vpc_id=vpc,tags=tags)
 ec2.authorize_security_group_ingress(GroupId=target,IpPermissions=[{"IpProtocol":"tcp","FromPort":port,"ToPort":port,"UserIdGroupPairs":[{"GroupId":client}]}])
 return subs,client,target

def create(kind,folder):
 args=common_create_parser(f"Create live {kind} telemetry fixture").parse_args(); run=args.run_id or make_run_id(kind); path=args.state or state_path_for(folder,run)
 require_apply(args,{"rightsizer":kind,"run_id":run,"state":str(path),"profile":args.profile,"region":args.region})
 session=new_session(args.profile,args.region); store=StateStore.create(path,rightsizer=kind,run_id=run,profile=args.profile,region=args.region,identity=caller_identity(session,args.region)); tags=aws_tags(kind,run,store.data["expires_at"])
 try:
  subs,client_sg,target_sg=_network(session,store,tags,6379 if kind=="elasticache" else 3306); ssm=session.client("ssm",region_name=args.region)
  profile=create_instance_role(session,store,name_prefix=f"maxops-{run}",tags=tags); wait_for_instance_profile(session.client("iam"),profile,store.resource("instance_role")["id_or_arn"])
  endpoint=""; port=6379
  if kind=="elasticache":
   api=session.client("elasticache",region_name=args.region); subnet=f"maxops-{run}"[:255]; api.create_cache_subnet_group(CacheSubnetGroupName=subnet,CacheSubnetGroupDescription="telemetry fixture",SubnetIds=subs,Tags=tag_list(tags)); store.add_resource("subnet_group","elasticache","cache_subnet_group",subnet,dependencies=["subnet"])
   rg=f"maxops-{run}"[:40]; api.create_replication_group(ReplicationGroupId=rg,ReplicationGroupDescription="MaxOps telemetry fixture",Engine="redis",CacheNodeType="cache.t4g.micro",NumCacheClusters=2,AutomaticFailoverEnabled=False,CacheSubnetGroupName=subnet,SecurityGroupIds=[target_sg],Tags=tag_list(tags)); store.add_resource("replication_group","elasticache","replication_group",rg,dependencies=["subnet_group","target_security_group"])
   obj=_wait(lambda:api.describe_replication_groups(ReplicationGroupId=rg)["ReplicationGroups"][0],lambda x:x["Status"]=="available"); members=obj["NodeGroups"][0]["NodeGroupMembers"]; roles={m["CacheClusterId"]:m["CurrentRole"] for m in members}; endpoint=obj["NodeGroups"][0]["PrimaryEndpoint"]["Address"]; port=obj["NodeGroups"][0]["PrimaryEndpoint"]["Port"]; store.mark_ready("replication_group",member_cluster_ids=list(roles),member_roles=roles,cache_node_type="cache.t4g.micro",endpoint=endpoint,port=port)
  else:
   api=session.client("rds",region_name=args.region); subnet=f"maxops-{run}"[:255]; api.create_db_subnet_group(DBSubnetGroupName=subnet,DBSubnetGroupDescription="telemetry fixture",SubnetIds=subs,Tags=tag_list(tags)); store.add_resource("subnet_group","rds","db_subnet_group",subnet,dependencies=["subnet"])
   parameter=f"/maxops/telemetry/{run}/db-password"; password="Aa1!"+secrets.token_hex(16); response=ssm.put_parameter(Name=parameter,Description="Temporary MaxOps telemetry DB password",Type="SecureString",Value=password,Tags=tag_list(tags)); store.add_resource("password_parameter","ssm","secure_string_parameter",parameter,attributes={"version":response["Version"]})
   role=store.resource("instance_role"); policy_name="ReadTelemetryPassword"; session.client("iam").put_role_policy(RoleName=role["id_or_arn"],PolicyName=policy_name,PolicyDocument=json.dumps({"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ssm:GetParameter","Resource":f"arn:aws:ssm:{args.region}:{store.data['account_id']}:parameter{parameter}"}]})); role["attributes"].setdefault("inline_policy_names",[]).append(policy_name); store.save()
   primary=f"maxops-{run}-primary"[:63]; api.create_db_instance(DBInstanceIdentifier=primary,DBInstanceClass="db.t4g.medium",Engine="mysql",AllocatedStorage=20,StorageType="gp2",MasterUsername="maxopsadmin",MasterUserPassword=password,DBSubnetGroupName=subnet,VpcSecurityGroupIds=[target_sg],BackupRetentionPeriod=1,EnablePerformanceInsights=True,PubliclyAccessible=False,Tags=tag_list(tags)); store.add_resource("primary","rds","db_instance",primary,dependencies=["subnet_group","target_security_group","password_parameter"])
   pobj=_wait(lambda:api.describe_db_instances(DBInstanceIdentifier=primary)["DBInstances"][0],lambda x:x["DBInstanceStatus"]=="available"); store.mark_ready("primary",dbi_resource_id=pobj["DbiResourceId"],endpoint=pobj["Endpoint"])
   replica=f"maxops-{run}-replica"[:63]; api.create_db_instance_read_replica(DBInstanceIdentifier=replica,SourceDBInstanceIdentifier=primary,DBInstanceClass="db.t4g.medium",EnablePerformanceInsights=True,PubliclyAccessible=False,Tags=tag_list(tags)); store.add_resource("replica","rds","db_instance",replica,dependencies=["primary"])
   robj=_wait(lambda:api.describe_db_instances(DBInstanceIdentifier=replica)["DBInstances"][0],lambda x:x["DBInstanceStatus"]=="available"); store.mark_ready("replica",dbi_resource_id=robj["DbiResourceId"],endpoint=robj["Endpoint"]); endpoint=pobj["Endpoint"]["Address"]; port=pobj["Endpoint"]["Port"]
  ec2=session.client("ec2",region_name=args.region); iid=launch_managed_instance(session,store,logical_name="workload_client",image_id=latest_al2023_ami(ssm),instance_type=select_offered_type(ec2,("t3.micro","t3a.micro")),subnet_id=default_network(ec2)["subnet_id"],security_group_ids=[client_sg],profile_name=profile,tags=tags); wait_for_ssm(ssm,[iid])
  if kind=="elasticache": submit_cache_workload(ssm,store,iid,endpoint,port)
  else:
   cmd=f"nohup bash -c 'dnf install -y mariadb105; end=$((SECONDS+600)); while [ $SECONDS -lt $end ]; do p=$(aws ssm get-parameter --name {store.resource('password_parameter')['id_or_arn']} --with-decryption --query Parameter.Value --output text --region {args.region}); MYSQL_PWD=$p mysql -h {endpoint} -u maxopsadmin -e \"CREATE DATABASE IF NOT EXISTS maxops_telemetry; CREATE TABLE IF NOT EXISTS maxops_telemetry.t(id INT PRIMARY KEY, v INT); REPLACE INTO maxops_telemetry.t VALUES (1,RAND()*1000); SELECT * FROM maxops_telemetry.t;\"; sleep 5; done' >/tmp/maxops-rds.log 2>&1 &"
   send_shell_command(ssm,store,[iid],[cmd],purpose="rds-workload")
  store.set_phase("READY"); print_result({"result":"CREATED","state":str(store.path),"run_id":run})
 except Exception: store.set_phase("CREATE_FAILED"); cleanup(kind,store,session); raise

def cleanup(kind,store,session=None):
 session=session or new_session(store.data["profile"],store.data["region"]); assert_state_identity(store,session); errors=[]
 terminate_instance(session,store,"workload_client",errors); api=session.client("elasticache" if kind=="elasticache" else "rds",region_name=store.data["region"])
 names=["replication_group"] if kind=="elasticache" else ["replica","primary"]
 for name in names:
  r=store.maybe_resource(name)
  if not r or r["delete_status"] in {"DELETED","ALREADY_ABSENT"}: continue
  try:
   if kind=="elasticache": api.delete_replication_group(ReplicationGroupId=r["id_or_arn"],RetainPrimaryCluster=False)
   else: api.delete_db_instance(DBInstanceIdentifier=r["id_or_arn"],SkipFinalSnapshot=True,DeleteAutomatedBackups=True)
   if kind=="elasticache":
    _wait(lambda: _describe_absent(api.describe_replication_groups, "ReplicationGroupNotFoundFault", ReplicationGroupId=r["id_or_arn"]), lambda x:x is None)
   else:
    _wait(lambda: _describe_absent(api.describe_db_instances, "DBInstanceNotFound", DBInstanceIdentifier=r["id_or_arn"]), lambda x:x is None)
   store.mark_deleted(name)
  except ClientError as exc:
   if is_not_found(exc,{"ReplicationGroupNotFoundFault","DBInstanceNotFound"}): store.mark_deleted(name,"ALREADY_ABSENT")
   else: errors.append(cleanup_error(r,"delete_managed_resource",exc))
 sg=store.maybe_resource("subnet_group")
 if sg:
  try:
   if kind=="elasticache": api.delete_cache_subnet_group(CacheSubnetGroupName=sg["id_or_arn"])
   else: api.delete_db_subnet_group(DBSubnetGroupName=sg["id_or_arn"])
   store.mark_deleted("subnet_group")
  except ClientError as exc:
   missing={"CacheSubnetGroupNotFoundFault","DBSubnetGroupNotFoundFault"}
   if is_not_found(exc,missing): store.mark_deleted("subnet_group","ALREADY_ABSENT")
   else: errors.append(cleanup_error(sg,"delete_subnet_group",exc))
 if kind=="rds" and store.maybe_resource("password_parameter"):
  try: session.client("ssm",region_name=store.data["region"]).delete_parameter(Name=store.resource("password_parameter")["id_or_arn"]); store.mark_deleted("password_parameter")
  except ClientError as exc: errors.append(cleanup_error(store.resource("password_parameter"),"delete_parameter",exc))
 delete_security_group(session,store,"target_security_group",errors); delete_security_group(session,store,"client_security_group",errors); cleanup_instance_role(session,store,errors); return finalize_cleanup(store,errors)

def _describe_absent(operation, missing_code, **kwargs):
 try: return operation(**kwargs)
 except ClientError as exc:
  if error_code(exc)==missing_code: return None
  raise

def validate(kind):
 args=common_state_parser(f"Validate {kind} telemetry",validate=True).parse_args(); store=StateStore.load(args.state); session=new_session(store.data["profile"],store.data["region"]); assert_state_identity(store,session)
 def check(start,end):
  command_readiness(session.client("ssm",region_name=store.data["region"]),store); adapter,rec=production_adapter(session,store.data["region"]); results={}
  if kind=="elasticache":
   resource=store.resource("replication_group"); obj=session.client("elasticache",region_name=store.data["region"]).describe_replication_groups(ReplicationGroupId=resource["id_or_arn"])["ReplicationGroups"][0]
   if obj["Status"]!="available": raise NotReady("replication group is not available")
   nodes=resource["attributes"]["member_cluster_ids"]; roles=resource["attributes"]["member_roles"]; raw=adapter.get_elasticache_rightsizing_metrics(nodes,start,end,region=store.data["region"],period_seconds=300,include_cpu_credits=True)
   calls=metric_data_calls(rec); queries=flatten_queries(calls); expected={(node,name,stat) for node in nodes for name,stat in EC_DEFS.values()}; actual={(dict((d["Name"],d["Value"]) for d in q["MetricStat"]["Metric"]["Dimensions"])["CacheClusterId"],q["MetricStat"]["Metric"]["MetricName"],q["MetricStat"]["Stat"]) for q in queries}
   if actual!=expected: raise ValidationFailure(f"ElastiCache query contract mismatch missing={expected-actual} extra={actual-expected}")
   assert_complete_responses(calls)
   for node,metrics in raw["per_node"].items():
    for alias,payload in metrics.items():
     if alias=="replication_lag_seconds" and roles.get(node)!="replica": results[f"{node}:{alias}"]={"result":"NOT_APPLICABLE"}; continue
     if alias=="get_type_cmds" and roles.get(node)=="replica": results[f"{node}:{alias}"]={"result":"NOT_APPLICABLE","reason":"reads target the primary endpoint and AWS does not publish this series on the replica"}; continue
     results[f"{node}:{alias}"]=require_series(f"{node}:{alias}",payload,maximum=100 if "percent" in alias else None,positive=alias in {"engine_cpu_percent","set_type_cmds","get_type_cmds"})
   before=len(rec.calls); selected=[{"cache_cluster_id":node,"role":roles.get(node,"unknown"),"selection_reason":"telemetry_fixture_full_topology"} for node in nodes]; adapter.get_elasticache_confidence_trend({"engine_cpu":selected,"memory":selected},start,end,region=store.data["region"]); trend=[q for c in rec.calls[before:] if c["operation"]=="get_metric_data" for q in c["request"].get("MetricDataQueries",[])]
   if len({q["Id"] for q in trend})!=60: raise ValidationFailure("ElastiCache confidence trend request shape is incomplete")
  else:
   api=session.client("rds",region_name=store.data["region"]); trend=[]
   for logical in ("primary","replica"):
    r=store.resource(logical); obj=api.describe_db_instances(DBInstanceIdentifier=r["id_or_arn"])["DBInstances"][0]
    if obj["DBInstanceStatus"]!="available": raise NotReady(f"{logical} is not available")
    before=len(rec.calls); raw=adapter.get_rds_rightsizing_metrics(r["id_or_arn"],start,end,region=store.data["region"],period_seconds=300); queries=flatten_queries([c for c in rec.calls[before:] if c["operation"]=="get_metric_data"])
    expected={(name,stat) for name,stat in RDS_DEFS.values()}; actual={(q["MetricStat"]["Metric"]["MetricName"],q["MetricStat"]["Stat"]) for q in queries}
    if actual!=expected: raise ValidationFailure(f"RDS {logical} query contract mismatch")
    for alias,payload in raw["metrics"].items():
     if alias=="replica_lag_seconds" and logical=="primary": results[f"{logical}:{alias}"]={"result":"NOT_APPLICABLE"}; continue
     results[f"{logical}:{alias}"]=require_series(
      f"{logical}:{alias}",payload,
      maximum=100 if "percent" in alias else None,
      positive=alias=="cpu_percent" or (alias=="connections" and logical=="primary"),
     )
   before=len(rec.calls); pi=adapter.get_rds_performance_insights_metrics(store.resource("primary")["attributes"]["dbi_resource_id"],start,end,region=store.data["region"],period_seconds=300); picalls=[c for c in rec.calls[before:] if c["operation"]=="get_resource_metrics"]
   if not picalls or picalls[0]["request"]["Identifier"]!=store.resource("primary")["attributes"]["dbi_resource_id"] or picalls[0]["request"]["MetricQueries"]!=[{"Metric":"db.load.avg"},{"Metric":"db.load.avg","GroupBy":{"Group":"db.wait_event_type","Limit":25}}]: raise ValidationFailure("RDS Performance Insights request contract mismatch")
   if not any(item.get("DataPoints") for item in pi.get("metric_list",[])): raise ValidationFailure("RDS Performance Insights returned no db.load.avg datapoints")
   before=len(rec.calls); adapter.get_rds_confidence_trend(store.resource("primary")["id_or_arn"],start,end,region=store.data["region"]); trend=[q for c in rec.calls[before:] if c["operation"]=="get_metric_data" for q in c["request"].get("MetricDataQueries",[])]
   if len({q["Id"] for q in trend})!=52: raise ValidationFailure("RDS confidence trend request shape is incomplete")
  return {"decision_metrics":results,"trend_query_count":len({q["Id"] for q in trend}),"request_count":len(rec.calls)}
 report,code=run_validation(store,args.window_minutes,check); print_result(report); raise SystemExit(code)

def cleanup_cli(kind):
 args=common_state_parser(f"Clean up {kind} telemetry fixture").parse_args(); result=cleanup(kind,StateStore.load(args.state)); print_result(result); raise SystemExit(0 if result["result"]=="SUCCESS" else 1)
