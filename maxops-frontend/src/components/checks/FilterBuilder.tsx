import React, { useState } from 'react';
import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { Plus, X, Trash2, Save } from 'lucide-react';
import { checksApi } from '@/services/checks';
import { useMutation, useQuery, useQueryClient } from 'react-query';

interface FilterCondition {
  type: string;
  operator: string;
  key?: string;
  value: string | number;
}

interface FilterBuilderProps {
  checkId: string;
  resourceType: string;
  onClose?: () => void;
}

// Common filter types available for all resources
const COMMON_FILTER_TYPES = [
  { value: 'tags', label: 'Tag', operators: ['equals', 'contains', 'not_equals', 'starts_with', 'ends_with'] },
  { value: 'region', label: 'Region', operators: ['equals', 'not_equals', 'contains'] },
  { value: 'name', label: 'Name', operators: ['equals', 'contains', 'starts_with', 'ends_with', 'not_equals'] },
  { value: 'resource_id', label: 'Resource ID', operators: ['equals', 'contains', 'starts_with', 'ends_with'] },
];

// Resource-specific filter types
const RESOURCE_SPECIFIC_FILTERS: Record<string, Array<{ value: string; label: string; operators: string[] }>> = {
  ec2: [
    { value: 'state', label: 'State', operators: ['equals', 'not_equals'] },
    { value: 'instance_type', label: 'Instance Type', operators: ['equals', 'contains', 'not_equals'] },
  ],
  rds: [
    { value: 'state', label: 'State', operators: ['equals', 'not_equals'] },
    { value: 'engine', label: 'Engine', operators: ['equals', 'contains', 'not_equals'] },
  ],
  ebs: [
    { value: 'state', label: 'State', operators: ['equals', 'not_equals'] },
    { value: 'volume_type', label: 'Volume Type', operators: ['equals', 'not_equals'] },
  ],
  snapshot: [
    { value: 'state', label: 'State', operators: ['equals', 'not_equals'] },
  ],
  dynamodb: [
    { value: 'billing_mode', label: 'Billing Mode', operators: ['equals', 'not_equals'] },
  ],
};

const OPERATOR_LABELS: Record<string, string> = {
  equals: 'Equals',
  not_equals: 'Not Equals',
  contains: 'Contains',
  starts_with: 'Starts With',
  ends_with: 'Ends With',
  greater_than: 'Greater Than',
  less_than: 'Less Than',
};

export const FilterBuilder: React.FC<FilterBuilderProps> = ({
  checkId,
  resourceType,
  onClose,
}) => {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<FilterCondition[]>([]);

  // Load existing filters
  const { isLoading } = useQuery(
    ['check-filters', checkId],
    () => checksApi.getCheckFilters(checkId),
    {
      enabled: !!checkId,
      onSuccess: (data) => {
        setFilters(data.filters || []);
      },
    }
  );

  // Save filters mutation
  const saveMutation = useMutation(
    (filtersToSave: FilterCondition[]) => checksApi.saveCheckFilters(checkId, filtersToSave),
    {
      onSuccess: () => {
        queryClient.invalidateQueries(['check-filters', checkId]);
        if (onClose) {
          onClose();
        }
      },
    }
  );

  // Delete filters mutation
  const deleteMutation = useMutation(
    () => checksApi.deleteCheckFilters(checkId),
    {
      onSuccess: () => {
        setFilters([]);
        queryClient.invalidateQueries(['check-filters', checkId]);
      },
    }
  );

  // Get available filter types for this resource type
  const availableFilterTypes = [
    ...COMMON_FILTER_TYPES,
    ...(RESOURCE_SPECIFIC_FILTERS[resourceType] || []),
  ];

  const addFilter = () => {
    setFilters([
      ...filters,
      {
        type: availableFilterTypes[0]?.value || 'tags',
        operator: 'equals',
        value: '',
      },
    ]);
  };

  const removeFilter = (index: number) => {
    setFilters(filters.filter((_, i) => i !== index));
  };

  const updateFilter = (index: number, field: keyof FilterCondition, value: any) => {
    const updated = [...filters];
    updated[index] = { ...updated[index], [field]: value };
    
    // Reset operator if filter type changes
    if (field === 'type') {
      const filterType = availableFilterTypes.find(ft => ft.value === value);
      if (filterType && filterType.operators.length > 0) {
        updated[index].operator = filterType.operators[0];
      }
    }
    
    // Clear key if not a tag filter
    if (field === 'type' && value !== 'tags') {
      delete updated[index].key;
    }
    
    setFilters(updated);
  };

  const handleSave = () => {
    // Validate filters
    const validFilters = filters.filter(f => {
      if (f.type === 'tags' && !f.key) return false;
      if (!f.value || (typeof f.value === 'string' && f.value.trim() === '')) return false;
      return true;
    });
    
    saveMutation.mutate(validFilters);
  };

  const handleDelete = () => {
    if (window.confirm('Are you sure you want to delete all filters for this check?')) {
      deleteMutation.mutate();
    }
  };

  if (isLoading) {
    return (
      <div className="text-center py-8 text-gray-500 dark:text-gray-400">
        Loading filters...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Check Filters</h3>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Configure filters to limit which resources this check processes
          </p>
        </div>
        <div className="flex items-center space-x-2">
          {filters.length > 0 && (
            <Button
              variant="secondary"
              size="sm"
              onClick={handleDelete}
              disabled={deleteMutation.isLoading}
            >
              <Trash2 size={16} className="mr-2" />
              Clear All
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            onClick={addFilter}
          >
            <Plus size={16} className="mr-2" />
            Add Filter
          </Button>
        </div>
      </div>

      {filters.length === 0 ? (
        <Card>
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p className="mb-4">No filters configured</p>
            <p className="text-sm mb-4">
              Add filters to limit which resources this check will process. 
              Only resources matching all filters will be included.
            </p>
            <Button variant="primary" size="sm" onClick={addFilter}>
              <Plus size={16} className="mr-2" />
              Add Your First Filter
            </Button>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {filters.map((filter, index) => (
            <Card key={index} className="p-4">
              <div className="flex items-start space-x-3">
                <div className="flex-1 grid grid-cols-1 md:grid-cols-4 gap-3">
                  {/* Filter Type */}
                  <div>
                    <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Filter Type
                    </label>
                    <select
                      value={filter.type}
                      onChange={(e) => updateFilter(index, 'type', e.target.value)}
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                    >
                      {availableFilterTypes.map((ft) => (
                        <option key={ft.value} value={ft.value}>
                          {ft.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Key (for tag filters) */}
                  {filter.type === 'tags' && (
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Tag Key
                      </label>
                      <input
                        type="text"
                        value={filter.key || ''}
                        onChange={(e) => updateFilter(index, 'key', e.target.value)}
                        placeholder="e.g., Environment"
                        className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                      />
                    </div>
                  )}

                  {/* Operator */}
                  <div>
                    <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Operator
                    </label>
                    <select
                      value={filter.operator}
                      onChange={(e) => updateFilter(index, 'operator', e.target.value)}
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                    >
                      {availableFilterTypes
                        .find(ft => ft.value === filter.type)
                        ?.operators.map((op) => (
                          <option key={op} value={op}>
                            {OPERATOR_LABELS[op] || op}
                          </option>
                        ))}
                    </select>
                  </div>

                  {/* Value */}
                  <div>
                    <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Value
                    </label>
                    <input
                      type="text"
                      value={filter.value}
                      onChange={(e) => updateFilter(index, 'value', e.target.value)}
                      placeholder="Enter value"
                      className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                    />
                  </div>
                </div>

                {/* Remove Button */}
                <button
                  onClick={() => removeFilter(index)}
                  className="mt-6 p-2 text-gray-400 hover:text-danger-600 dark:hover:text-danger-400 transition-colors"
                  title="Remove filter"
                >
                  <X size={18} />
                </button>
              </div>
            </Card>
          ))}

          {/* Save Button */}
          <div className="flex justify-end pt-4 border-t border-gray-200 dark:border-gray-700">
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={saveMutation.isLoading}
            >
              <Save size={16} className="mr-2" />
              {saveMutation.isLoading ? 'Saving...' : 'Save Filters'}
            </Button>
          </div>
        </div>
      )}

      {filters.length > 0 && (
        <div className="p-4 bg-primary-50 dark:bg-primary-900/20 rounded-lg border border-primary-200 dark:border-primary-800">
          <p className="text-sm text-primary-800 dark:text-primary-300">
            <strong>Note:</strong> All filters are combined with AND logic. 
            Resources must match all configured filters to be included in the check results.
          </p>
        </div>
      )}
    </div>
  );
};
