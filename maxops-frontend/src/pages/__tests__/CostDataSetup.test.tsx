import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/utils/test-utils';
import { OptimizationProfileProvider } from '@/contexts/OptimizationProfileContext';
import { CostDataSetupPage } from '../CostDataSetup';

const mocks = vi.hoisted(() => ({
  getSetupStatus: vi.fn(),
  getPreflight: vi.fn(),
  getRefreshEstimate: vi.fn(),
  startExport: vi.fn(),
  startRefresh: vi.fn(),
  setPricingEnabled: vi.fn(),
  getSetupCredentials: vi.fn(),
  updateSetupCredentials: vi.fn(),
}));

vi.mock('@/services/costData', () => ({
  costDataApi: {
    getSetupStatus: mocks.getSetupStatus,
    getPreflight: mocks.getPreflight,
    getRefreshEstimate: mocks.getRefreshEstimate,
    startExport: mocks.startExport,
    startRefresh: mocks.startRefresh,
    setPricingEnabled: mocks.setPricingEnabled,
  },
}));

vi.mock('@/services/settings', () => ({
  settingsApi: {
    getSetupCredentials: mocks.getSetupCredentials,
    updateSetupCredentials: mocks.updateSetupCredentials,
  },
}));

const credentials = (overrides: Record<string, any> = {}) => ({
  profile: null,
  scan_profile: 'MaxOpsReadOnlyRole',
  using_scan_profile: true,
  available_profiles: [
    {
      profile_name: 'maxops',
      display_name: 'maxops',
      account_id: '123456789012',
      arn: 'arn:aws:iam::123456789012:user/maxopsuser',
      is_default: false,
      error: null,
    },
  ],
  ...overrides,
});

const status = (overrides: Record<string, any> = {}) => ({
  region: 'us-east-1',
  account_id: '123456789012',
  bucket: 'maxops-cur-report-123456789012',
  aws_available: true,
  aws_error: null,
  steps: [
    { id: 'export', label: 'Cost and Usage Report export', state: 'done', detail: 'Export exists.' },
    { id: 'delivery', label: 'First bill delivered by AWS', state: 'done', detail: 'Delivered.' },
    { id: 'cache', label: 'Local cost cache', state: 'action_required', detail: 'Nothing cached yet.' },
    {
      id: 'pricing',
      label: 'Pricing from actual cost',
      state: 'action_required',
      detail: 'Using list prices.',
      enabled: false,
      billing_month: null,
      resource_count: 0,
    },
  ],
  diagnostics: [
    { id: 'athena_table', label: 'Athena table', state: 'done', detail: 'Queryable.' },
  ],
  jobs: [],
  running: { export: null, refresh: null },
  ...overrides,
});

const renderPage = () =>
  render(
    <OptimizationProfileProvider>
      <CostDataSetupPage />
    </OptimizationProfileProvider>
  );

describe('CostDataSetupPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSetupCredentials.mockResolvedValue(credentials());
  });

  it('warns when setup would run as the read-only scan role', async () => {
    mocks.getSetupStatus.mockResolvedValue(status());

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No setup profile chosen/)).toBeInTheDocument();
    });
    expect(screen.getByText(/MaxOpsReadOnlyRole/)).toBeInTheDocument();
  });

  it('drops the warning once a setup profile is chosen', async () => {
    mocks.getSetupStatus.mockResolvedValue(status());
    mocks.getSetupCredentials.mockResolvedValue(
      credentials({ profile: 'maxops', using_scan_profile: false })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Setup credentials/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/No setup profile chosen/)).not.toBeInTheDocument();
  });

  it('renders the four setup steps', async () => {
    mocks.getSetupStatus.mockResolvedValue(status());

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Local cost cache/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Cost and Usage Report export/)).toBeInTheDocument();
    expect(screen.getByText(/Pricing from actual cost/)).toBeInTheDocument();
    expect(screen.getByText('123456789012')).toBeInTheDocument();
  });

  it('shows progress while a refresh job is running', async () => {
    mocks.getSetupStatus.mockResolvedValue(
      status({
        running: {
          export: null,
          refresh: {
            id: 1,
            job_type: 'refresh',
            status: 'running',
            progress: { message: 'Summarising resource_monthly for 2026-08…', current: 2, total: 13 },
          },
        },
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Summarising resource_monthly/)).toBeInTheDocument();
    });
    expect(screen.getByText('2/13')).toBeInTheDocument();
  });

  it('tells you how to fix a permission-blocked step', async () => {
    mocks.getSetupStatus.mockResolvedValue(
      status({
        steps: [
          {
            id: 'export',
            label: 'Cost and Usage Report export',
            state: 'unknown',
            detail: "The credentials in use aren't allowed to call bcm-data-exports:ListExports.",
            remedy:
              'Setup is using the read-only scan role, which deliberately cannot do this. ' +
              'Choose an AWS profile under Setup credentials above.',
            denied_action: 'bcm-data-exports:ListExports',
          },
        ],
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Choose an AWS profile under Setup credentials/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Needs: bcm-data-exports:ListExports/)).toBeInTheDocument();
  });

  it('offers a repair action when the export exists but its table is stale', async () => {
    mocks.getSetupStatus.mockResolvedValue(
      status({
        steps: [
          {
            id: 'export',
            label: 'Cost and Usage Report export',
            state: 'error',
            detail: 'Export exists. Table points at the wrong bucket.',
            remedy: "Re-run 'Create export' to point it at the current one.",
            repairable: true,
          },
        ],
      })
    );

    renderPage();

    // A green "done" export would render no button at all, leaving the user
    // with a diagnosis and no way to act on it.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Repair export/ })).toBeInTheDocument();
    });
  });

  it('shows a failure for each job type, not just the newest', async () => {
    // The export failure was the real blocker but sat behind a later refresh
    // failure, so the page looked like the repair had worked.
    mocks.getSetupStatus.mockResolvedValue(
      status({
        jobs: [
          { id: 10, job_type: 'refresh', status: 'failed', progress: {}, error: 'ATHENA BOOM' },
          { id: 9, job_type: 'export', status: 'failed', progress: {}, error: 'CUR PUT DENIED' },
        ],
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/CUR PUT DENIED/)).toBeInTheDocument();
    });
    expect(screen.getByText(/ATHENA BOOM/)).toBeInTheDocument();
  });

  it('stops showing a failure once a later run of that type succeeded', async () => {
    mocks.getSetupStatus.mockResolvedValue(
      status({
        jobs: [
          { id: 11, job_type: 'export', status: 'completed', progress: {}, error: null },
          { id: 9, job_type: 'export', status: 'failed', progress: {}, error: 'OLD FAILURE' },
        ],
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Local cost cache/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/OLD FAILURE/)).not.toBeInTheDocument();
  });

  it('says when a failure happened, not just what it was', async () => {
    const twoMinutesAgo = new Date(Date.now() - 2 * 60 * 1000).toISOString();
    mocks.getSetupStatus.mockResolvedValue(
      status({
        jobs: [
          {
            id: 13,
            job_type: 'export',
            status: 'failed',
            progress: {},
            error: 'Cannot create duplicate export name.',
            completed_at: twoMinutesAgo,
          },
        ],
      })
    );

    renderPage();

    // Without a time, an old failure is indistinguishable from one the user
    // just caused, which is what made this unreadable.
    await waitFor(() => {
      expect(screen.getByText(/2 minutes ago/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Attempt #13/)).toBeInTheDocument();
  });

  it('surfaces a failed job so the AWS error is visible', async () => {
    mocks.getSetupStatus.mockResolvedValue(
      status({
        jobs: [
          {
            id: 4,
            job_type: 'refresh',
            status: 'failed',
            progress: {},
            error: 'AccessDenied on athena:StartQueryExecution',
          },
        ],
      })
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/AccessDenied on athena/)).toBeInTheDocument();
    });
  });
});
