import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/utils/test-utils';
import { PolicyEditor } from '../PolicyEditor';

// Mock the API
vi.mock('@/services/policies', () => ({
  policiesApi: {
    listTemplates: vi.fn().mockResolvedValue([]),
    getFilters: vi.fn().mockResolvedValue({
      idle: {
        type: 'idle',
        label: 'Idle Duration',
        operators: ['greater_than'],
        value_type: 'number',
      },
    }),
    create: vi.fn().mockResolvedValue({ id: 1 }),
    update: vi.fn().mockResolvedValue({ id: 1 }),
    validate: vi.fn().mockResolvedValue({ valid: true, errors: [], warnings: [] }),
  },
}));

// Mock Button component
vi.mock('@/components/common/Button', () => ({
  Button: ({ children, onClick, disabled, isLoading, variant, size }: any) => (
    <button
      onClick={onClick}
      disabled={disabled || isLoading}
      data-variant={variant}
      data-size={size}
    >
      {isLoading ? 'Loading...' : children}
    </button>
  ),
}));

// Mock TemplateSelector
vi.mock('../TemplateSelector', () => ({
  TemplateSelector: () => <div data-testid="template-selector">Template Selector</div>,
}));

describe('PolicyEditor', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders policy editor for new policy', async () => {
    render(<PolicyEditor onClose={mockOnClose} />);
    await waitFor(() => {
      expect(screen.getByText('Policy Name *')).toBeInTheDocument();
      expect(screen.getByText('Resource Type *')).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/Idle EC2 Instances/i)).toBeInTheDocument();
    });
  });

  it('renders policy editor for editing', async () => {
    const policy = {
      id: 1,
      policy_code: 'POLA1B',
      name: 'Test Policy',
      description: 'Test description',
      resource_type: 'ec2',
      status: 'active' as const,
      filters_json: [
        { type: 'idle', operator: 'greater_than', value: 7 }
      ],
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    render(<PolicyEditor policy={policy} onClose={mockOnClose} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('Test Policy')).toBeInTheDocument();
      expect(screen.getByDisplayValue('Test description')).toBeInTheDocument();
    });
  });

  it('shows policy code for existing policy', async () => {
    const policy = {
      id: 1,
      policy_code: 'POLA1B',
      name: 'Test Policy',
      resource_type: 'ec2',
      status: 'active' as const,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    render(<PolicyEditor policy={policy} onClose={mockOnClose} />);
    await waitFor(() => {
      expect(screen.getByText('POLA1B')).toBeInTheDocument();
    });
  });

  it('validates required fields', async () => {
    render(<PolicyEditor onClose={mockOnClose} />);
    await waitFor(() => {
      // The submit button is labelled "Create Check": policies were renamed
      // to checks, and this assertion was never updated.
      const submitButton = screen.getByText('Create Check');
      expect(submitButton).toBeDisabled();
    });
  });
});

