import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from 'react-query';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@/test/utils/test-utils';
import { ScanCredentialsCard } from '../ScanCredentialsCard';

const mocks = vi.hoisted(() => ({
  getScanCredentials: vi.fn(),
  updateScanCredentials: vi.fn(),
}));

vi.mock('@/services/settings', () => ({
  settingsApi: {
    getScanCredentials: mocks.getScanCredentials,
    updateScanCredentials: mocks.updateScanCredentials,
  },
}));

const credentials = (overrides: Record<string, any> = {}) => ({
  profile: 'MaxOpsReadOnlyRole',
  using_default_chain: false,
  resolved_account_id: '123456789012',
  settings_account_id: '123456789012',
  account_mismatch: false,
  available_profiles: [
    {
      profile_name: 'MaxOpsReadOnlyRole',
      display_name: 'MaxOpsReadOnlyRole',
      account_id: '123456789012',
      arn: null,
      is_default: false,
      error: null,
    },
    {
      profile_name: 'prod',
      display_name: 'prod',
      account_id: '210987654321',
      arn: null,
      is_default: false,
      error: null,
    },
  ],
  ...overrides,
});

describe('ScanCredentialsCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getScanCredentials.mockResolvedValue(credentials());
    mocks.updateScanCredentials.mockImplementation((profile: string | null) =>
      Promise.resolve(credentials({ profile, using_default_chain: profile === null })),
    );
  });

  it('shows the profile scans currently run as', async () => {
    render(<ScanCredentialsCard />);
    await waitFor(() => {
      expect(screen.getByLabelText('AWS profile')).toHaveValue('MaxOpsReadOnlyRole');
    });
  });

  it('changes the profile when another is picked', async () => {
    render(<ScanCredentialsCard />);
    await screen.findByLabelText('AWS profile');

    await userEvent.selectOptions(screen.getByLabelText('AWS profile'), 'prod');

    await waitFor(() => {
      expect(mocks.updateScanCredentials).toHaveBeenCalledWith('prod', undefined);
    });
  });

  it('resets to the default credential chain', async () => {
    render(<ScanCredentialsCard />);
    await screen.findByLabelText('AWS profile');

    await userEvent.selectOptions(
      screen.getByLabelText('AWS profile'),
      'Reset to the default credential chain',
    );

    await waitFor(() => {
      expect(mocks.updateScanCredentials).toHaveBeenCalledWith(null, undefined);
    });
  });

  it('explains the fallback when no profile is set', async () => {
    mocks.getScanCredentials.mockResolvedValue(
      credentials({ profile: null, using_default_chain: true }),
    );
    render(<ScanCredentialsCard />);
    expect(await screen.findByText(/default credential chain/i)).toBeInTheDocument();
  });

  it('warns when the profile resolves to a different account', async () => {
    mocks.getScanCredentials.mockResolvedValue(
      credentials({
        profile: 'prod',
        resolved_account_id: '210987654321',
        settings_account_id: '123456789012',
        account_mismatch: true,
      }),
    );
    render(<ScanCredentialsCard />);
    const warning = await screen.findByText(/but Global\s+Settings records/i);
    expect(warning).toHaveTextContent('210987654321');
    expect(warning).toHaveTextContent('123456789012');
  });

  it('invalidates cached AWS-derived views so they refetch under the new profile', async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    render(<ScanCredentialsCard />);
    await screen.findByLabelText('AWS profile');

    await userEvent.selectOptions(screen.getByLabelText('AWS profile'), 'prod');

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith();
    });
    invalidate.mockRestore();
  });

  it('reports a failed change instead of silently keeping the old value', async () => {
    mocks.updateScanCredentials.mockRejectedValue(new Error('400'));
    render(<ScanCredentialsCard />);
    await screen.findByLabelText('AWS profile');

    await userEvent.selectOptions(screen.getByLabelText('AWS profile'), 'prod');

    expect(await screen.findByText(/Could not change the scan profile/i)).toBeInTheDocument();
  });

  const accountConflict = (runningScans = 1) => ({
    response: {
      status: 409,
      data: {
        detail: {
          code: 'account_change_requires_confirmation',
          current_account_id: '123456789012',
          new_account_id: '210987654321',
          running_scans: runningScans,
          message: 'Switching stops any running scan and clears every finding.',
        },
      },
    },
  });

  const pickProd = async () => {
    render(<ScanCredentialsCard />);
    await screen.findByLabelText('AWS profile');
    await userEvent.selectOptions(screen.getByLabelText('AWS profile'), 'prod');
  };

  it('asks before switching to a different account', async () => {
    mocks.updateScanCredentials.mockRejectedValue(accountConflict());
    await pickProd();

    expect(await screen.findByText('Change AWS account?')).toBeInTheDocument();
    expect(screen.getByText(/database will be emptied/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument();
  });

  it('says how many running scans will be stopped', async () => {
    mocks.updateScanCredentials.mockRejectedValue(accountConflict(2));
    await pickProd();

    expect(await screen.findByText(/2 running scans will be stopped/i)).toBeInTheDocument();
  });

  it('does not offer to stop scans when none are running', async () => {
    mocks.updateScanCredentials.mockRejectedValue(accountConflict(0));
    await pickProd();

    await screen.findByText('Change AWS account?');
    expect(screen.queryByText(/will be stopped/i)).not.toBeInTheDocument();
  });

  it('changes nothing when the account switch is cancelled', async () => {
    mocks.updateScanCredentials.mockRejectedValue(accountConflict());
    await pickProd();
    await screen.findByText('Change AWS account?');

    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByText('Change AWS account?')).not.toBeInTheDocument();
    });
    expect(mocks.updateScanCredentials).toHaveBeenCalledTimes(1);
    expect(mocks.updateScanCredentials).not.toHaveBeenCalledWith('prod', true);
  });

  it('resends with confirmation once the reset is accepted', async () => {
    mocks.updateScanCredentials.mockRejectedValueOnce(accountConflict());
    mocks.updateScanCredentials.mockResolvedValueOnce(
      credentials({ profile: 'prod', account_changed: true, stopped_scans: 1 }),
    );
    await pickProd();
    await screen.findByText('Change AWS account?');

    await userEvent.click(screen.getByRole('button', { name: /reset and switch account/i }));

    await waitFor(() => {
      expect(mocks.updateScanCredentials).toHaveBeenLastCalledWith('prod', true);
    });
  });

  it('does not show a generic error while the confirmation is open', async () => {
    mocks.updateScanCredentials.mockRejectedValue(accountConflict());
    await pickProd();
    await screen.findByText('Change AWS account?');

    expect(screen.queryByText(/Could not change the scan profile/i)).not.toBeInTheDocument();
  });
});
