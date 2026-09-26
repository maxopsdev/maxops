import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'react-query';
import { AlertTriangle, CheckCircle2, Gauge, Info, Server, ShieldAlert } from 'lucide-react';
import { Card } from '@/components/common/Card';
import {
  rightsizingApi,
  type ElastiCacheNodeCandidate,
  type ElastiCacheReplicaRecommendation,
  type ElastiCacheRightsizingResponse,
  type TierName,
} from '@/services/recommendations';

const money = (value: number) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 0,
}).format(value);

const classificationStyle: Record<string, string> = {
  ACTIONABLE: 'bg-success-100 text-success-800 dark:bg-success-950/60 dark:text-success-200',
  CONDITIONAL: 'bg-warning-100 text-warning-800 dark:bg-warning-950/60 dark:text-warning-200',
  REJECTED: 'bg-danger-100 text-danger-800 dark:bg-danger-950/60 dark:text-danger-200',
  DEFERRED: 'bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-200',
  NO_CANDIDATE: 'bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-200',
};

const reasonLabel = (value: string) => value.toLowerCase().split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');

interface Selection {
  id: string;
  kind: 'node' | 'replica';
  label: string;
  classification: string;
  monthlySavings: number;
  yearlySavings: number;
  projectedUtil?: number;
  bindingDimension?: string;
  reasons: string[];
  node?: ElastiCacheNodeCandidate;
  replica?: ElastiCacheReplicaRecommendation;
}

const buildSelections = (recommendation: ElastiCacheRightsizingResponse): Selection[] => {
  const rows: Selection[] = [];
  const nodeRows = new Map<string, Selection & { tiers: TierName[] }>();
  (['conservative', 'balanced', 'aggressive'] as TierName[]).forEach((tier) => {
    const option = recommendation.tiers[tier];
    if (!option) return;
    const node = recommendation.recommendations.find((item) => item.target_node_type === option.target_node_type);
    const existing = nodeRows.get(option.target_node_type);
    if (existing) {
      existing.tiers.push(tier);
      if (tier === recommendation.tiers.default) existing.id = `node:${tier}`;
      return;
    }
    nodeRows.set(option.target_node_type, {
      id: `node:${tier}`, kind: 'node', label: '', tiers: [tier],
      classification: option.classification,
      monthlySavings: option.monthly_savings,
      yearlySavings: option.yearly_savings,
      projectedUtil: option.projected_util,
      bindingDimension: option.binding_dimension,
      reasons: node?.reason_codes || [],
      node,
    });
  });
  nodeRows.forEach((row) => {
    row.label = row.tiers.map((tier) => tier.charAt(0).toUpperCase() + tier.slice(1)).join(' · ');
    rows.push(row);
  });
  if (recommendation.replica_recommendation) {
    const replica = recommendation.replica_recommendation;
    rows.push({
      id: 'replica', kind: 'replica', label: 'Reduce replicas',
      classification: replica.classification,
      monthlySavings: replica.monthly_savings,
      yearlySavings: replica.yearly_savings,
      reasons: replica.reason_codes,
      replica,
    });
  }
  return rows;
};

export const ElasticacheRightsizerPanel: React.FC<{ inventoryId: number }> = ({ inventoryId }) => {
  const recommendationQuery = useQuery(
    ['elasticache-rightsizer', inventoryId],
    () => rightsizingApi.getElasticacheRecommendation(inventoryId),
    { retry: false },
  );
  const trendQuery = useQuery(
    ['elasticache-rightsizer-trend', inventoryId],
    () => rightsizingApi.getElasticacheTrend(inventoryId),
    { enabled: Boolean(recommendationQuery.data && recommendationQuery.data.classification !== 'DEFERRED'), retry: false },
  );
  const selections = useMemo(() => recommendationQuery.data ? buildSelections(recommendationQuery.data) : [], [recommendationQuery.data]);
  const [selectedId, setSelectedId] = useState('');
  const [showPrimaryContext, setShowPrimaryContext] = useState(false);
  useEffect(() => {
    if (!recommendationQuery.data || selectedId) return;
    const defaultTier = recommendationQuery.data.tiers.default;
    setSelectedId(defaultTier ? `node:${defaultTier}` : selections[0]?.id || '');
  }, [recommendationQuery.data, selectedId, selections]);
  const selected = selections.find((item) => item.id === selectedId) || selections[0];

  if (recommendationQuery.isLoading) {
    return <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95"><div className="animate-pulse text-sm text-gray-500">Evaluating ElastiCache rightsizing evidence…</div></Card>;
  }
  if (recommendationQuery.isError || !recommendationQuery.data) {
    return <Card className="border-warning-200 bg-warning-50 dark:border-warning-900/50 dark:bg-warning-950/30"><div className="flex gap-3"><AlertTriangle className="mt-0.5 text-warning-600" size={18} /><div><div className="font-semibold text-warning-900 dark:text-warning-100">Rightsizing recommendation unavailable</div><div className="mt-1 text-sm text-warning-700 dark:text-warning-200">Inventory and runbook actions remain available. No recommendation has been inferred.</div></div></div></Card>;
  }
  const recommendation = recommendationQuery.data;
  if (recommendation.classification === 'DEFERRED') {
    return <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95"><div className="flex gap-3"><Info className="mt-0.5 text-gray-500" size={18} /><div><div className="font-semibold text-gray-900 dark:text-white">Rightsizing deferred</div><div className="mt-1 text-sm text-gray-600 dark:text-gray-300">{recommendation.deferred_reason_codes.map(reasonLabel).join(' · ')}</div></div></div></Card>;
  }

  return (
    <section aria-labelledby="elasticache-rightsizer-title" className="space-y-6">
      <Card className="border-success-200 bg-white/95 dark:border-success-900/60 dark:bg-gray-900/95">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-success-700 dark:text-success-300"><Gauge size={18} /><span className="text-xs font-semibold uppercase tracking-[0.18em]">Read-only advisory</span></div>
            <h2 id="elasticache-rightsizer-title" className="mt-3 text-2xl font-semibold text-gray-950 dark:text-white">Rightsizing recommendation</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600 dark:text-gray-300">Choose one alternative to inspect. Node-type and replica changes are evaluated independently and are never combined.</p>
          </div>
          {selected ? <div className="rounded-2xl bg-success-50 px-5 py-4 text-right dark:bg-success-950/40"><div className="text-xs font-semibold uppercase tracking-[0.16em] text-success-700 dark:text-success-300">Potential monthly savings</div><div className="mt-1 text-3xl font-semibold text-success-800 dark:text-success-200">{money(selected.monthlySavings)}</div></div> : null}
        </div>
      </Card>

      {selections.length === 0 ? (
        <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95"><div className="font-semibold text-gray-900 dark:text-white">No cost-saving candidate passed the current policy</div><div className="mt-2 text-sm text-gray-600 dark:text-gray-300">Telemetry remains visible; rejected candidates are not presented as opportunities.</div></Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3" role="radiogroup" aria-label="Rightsizing alternatives">
          {selections.map((option) => {
            const checked = option.id === selected?.id;
            return <button key={option.id} type="button" role="radio" aria-checked={checked} onClick={() => setSelectedId(option.id)} className={`rounded-2xl border p-5 text-left transition focus:outline-none focus:ring-2 focus:ring-success-500 ${checked ? 'border-success-500 bg-success-50 shadow-sm dark:bg-success-950/30' : 'border-gray-200 bg-white hover:border-success-300 dark:border-gray-700 dark:bg-gray-900'}`}>
              <div className="flex items-start justify-between gap-3"><div><div className="text-sm font-semibold text-gray-900 dark:text-white">{option.label}{option.id === 'node:balanced' ? <span className="ml-2 text-xs text-success-700 dark:text-success-300">Recommended</span> : null}</div><div className="mt-1 text-xs text-gray-500">{option.kind === 'node' ? option.node?.target_node_type : `${option.replica?.current_replicas_per_node_group} → ${option.replica?.target_replicas_per_node_group} replicas per shard`}</div></div><span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${classificationStyle[option.classification]}`}>{option.classification}</span></div>
              <div className="mt-5 text-2xl font-semibold text-gray-950 dark:text-white">{money(option.monthlySavings)}<span className="text-sm font-normal text-gray-500">/mo</span></div>
              {option.projectedUtil !== undefined ? <div className="mt-2 text-xs text-gray-500">Projected utilization {Math.round(option.projectedUtil * 100)}% · {option.bindingDimension} binds</div> : <div className="mt-2 text-xs text-gray-500">{option.replica?.removed_node_count} nodes removed across {option.replica?.affected_node_group_count} shards</div>}
            </button>;
          })}
        </div>
      )}

      {!recommendation.replica_recommendation && (recommendation.replica_deferred_reason_codes || []).length > 0 ? (
        <div className="flex gap-3 rounded-2xl border border-warning-200 bg-warning-50 p-4 text-sm text-warning-800 dark:border-warning-900/50 dark:bg-warning-950/30 dark:text-warning-200">
          <ShieldAlert size={18} className="shrink-0" />
          Replica analysis was not produced: {(recommendation.replica_deferred_reason_codes || []).map(reasonLabel).join(' · ')}.
        </div>
      ) : null}

      {selected ? <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
          <div className="flex items-center gap-2"><Server size={17} className="text-gray-500" /><h3 className="font-semibold text-gray-900 dark:text-white">Selected alternative</h3></div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70"><div className="text-xs uppercase tracking-[0.14em] text-gray-500">Yearly savings</div><div className="mt-2 text-xl font-semibold text-gray-900 dark:text-white">{money(selected.yearlySavings)}</div></div>
            <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70"><div className="text-xs uppercase tracking-[0.14em] text-gray-500">Classification</div><div className="mt-2 text-xl font-semibold text-gray-900 dark:text-white">{selected.classification}</div></div>
          </div>
          {selected.classification === 'CONDITIONAL' ? <div className="mt-4 flex gap-3 rounded-2xl bg-warning-50 p-4 text-sm text-warning-800 dark:bg-warning-950/40 dark:text-warning-200"><ShieldAlert size={18} className="shrink-0" />Review required before making a change.</div> : <div className="mt-4 flex gap-3 rounded-2xl bg-success-50 p-4 text-sm text-success-800 dark:bg-success-950/40 dark:text-success-200"><CheckCircle2 size={18} className="shrink-0" />The observed evidence passed the configured actionable policy.</div>}
          {selected.reasons.length > 0 ? <div className="mt-4 flex flex-wrap gap-2">{selected.reasons.map((reason) => <span key={reason} className="rounded-full bg-gray-100 px-3 py-1.5 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-200">{reasonLabel(reason)}</span>)}</div> : null}
          {selected.node ? <div className="mt-4 grid gap-3 sm:grid-cols-3"><div className="rounded-2xl bg-gray-50 p-3 dark:bg-gray-800/70"><div className="text-[10px] uppercase tracking-[0.12em] text-gray-500">Engine CPU p99</div><div className="mt-1 font-semibold text-gray-900 dark:text-white">{selected.node.projected_engine_cpu == null ? 'n/a' : `${Math.round(selected.node.projected_engine_cpu)}%`}</div></div><div className="rounded-2xl bg-gray-50 p-3 dark:bg-gray-800/70"><div className="text-[10px] uppercase tracking-[0.12em] text-gray-500">Projected memory</div><div className="mt-1 font-semibold text-gray-900 dark:text-white">{selected.node.projected_memory_util == null ? 'n/a' : `${Math.round(selected.node.projected_memory_util)}%`}</div></div><div className="rounded-2xl bg-gray-50 p-3 dark:bg-gray-800/70"><div className="text-[10px] uppercase tracking-[0.12em] text-gray-500">Network risk</div><div className="mt-1 font-semibold text-gray-900 dark:text-white">{selected.node.risk_assessment.network}</div></div></div> : null}
          {selected.replica ? <p className="mt-4 text-sm leading-6 text-gray-600 dark:text-gray-300">{selected.replica.redundancy_disclosure}</p> : null}
          {selected.replica ? <div className="mt-4 max-h-64 overflow-auto rounded-2xl border border-gray-200 dark:border-gray-700"><table className="w-full min-w-[680px] text-left text-xs"><thead className="sticky top-0 bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"><tr><th className="px-3 py-2">Member</th><th className="px-3 py-2">Shard</th><th className="px-3 py-2">Decision</th><th className="px-3 py-2">Read p99 / max</th><th className="px-3 py-2">Coverage</th><th className="px-3 py-2">Observed</th><th className="px-3 py-2">Freshness</th></tr></thead><tbody>{selected.replica.member_evidence.map((member) => <tr key={String(member.cache_cluster_id)} className="border-t border-gray-100 dark:border-gray-800"><td className="px-3 py-2 text-gray-900 dark:text-white">{String(member.cache_cluster_id)}</td><td className="px-3 py-2">{String(member.node_group_id ?? 'n/a')}</td><td className="px-3 py-2">{member.removed ? 'Remove' : 'Retain'}</td><td className="px-3 py-2">{String(member.read_ops_p99 ?? 'n/a')} / {String(member.read_ops_max ?? 'n/a')}</td><td className="px-3 py-2">{member.read_coverage_ratio == null ? 'n/a' : `${Math.round(Number(member.read_coverage_ratio) * 100)}%`}</td><td className="px-3 py-2">{member.read_observed_days == null ? 'n/a' : `${Math.round(Number(member.read_observed_days))}d`}</td><td className="px-3 py-2">{member.read_latest_sample_age_seconds == null ? 'n/a' : `${Math.round(Number(member.read_latest_sample_age_seconds))}s`}</td></tr>)}</tbody></table></div> : null}
        </Card>

        <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
          <div className="flex items-center justify-between gap-3"><h3 className="font-semibold text-gray-900 dark:text-white">Long-term confidence</h3>{trendQuery.data?.metrics && (trendQuery.data.metrics.engine_cpu.some((series) => series.selection_reason === 'primary') || trendQuery.data.metrics.memory.some((series) => series.selection_reason === 'primary')) ? <button type="button" aria-pressed={showPrimaryContext} onClick={() => setShowPrimaryContext((value) => !value)} className="rounded-full border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 dark:border-gray-700 dark:text-gray-200">Shard primary context</button> : null}</div>
          {trendQuery.isLoading ? <div className="mt-4 text-sm text-gray-500">Loading 15-month CPU and memory evidence…</div> : trendQuery.isError || !trendQuery.data ? <div className="mt-4 rounded-2xl bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800/70">Long-term trend unavailable. The 60-day decision evidence remains authoritative.</div> : <div className="mt-4 space-y-4">{(['engine_cpu', 'memory'] as const).map((metric) => {
            const summary = trendQuery.data.selection_summary[metric];
            const hottest = trendQuery.data.metrics[metric].find((series) => series.selection_reason === (metric === 'engine_cpu' ? 'hottest_engine_cpu' : 'hottest_memory'));
            const primaryContext = trendQuery.data.metrics[metric].filter((series) => series.selection_reason === 'primary');
            return <div key={metric} className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70"><div className="flex items-center justify-between"><div className="text-sm font-semibold text-gray-800 dark:text-gray-100">{metric === 'engine_cpu' ? 'Engine CPU' : 'Memory'}</div><div className="text-xs text-gray-500">Hottest: {hottest?.cache_cluster_id || 'No series'}</div></div><div className="mt-3 grid grid-cols-3 gap-2">{['30d', '180d', '455d'].map((bucket) => <div key={bucket}><div className="text-[10px] uppercase tracking-[0.12em] text-gray-500">{bucket}</div><div className="mt-1 text-sm font-semibold text-gray-900 dark:text-white">{hottest?.buckets[bucket]?.maximum == null ? 'n/a' : `${Math.round(hottest.buckets[bucket].maximum!)}%`}</div></div>)}</div>{summary.primary_omitted > 0 ? <div className="mt-3 text-xs text-warning-700 dark:text-warning-300">Showing {summary.primary_returned} of {summary.primary_total} shard primaries ({summary.primary_omitted} omitted).</div> : null}{showPrimaryContext && primaryContext.length > 0 ? <div className="mt-3 flex flex-wrap gap-1.5" aria-label={`${metric} shard primary context`}>{primaryContext.map((series) => <span key={series.cache_cluster_id} className="rounded-full bg-white px-2.5 py-1 text-[11px] text-gray-600 dark:bg-gray-900 dark:text-gray-300">{series.node_group_id}: {series.cache_cluster_id}</span>)}</div> : null}</div>;
          })}</div>}
        </Card>
      </div> : null}

      <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"><strong>Runbook actions are separate.</strong> Selecting an option here does not preselect, enable, or execute an action. Run a new scan after any topology change.</div>
    </section>
  );
};
