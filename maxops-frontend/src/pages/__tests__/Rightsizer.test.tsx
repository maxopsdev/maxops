import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@/test/utils/test-utils';
import { OptimizationProfileProvider } from '@/contexts/OptimizationProfileContext';
import { RightsizerDetailPage } from '../Rightsizer';

const mocks = vi.hoisted(() => ({
  getDetail: vi.fn(),
  applyRecommendation: vi.fn(),
}));

vi.mock('@/services/rightsizer', () => ({
  rightsizerApi: {
    getDetail: mocks.getDetail,
    applyRecommendation: mocks.applyRecommendation,
  },
}));

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useParams: () => ({ resourceType: 'ec2', inventoryId: '7' }),
    useNavigate: () => vi.fn(),
  };
});

const tier = (targetType: string, monthly: number) => ({
  target_instance_type: targetType,
  target_instance: { vcpus: 2, memory_gib: 8 },
  monthly_savings: monthly,
  yearly_savings: monthly * 12,
  projected_cpu_util: 0.45,
  projected_memory_util: 0.5,
  risk_assessment: { overall: 'LOW' },
  reason_codes: [],
});

const buildDetail = (overrides: Record<string, any> = {}) => ({
  resource_type: 'ec2',
  inventory_id: 7,
  resource: {
    inventory_id: 7,
    resource_id: 'i-PLACEHOLDER-INSTANCE',
    resource_name: 'payload-instance',
    account_id: '123456789012',
    region: 'us-east-1',
    state: 'running',
    current_type: 'm5.xlarge',
    tags: {},
  },
  status: 'ACTIONABLE',
  classification: 'ACTIONABLE',
  recommendation: null,
  recommendations: [],
  current_instance: { vcpus: 4, memory_gib: 16 },
  tiers: {
    default: 'balanced',
    conservative: null,
    balanced: tier('m5.large', 70),
    aggressive: tier('c5.large', 95),
  },
  deferred_reason_codes: [],
  blocking_reasons: [],
  telemetry_summary: {},
  risk_assessment: { overall: 'LOW' },
  warnings: [],
  lookback_summary: [],
  chart: { metric: 'cpu_utilization', unit: 'Percent', points: [], target_capacity_line: 100 },
  memory_chart: null,
  iops_chart: null,
  utilization_bars: [],
  coverage_caveats: {},
  policy: {
    network_medium_ratio: 0.6,
    network_high_ratio: 0.8,
    ebs_medium_ratio: 0.6,
    ebs_high_ratio: 0.8,
    allow_unknown_instance_store_usage: false,
  },
  ...overrides,
});

// Layout's Header consumes the optimization-profile context, which the shared
// test wrapper does not provide.
const renderDetailPage = () =>
  render(
    <OptimizationProfileProvider>
      <RightsizerDetailPage />
    </OptimizationProfileProvider>
  );

describe('RightsizerDetailPage apply flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('maps the default balanced tier into the rightsize action payload', async () => {
    mocks.getDetail.mockResolvedValue(buildDetail());
    mocks.applyRecommendation.mockResolvedValue({
      check_id: 'ec2_rightsizer',
      action: 'rightsize',
      status: 'submitted',
      message: 'Instance rightsize to m5.large submitted',
    });

    renderDetailPage();
    fireEvent.click(await screen.findByRole('button', { name: /Apply/i }));

    await waitFor(() =>
      expect(mocks.applyRecommendation).toHaveBeenCalledWith(7, {
        target_instance_type: 'm5.large',
        recommendation_option: 'balanced',
        account_id: '123456789012',
        region: 'us-east-1',
        resource_id: 'i-PLACEHOLDER-INSTANCE',
      })
    );
    expect(await screen.findByText('Instance rightsize to m5.large submitted')).toBeInTheDocument();
  });

  it('maps the selected tier, not the default, after switching options', async () => {
    mocks.getDetail.mockResolvedValue(buildDetail());
    mocks.applyRecommendation.mockResolvedValue({ status: 'submitted', message: 'ok' });

    renderDetailPage();
    fireEvent.click(await screen.findByRole('button', { name: /Aggressive/i }));
    fireEvent.click(screen.getByRole('button', { name: /Apply/i }));

    await waitFor(() => expect(mocks.applyRecommendation).toHaveBeenCalledTimes(1));
    const [inventoryId, payload] = mocks.applyRecommendation.mock.calls[0];
    expect(inventoryId).toBe(7);
    expect(payload).toMatchObject({
      target_instance_type: 'c5.large',
      recommendation_option: 'aggressive',
    });
  });

  it('surfaces the backend error detail when the action fails', async () => {
    mocks.getDetail.mockResolvedValue(buildDetail());
    mocks.applyRecommendation.mockRejectedValue({
      response: { data: { detail: "Action 'rightsize' is already running for resource i-PLACEHOLDER-INSTANCE." } },
    });

    renderDetailPage();
    fireEvent.click(await screen.findByRole('button', { name: /Apply/i }));

    expect(
      await screen.findByText(/already running for resource i-PLACEHOLDER-INSTANCE/i)
    ).toBeInTheDocument();
    expect(mocks.applyRecommendation).toHaveBeenCalledTimes(1);
  });

  it('disables Apply when no recommendation target exists', async () => {
    mocks.getDetail.mockResolvedValue(
      buildDetail({
        tiers: { default: null, conservative: null, balanced: null, aggressive: null },
        recommendation: null,
        recommendations: [],
      })
    );

    renderDetailPage();
    const apply = await screen.findByRole('button', { name: /Apply/i });
    expect(apply).toBeDisabled();
    fireEvent.click(apply);
    expect(mocks.applyRecommendation).not.toHaveBeenCalled();
  });
});
