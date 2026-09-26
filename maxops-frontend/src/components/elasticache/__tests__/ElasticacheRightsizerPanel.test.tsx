import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@/test/utils/test-utils';
import { ElasticacheRightsizerPanel } from '../ElasticacheRightsizerPanel';

const mocks = vi.hoisted(() => ({
  getRecommendation: vi.fn(),
  getTrend: vi.fn(),
}));

vi.mock('@/services/recommendations', () => ({
  rightsizingApi: {
    getElasticacheRecommendation: mocks.getRecommendation,
    getElasticacheTrend: mocks.getTrend,
  },
}));

const risk = {
  telemetry: 'LOW', compute: 'LOW', memory: 'LOW', network: 'LOW',
  cache_health: 'LOW', compatibility: 'LOW', migration: 'MEDIUM',
  overall: 'LOW', reason_codes: [],
};

const node = {
  kind: 'NODE_TYPE_CHANGE', rank: 1, target_node_type: 'cache.m6g.large',
  target_monthly_cost: 200, monthly_savings: 100, yearly_savings: 1200,
  projected_engine_cpu: 30, projected_host_cpu: 40, projected_memory_util: 50,
  projected_util: 0.5, binding_dimension: 'memory', classification: 'ACTIONABLE',
  risk_assessment: risk, reason_codes: [], warning_details: [], compute_evidence: {},
  network_evaluation: {}, memory_evaluation: {}, cache_health: {}, required_review: false,
};

const recommendation = {
  inventory_id: 1, resource_id: 'rg-prod', resource_name: 'Production cache',
  account_id: '123', region: 'us-east-1', state: 'available', classification: 'ACTIONABLE',
  deferred_reason_codes: [], current_monthly_cost: 300, engine: 'redis', engine_version: '7.1',
  current_node_type: 'cache.m5.large', node_count: 3, cluster_mode_enabled: true,
  num_node_groups: 100, recommendations: [node],
  tiers: {
    default: 'balanced', conservative: null, aggressive: null,
    balanced: {
      tier: 'balanced', target_node_type: node.target_node_type,
      monthly_savings: node.monthly_savings, yearly_savings: node.yearly_savings,
      projected_util: node.projected_util, binding_dimension: node.binding_dimension,
      classification: node.classification, risk_assessment: risk, recommendation_rank: 1,
    },
  },
  rejection_summary: {},
  replica_recommendation: {
    kind: 'REPLICA_COUNT_REDUCTION', classification: 'CONDITIONAL',
    current_replicas_per_node_group: 2, target_replicas_per_node_group: 1,
    affected_node_group_count: 100, removed_node_count: 100,
    projected_survivor_engine_cpu: 45, monthly_savings: 80, yearly_savings: 960,
    risk_assessment: { ...risk, overall: 'HIGH' },
    reason_codes: ['REPLICA_READ_TRAFFIC_OBSERVED'],
    redundancy_disclosure: 'Reducing replicas reduces read redundancy and failover headroom.',
    member_evidence: [],
  },
  operational_note: 'Online scaling can replace nodes.', availability_note: 'Validate availability.',
  telemetry_summary: {},
};

const hottest = (selectionReason: string) => ({
  cache_cluster_id: 'cache-099', node_group_id: '0099', role: 'primary',
  selection_reason: selectionReason, daily: [],
  buckets: {
    '30d': { maximum: 40, p99: 35, p95: 30 },
    '180d': { maximum: 50, p99: 40, p95: 35 },
    '455d': { maximum: 60, p99: 45, p95: 40 },
  },
});

const trend = {
      inventory_id: 1, resource_id: 'rg-prod', cached: false, bucket_semantics: {},
      metrics: {
        engine_cpu: [hottest('hottest_engine_cpu')],
        memory: [hottest('hottest_memory')],
      },
      selection_summary: {
        engine_cpu: { primary_total: 100, primary_returned: 8, primary_omitted: 92, primary_context_limit: 8 },
        memory: { primary_total: 100, primary_returned: 8, primary_omitted: 92, primary_context_limit: 8 },
      },
    };

describe('ElasticacheRightsizerPanel', () => {
  it('defaults to Balanced and keeps replica selection mutually exclusive', async () => {
    mocks.getRecommendation.mockResolvedValue(recommendation);
    mocks.getTrend.mockResolvedValue(trend);
    render(<ElasticacheRightsizerPanel inventoryId={1} />);
    const balanced = await screen.findByRole('radio', { name: /Balanced/i });
    await waitFor(() => expect(balanced).toHaveAttribute('aria-checked', 'true'));
    const replica = screen.getByRole('radio', { name: /Reduce replicas/i });
    fireEvent.click(replica);
    expect(replica).toHaveAttribute('aria-checked', 'true');
    expect(balanced).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/Reducing replicas reduces read redundancy/i)).toBeInTheDocument();
  });

  it('discloses capped shard-primary context and separates Execute controls', async () => {
    mocks.getRecommendation.mockResolvedValue(recommendation);
    mocks.getTrend.mockResolvedValue(trend);
    render(<ElasticacheRightsizerPanel inventoryId={1} />);
    expect(await screen.findAllByText(/Showing 8 of 100 shard primaries/)).toHaveLength(2);
    expect(screen.getByText(/Runbook actions are separate/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Execute$/i })).not.toBeInTheDocument();
  });
});
