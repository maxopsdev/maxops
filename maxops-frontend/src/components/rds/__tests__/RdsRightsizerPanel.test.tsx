import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/utils/test-utils';
import { RdsRightsizerPanel } from '../RdsRightsizerPanel';

const mocks = vi.hoisted(() => ({
  getRecommendation: vi.fn(),
  getTrend: vi.fn(),
}));

vi.mock('@/services/recommendations', () => ({
  rightsizingApi: {
    getRdsRecommendation: mocks.getRecommendation,
    getRdsTrend: mocks.getTrend,
  },
}));

const risk = {
  telemetry: 'LOW', compute: 'LOW', memory: 'LOW', db_load: 'NOT_NEEDED',
  storage: 'LOW', network: 'LOW', connections: 'LOW', compatibility: 'LOW',
  operations: 'LOW', overall: 'LOW', reason_codes: [],
};

const target = {
  kind: 'DB_INSTANCE_CLASS_CHANGE', rank: 1,
  target_db_instance_class: 'db.m6i.large', target_vcpus: 2, target_memory_gib: 8,
  projected_cpu_percent: 40, projected_memory_util: 0.5, projected_freeable_gib: 4,
  projected_util: 0.5, binding_dimension: 'memory', target_monthly_cost: 100,
  monthly_savings: 100, yearly_savings: 1200, classification: 'ACTIONABLE',
  satisfied_tiers: ['conservative', 'balanced', 'aggressive'], risk_assessment: risk,
  reason_codes: [], warning_details: [], evidence: {
    cpu_p99_percent: 20, freeable_memory_p01_bytes: 12 * 1024 ** 3,
    network_p99_mbps: 50, storage_iops_p99: 1000, storage_throughput_p99_mibps: 50,
    target_network_baseline_mbps: 1000, target_network_peak_mbps: 5000,
    target_ebs_baseline_iops: 12000, target_ebs_peak_iops: 20000,
    target_ebs_baseline_mibps: 500, target_ebs_peak_mibps: 1000,
  },
};

const recommendation = {
  inventory_id: 1, resource_id: 'db-prod', resource_name: 'Production DB',
  account_id: '123', region: 'us-east-1', state: 'available', engine: 'postgres',
  engine_version: '16.3', license_model: 'postgresql-license', multi_az: true,
  read_replica: false, classification: 'ACTIONABLE',
  evaluation_status: { instance_class: 'RECOMMENDED', storage_configuration: 'NO_RECOMMENDATION' },
  evaluation_reason_codes: { instance_class: [], storage_configuration: ['NO_SAVING_STORAGE_TARGET'] },
  current: { db_instance_class: 'db.m5.xlarge', vcpus: 4, memory_gib: 16, monthly_cost: 200, storage_type: 'gp3', allocated_storage_gib: 100, iops: 3000, storage_throughput_mibps: 125 },
  policy: { memory_absolute_free_floor_gib: 1 }, pricing_scope: {},
  recommendations: [{
    kind: 'DB_INSTANCE_CLASS_CHANGE', classification: 'ACTIONABLE', candidates: [target],
    tiers: { default: 'balanced', conservative: target, balanced: target, aggressive: target },
  }],
  telemetry_summary: {},
  database_load_attribution: { status: 'NOT_NEEDED', required: false, observed_days: null, total_load: null, cpu_load: null, non_cpu_load: null, unattributed_load: null, wait_type_shares: [], enablement_prompt: null },
  rejection_summary: {}, operational_note: 'Changing class causes an outage.',
  availability_note: 'Availability is checked at scan time.',
};

describe('RdsRightsizerPanel', () => {
  it('defaults to Balanced, renders independent storage state, and remains advisory', async () => {
    mocks.getRecommendation.mockResolvedValue(recommendation);
    mocks.getTrend.mockRejectedValue(new Error('trend unavailable'));
    render(<RdsRightsizerPanel inventoryId={1} />);
    const balanced = await screen.findByRole('radio', { name: /balanced/i });
    await waitFor(() => expect(balanced).toHaveAttribute('aria-checked', 'true'));
    expect(screen.getAllByText(/\$100\.00/).length).toBeGreaterThan(0);
    expect(screen.getByText(/No Saving Storage Target/i)).toBeInTheDocument();
    expect(screen.getByText(/Long-range evidence is unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/causes an outage/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /apply|modify|execute|enable performance insights/i })).not.toBeInTheDocument();
  });

  it('names missing telemetry and offers PI guidance without enabling it', async () => {
    const retained = {
      ...target,
      projected_cpu_percent: null,
      projected_memory_util: null,
      projected_freeable_gib: null,
      projected_util: null,
      binding_dimension: null,
      classification: 'CONDITIONAL',
      satisfied_tiers: [],
      reason_codes: [
        'RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED',
        'RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED',
      ],
      evidence: { ...target.evidence, cpu_p99_percent: null, freeable_memory_p01_bytes: null },
    };
    mocks.getRecommendation.mockResolvedValue({
      ...recommendation,
      classification: 'CONDITIONAL',
      recommendations: [{
        kind: 'DB_INSTANCE_CLASS_CHANGE', classification: 'CONDITIONAL', candidates: [retained],
        tiers: { default: null, conservative: null, balanced: null, aggressive: null },
      }],
      database_load_attribution: {
        status: 'DISABLED', required: true, observed_days: null, total_load: null,
        cpu_load: null, non_cpu_load: null, unattributed_load: null, wait_type_shares: [],
        enablement_prompt: {
          title: 'Enable Database Insights for stronger confidence',
          message: 'Enable it and rescan. MaxOps will not enable it automatically.',
          documentation_url: 'https://docs.aws.amazon.com/rds/', causes_downtime: false,
          recommended_mode: 'standard', sufficient_retention_days: 7,
        },
      },
    });
    mocks.getTrend.mockResolvedValue({ cpu: { daily: [] }, freeable_memory: { daily: [] } });
    render(<RdsRightsizerPanel inventoryId={1} />);
    expect(await screen.findByText(/Capacity-retaining alternatives/i)).toBeInTheDocument();
    expect(screen.getAllByText(/^Missing$/i)).toHaveLength(2);
    expect(screen.getByText(/will not enable it automatically/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /enable/i })).not.toBeInTheDocument();
  });
});
