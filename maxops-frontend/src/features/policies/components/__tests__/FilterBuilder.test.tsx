import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/utils/test-utils';
import { FilterBuilder } from '../FilterBuilder';

// Mock the API
vi.mock('@/services/policies', () => ({
  policiesApi: {
    getFilters: vi.fn().mockResolvedValue({
      idle: {
        type: 'idle',
        label: 'Idle Duration',
        operators: ['greater_than'],
        value_type: 'number',
      },
      tags: {
        type: 'tags',
        label: 'Tags',
        operators: ['equals'],
        value_type: 'key_value',
      },
    }),
  },
}));

describe('FilterBuilder', () => {
  const defaultProps = {
    resourceType: 'ec2',
    filters: [],
    onChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders filter builder', async () => {
    render(<FilterBuilder {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText('Filter Conditions')).toBeInTheDocument();
    });
  });

  it('shows empty state when no filters', async () => {
    render(<FilterBuilder {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText('No filters added yet')).toBeInTheDocument();
    });
  });

  it('displays existing filters', async () => {
    const filters = [
      {
        type: 'idle',
        operator: 'greater_than',
        value: 7,
      },
    ];
    render(<FilterBuilder {...defaultProps} filters={filters} />);
    await waitFor(() => {
      expect(screen.getByText('Idle Duration')).toBeInTheDocument();
    });
  });

  it('shows add filter button', async () => {
    render(<FilterBuilder {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText('Add Your First Filter')).toBeInTheDocument();
    });
  });

  it('displays validation errors', async () => {
    const errors = ['Filter validation error'];
    render(<FilterBuilder {...defaultProps} errors={errors} />);
    await waitFor(() => {
      expect(screen.getByText('Validation Errors')).toBeInTheDocument();
      expect(screen.getByText('Filter validation error')).toBeInTheDocument();
    });
  });
});

