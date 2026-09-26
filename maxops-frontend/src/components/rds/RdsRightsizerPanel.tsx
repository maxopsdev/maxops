import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'react-query';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertTriangle, CheckCircle2, Database, Gauge, HardDrive, Info, ShieldAlert } from 'lucide-react';
import { Card } from '@/components/common/Card';
import { rightsizingClassificationClass } from '@/components/rightsizing/classification';
import { formatCurrency } from '@/utils/formatters';
import { useTheme } from '@/contexts/ThemeContext';
import { CHART_COLORS, STATUS_COLORS, chartAxisLineColor, chartGridColor, chartTickColor } from '@/styles/chartColors';
import {
  rightsizingApi,
  type RdsInstanceClassRecommendation,
  type RdsRightsizingResponse,
  type RdsStorageRecommendation,
  type RdsTierOption,
  type TierName,
} from '@/services/recommendations';

const money = (value: number | null | undefined) => value == null ? 'Unavailable' : formatCurrency(value);
const label = (value: string) => value.toLowerCase().split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
const recommendationKinds = (response: RdsRightsizingResponse) => ({
  classRecommendation: response.recommendations.find((item): item is RdsInstanceClassRecommendation => item.kind === 'DB_INSTANCE_CLASS_CHANGE'),
  storageRecommendation: response.recommendations.find((item): item is RdsStorageRecommendation => item.kind === 'STORAGE_CONFIGURATION_CHANGE'),
});

const statusCopy = (status: string, reasons: string[]) => {
  if (status === 'NO_RECOMMENDATION') return `Evaluation completed without an eligible saving${reasons.length ? `: ${reasons.map(label).join(' · ')}` : '.'}`;
  if (status === 'NOT_APPLICABLE') return `Not applicable${reasons.length ? `: ${reasons.map(label).join(' · ')}` : '.'}`;
  if (status === 'INSUFFICIENT_DATA') return `Evaluation unavailable${reasons.length ? `: ${reasons.map(label).join(' · ')}` : '.'}`;
  return reasons.map(label).join(' · ');
};

const CapacityCharts: React.FC<{
  response: RdsRightsizingResponse;
  selected: RdsTierOption;
  trend: any;
}> = ({ response, selected, trend }) => {
  const currentVcpus = Number(response.current.vcpus || 0);
  const currentMemory = Number(response.current.memory_gib || 0);
  const targetVcpus = selected.target_vcpus;
  const targetMemory = selected.target_memory_gib;
  const cpuData = (trend?.cpu?.daily || []).map((point: any) => ({
    date: new Date(point.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    projected: currentVcpus && targetVcpus ? Number(point.value) * currentVcpus / targetVcpus : null,
  }));
  const memoryData = (trend?.freeable_memory?.daily || []).map((point: any) => {
    const currentFree = Number(point.value) / 1024 ** 3;
    const used = currentMemory - currentFree;
    return {
      date: new Date(point.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      currentFree,
      projectedFree: targetMemory - used,
    };
  });
  const floor = Number(response.policy.memory_absolute_free_floor_gib || 1);
  const { theme } = useTheme();
  return <div className="grid gap-6 xl:grid-cols-2">
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <h4 className="font-semibold text-gray-900 dark:text-white">CPU headroom on {selected.target_db_instance_class}</h4>
      <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">Decision-window projected CPU is {selected.projected_cpu_percent == null ? 'unmeasured' : `${selected.projected_cpu_percent.toFixed(1)}%`}; the chart scales each daily maximum to the target.</p>
      {cpuData.length ? <div className="mt-5 h-64" aria-label="Target-scaled daily CPU maximum chart">
        <ResponsiveContainer width="100%" height="100%"><LineChart data={cpuData}><CartesianGrid strokeDasharray="3 3" opacity={0.3} stroke={chartGridColor(theme)} /><XAxis dataKey="date" minTickGap={24} stroke={chartAxisLineColor(theme)} tick={{ fill: chartTickColor(theme) }} /><YAxis domain={[0, 'auto']} unit="%" stroke={chartAxisLineColor(theme)} tick={{ fill: chartTickColor(theme) }} /><Tooltip /><ReferenceLine y={100} stroke={STATUS_COLORS.error} label="Target ceiling" /><Line type="monotone" dataKey="projected" name="Projected target CPU" stroke={CHART_COLORS[0]} dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer>
      </div> : <p className="mt-5 text-sm text-gray-500">CPU trend is unavailable.</p>}
    </Card>
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <h4 className="font-semibold text-gray-900 dark:text-white">Modeled free memory on target</h4>
      <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">Each daily current FreeableMemory minimum is converted to estimated used memory, then projected onto the target. Negative values are intentionally visible.</p>
      {selected.projected_memory_util == null ? <p className="mt-5 text-sm text-warning-700 dark:text-warning-300">FreeableMemory was unmeasured, so current memory capacity was retained and no projection is shown.</p> : memoryData.length ? <div className="mt-5 h-64" aria-label="Projected target freeable memory chart">
        <ResponsiveContainer width="100%" height="100%"><LineChart data={memoryData}><CartesianGrid strokeDasharray="3 3" opacity={0.3} stroke={chartGridColor(theme)} /><XAxis dataKey="date" minTickGap={24} stroke={chartAxisLineColor(theme)} tick={{ fill: chartTickColor(theme) }} /><YAxis unit=" GiB" stroke={chartAxisLineColor(theme)} tick={{ fill: chartTickColor(theme) }} /><Tooltip /><Legend /><ReferenceLine y={floor} stroke={STATUS_COLORS.warning} label="Policy floor" /><Line type="monotone" dataKey="currentFree" name="Current free GiB" stroke={CHART_COLORS[4]} dot={false} /><Line type="monotone" dataKey="projectedFree" name="Projected target free GiB" stroke={STATUS_COLORS.healthy} dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer>
      </div> : <p className="mt-5 text-sm text-gray-500">FreeableMemory trend is unavailable.</p>}
    </Card>
  </div>;
};

export const RdsRightsizerPanel: React.FC<{ inventoryId: number }> = ({ inventoryId }) => {
  const recommendationQuery = useQuery(
    ['rds-rightsizer', inventoryId],
    () => rightsizingApi.getRdsRecommendation(inventoryId),
    { retry: false },
  );
  const trendQuery = useQuery(
    ['rds-rightsizer-trend', inventoryId],
    () => rightsizingApi.getRdsTrend(inventoryId),
    { enabled: Boolean(recommendationQuery.data && recommendationQuery.data.classification !== 'DEFERRED'), retry: false },
  );
  const [selectedTier, setSelectedTier] = useState<TierName | null>(null);
  const [selectedCandidateClass, setSelectedCandidateClass] = useState<string | null>(null);
  const data = recommendationQuery.data;
  const kinds = data ? recommendationKinds(data) : { classRecommendation: undefined, storageRecommendation: undefined };
  useEffect(() => {
    if (!selectedTier && kinds.classRecommendation?.tiers.default) setSelectedTier(kinds.classRecommendation.tiers.default);
  }, [kinds.classRecommendation, selectedTier]);
  const selected = useMemo(() => {
    if (!kinds.classRecommendation) return undefined;
    if (selectedCandidateClass) {
      const candidate = kinds.classRecommendation.candidates.find((item) => item.target_db_instance_class === selectedCandidateClass);
      if (candidate) return candidate;
    }
    return (selectedTier ? kinds.classRecommendation.tiers[selectedTier] : undefined) || kinds.classRecommendation.candidates[0];
  }, [kinds.classRecommendation, selectedCandidateClass, selectedTier]);

  if (recommendationQuery.isLoading) return <Card><div className="animate-pulse text-sm text-gray-500">Evaluating RDS rightsizing evidence…</div></Card>;
  if (recommendationQuery.isError || !data) return <Card className="border-warning-200 bg-warning-50 dark:border-warning-900/50 dark:bg-warning-950/30"><div className="flex gap-3"><AlertTriangle className="text-warning-600" size={18} /><div><div className="font-semibold">Rightsizing recommendation unavailable</div><div className="mt-1 text-sm">Inventory and runbook actions remain available. No target has been inferred.</div></div></div></Card>;
  if (data.classification === 'DEFERRED') return <Card><div className="flex gap-3"><Info size={18} /><div><div className="font-semibold">Rightsizing deferred</div><div className="mt-1 text-sm text-gray-600 dark:text-gray-300">{(data.deferred_reason_codes || []).map(label).join(' · ')}</div></div></div></Card>;

  return <section aria-labelledby="rds-rightsizer-title" className="space-y-6">
    <Card className="border-primary-200 bg-white/95 dark:border-primary-900/60 dark:bg-gray-900/95">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between"><div>
        <div className="flex items-center gap-2 text-primary-700 dark:text-primary-300"><Gauge size={18} /><span className="text-xs font-semibold uppercase tracking-[0.18em]">Read-only advisory</span></div>
        <h2 id="rds-rightsizer-title" className="mt-3 text-2xl font-semibold text-gray-950 dark:text-white">Rightsizing recommendation</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600 dark:text-gray-300">Instance class and storage configuration are evaluated independently. Savings are not additive and no change is applied here.</p>
      </div>{selected || kinds.storageRecommendation ? <div className="rounded-2xl bg-primary-50 px-5 py-4 text-right dark:bg-primary-950/40"><div className="text-xs font-semibold uppercase tracking-[0.16em] text-primary-700 dark:text-primary-300">Selected monthly savings</div><div className="mt-1 text-3xl font-semibold text-primary-800 dark:text-primary-200">{money(selected?.monthly_savings ?? kinds.storageRecommendation?.monthly_savings)}</div></div> : null}</div>
      {data.classification == null ? <div className="mt-5 rounded-2xl bg-gray-50 p-4 text-sm text-gray-700 dark:bg-gray-800/70 dark:text-gray-200">No cost-saving RDS rightsizing recommendation was found under the current policy.</div> : <span className={`mt-5 inline-flex rounded-full px-3 py-1.5 text-xs font-semibold ${rightsizingClassificationClass(data.classification)}`}>{data.classification}</span>}
    </Card>

    {data.evaluation_status.instance_class !== 'RECOMMENDED' ? <Card><div className="font-semibold">Instance-class evaluation</div><p className="mt-2 text-sm text-gray-600 dark:text-gray-300">{statusCopy(data.evaluation_status.instance_class, data.evaluation_reason_codes.instance_class)}</p></Card> : null}
    {kinds.classRecommendation ? <div className="space-y-5">
      {kinds.classRecommendation.tiers.default ? <div className="grid gap-4 lg:grid-cols-3" role="radiogroup" aria-label="RDS class tiers">{(['conservative', 'balanced', 'aggressive'] as TierName[]).map((tier) => {
        const option = kinds.classRecommendation!.tiers[tier];
        if (!option) return null;
        const checked = selected?.target_db_instance_class === option.target_db_instance_class && selectedTier === tier;
        return <button key={tier} type="button" role="radio" aria-checked={checked} onClick={() => { setSelectedTier(tier); setSelectedCandidateClass(null); }} className={`rounded-2xl border p-5 text-left focus:outline-none focus:ring-2 focus:ring-primary-500 ${checked ? 'border-primary-500 bg-primary-50 dark:bg-primary-950/30' : 'border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900'}`}><div className="flex justify-between gap-2"><div className="font-semibold capitalize">{tier}{tier === 'balanced' ? <span className="ml-2 text-xs text-primary-700">Recommended</span> : null}</div><span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${rightsizingClassificationClass(option.classification)}`}>{option.classification}</span></div><div className="mt-3 text-lg font-semibold">{option.target_db_instance_class}</div><div className="mt-1 text-sm text-gray-500">{option.target_vcpus} vCPU · {option.target_memory_gib} GiB</div><div className="mt-4 text-2xl font-semibold">{money(option.monthly_savings)}<span className="text-sm font-normal text-gray-500">/mo</span></div><div className="mt-2 text-xs text-gray-500">{option.projected_util == null ? 'Compute utilization unmeasured' : `Maximum projected utilization ${(option.projected_util * 100).toFixed(1)}%`}{option.binding_dimension ? ` · ${label(option.binding_dimension)} binds` : ''}</div></button>;
      })}</div> : <Card><div className="font-semibold">Capacity-retaining alternatives</div><p className="mt-2 text-sm text-warning-700 dark:text-warning-300">CPU and FreeableMemory are unmeasured. These conditional options retain current capacity and are savings-ranked without tiers.</p><div className="mt-4 grid gap-3 md:grid-cols-2" role="radiogroup" aria-label="Capacity-retaining class alternatives">{kinds.classRecommendation.candidates.map((candidate) => <button key={candidate.target_db_instance_class} type="button" role="radio" aria-checked={selected?.target_db_instance_class === candidate.target_db_instance_class} onClick={() => { setSelectedTier(null); setSelectedCandidateClass(candidate.target_db_instance_class); }} className={`rounded-xl border p-4 text-left ${selected?.target_db_instance_class === candidate.target_db_instance_class ? 'border-primary-500 bg-primary-50 dark:bg-primary-950/30' : 'border-gray-200 dark:border-gray-700'}`}><div className="font-semibold">{candidate.target_db_instance_class}</div><div className="mt-1 text-xs text-gray-500">{candidate.target_vcpus} vCPU · {candidate.target_memory_gib} GiB · {money(candidate.monthly_savings)}/mo</div></button>)}</div></Card>}
      {selected ? <Card><div className="flex items-center gap-2"><Database size={17} /><h3 className="font-semibold">Selected class evidence</h3></div><div className="mt-4 overflow-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead><tr className="text-gray-500"><th className="py-2">Dimension</th><th>Observed</th><th>Projected</th><th>Target capacity</th></tr></thead><tbody className="divide-y divide-gray-100 dark:divide-gray-800"><tr><td className="py-3">CPU</td><td>{selected.evidence.cpu_p99_percent == null ? 'Missing' : `${Number(selected.evidence.cpu_p99_percent).toFixed(1)}% p99`}</td><td>{selected.projected_cpu_percent == null ? 'Current vCPU retained' : `${selected.projected_cpu_percent.toFixed(1)}%`}</td><td>{selected.target_vcpus} vCPU</td></tr><tr><td className="py-3">Memory</td><td>{selected.evidence.freeable_memory_p01_bytes == null ? 'Missing' : `${(Number(selected.evidence.freeable_memory_p01_bytes) / 1024 ** 3).toFixed(1)} GiB p01 free`}</td><td>{selected.projected_freeable_gib == null ? 'Current memory retained' : `${selected.projected_freeable_gib.toFixed(1)} GiB free`}</td><td>{selected.target_memory_gib} GiB</td></tr><tr><td className="py-3">Network</td><td>{selected.evidence.network_p99_mbps == null ? 'Missing' : `${Number(selected.evidence.network_p99_mbps).toFixed(1)} Mbps p99`}</td><td>{selected.evidence.target_network_baseline_mbps == null ? 'Unknown capacity' : 'Documented capacity'}</td><td>{selected.evidence.target_network_peak_mbps == null ? 'Unknown peak' : `${selected.evidence.target_network_peak_mbps} Mbps peak`}</td></tr><tr><td className="py-3">Storage IOPS</td><td>{selected.evidence.storage_iops_p99 ?? 'Missing'}</td><td>{selected.evidence.target_ebs_baseline_iops == null ? 'Unknown capacity' : 'Documented capacity'}</td><td>{selected.evidence.target_ebs_peak_iops ?? 'Unknown peak'}</td></tr></tbody></table></div>{selected.reason_codes.length ? <div className="mt-4 flex flex-wrap gap-2">{selected.reason_codes.map((reason) => <span key={reason} className="rounded-full bg-warning-50 px-3 py-1.5 text-xs text-warning-800 dark:bg-warning-950/40 dark:text-warning-200">{label(reason)}</span>)}</div> : <div className="mt-4 flex gap-2 text-sm text-success-700"><CheckCircle2 size={18} />All configured actionable constraints passed.</div>}</Card> : null}
      {selected && trendQuery.data ? <CapacityCharts response={data} selected={selected} trend={trendQuery.data} /> : trendQuery.isError ? <Card className="border-warning-200"><div className="flex gap-2 text-sm text-warning-700"><AlertTriangle size={18} />Long-range evidence is unavailable. The recommendation remains visible.</div></Card> : null}
    </div> : null}

    {kinds.storageRecommendation ? <Card><div className="flex items-center gap-2"><HardDrive size={17} /><h3 className="font-semibold">Storage configuration</h3></div><div className="mt-4 overflow-auto"><table className="w-full text-left text-sm"><thead><tr className="text-gray-500"><th className="py-2">Setting</th><th>Current</th><th>Recommended</th></tr></thead><tbody><tr><td className="py-3">Type</td><td>{kinds.storageRecommendation.current_storage.storage_type}</td><td>{kinds.storageRecommendation.target_storage.storage_type}</td></tr><tr><td className="py-3">Allocated storage</td><td>{kinds.storageRecommendation.current_storage.allocated_storage_gib} GiB</td><td>{kinds.storageRecommendation.target_storage.allocated_storage_gib} GiB (unchanged)</td></tr><tr><td className="py-3">IOPS</td><td>{kinds.storageRecommendation.current_storage.iops ?? 'Included'}</td><td>{kinds.storageRecommendation.target_storage.iops ?? 'Included'}</td></tr><tr><td className="py-3">Throughput</td><td>{kinds.storageRecommendation.current_storage.throughput_mibps ?? 'Included'} MiB/s</td><td>{kinds.storageRecommendation.target_storage.throughput_mibps ?? 'Included'} MiB/s</td></tr></tbody></table></div><p className="mt-4 text-sm text-gray-600 dark:text-gray-300">RDS doesn't support reducing allocated storage. This option changes only paid storage performance and/or storage type.</p></Card> : data.evaluation_status.storage_configuration !== 'RECOMMENDED' ? <Card><div className="font-semibold">Storage evaluation</div><p className="mt-2 text-sm text-gray-600 dark:text-gray-300">{statusCopy(data.evaluation_status.storage_configuration, data.evaluation_reason_codes.storage_configuration)}</p></Card> : null}

    <Card><div className="flex items-center gap-2"><ShieldAlert size={17} /><h3 className="font-semibold">Database-load attribution</h3></div>{data.database_load_attribution.status === 'AVAILABLE' ? <div className="mt-4"><div className="text-sm text-gray-600 dark:text-gray-300">DB load measures active sessions running on CPU or waiting for a resource. A large non-CPU share can indicate I/O, locks, or query behavior that a larger instance alone may not fix.</div><div className="mt-4 space-y-2">{data.database_load_attribution.wait_type_shares.map((item) => <div key={item.name} className="flex justify-between text-sm"><span>{item.name}</span><span>{(item.share * 100).toFixed(1)}% · p99 {item.aas_p99?.toFixed(2) ?? 'n/a'} AAS</span></div>)}</div></div> : data.database_load_attribution.enablement_prompt ? <div className="mt-4 rounded-2xl bg-warning-50 p-4 text-sm text-warning-900 dark:bg-warning-950/40 dark:text-warning-100"><div className="font-semibold">{data.database_load_attribution.enablement_prompt.title}</div><p className="mt-2">{data.database_load_attribution.enablement_prompt.message}</p><p className="mt-2">Enabling Performance Insights doesn't require a reboot, failover, or database outage.</p><a className="mt-3 inline-flex text-primary-700 underline" href={data.database_load_attribution.enablement_prompt.documentation_url} target="_blank" rel="noreferrer">Open AWS documentation</a></div> : <p className="mt-3 text-sm text-gray-600 dark:text-gray-300">{data.database_load_attribution.status === 'NOT_NEEDED' ? "Database-load attribution wasn't required because standard evidence had ample headroom." : data.database_load_attribution.status === 'UNSUPPORTED' ? 'Database-load attribution is unsupported for this configuration; use manual review.' : data.database_load_attribution.status === 'ACCESS_DENIED' ? 'Add pi:GetResourceMetrics to collect database-load evidence.' : 'Database-load evidence is temporarily unavailable.'}</p>}</Card>

    <Card><div className="flex items-center gap-2"><ShieldAlert size={17} /><h3 className="font-semibold">Operational review</h3></div><p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">{data.operational_note}</p><p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">{data.availability_note}</p>{data.classification === 'CONDITIONAL' ? <div className="mt-4 flex gap-2 rounded-2xl bg-warning-50 p-4 text-sm text-warning-800 dark:bg-warning-950/40 dark:text-warning-200"><ShieldAlert size={18} />Review all named uncertainties before scheduling a modification.</div> : null}</Card>
  </section>;
};
