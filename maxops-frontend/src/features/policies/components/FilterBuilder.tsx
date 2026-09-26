import React, { useMemo, useState } from 'react';
import { Plus, X, AlertCircle } from 'lucide-react';
import { useQuery } from 'react-query';
import { policiesApi } from '@/services/policies';
import type { FilterCondition, FilterDefinition } from '@/types/api';
import { FilterConditionEditor } from './FilterConditionEditor';

interface FilterBuilderProps {
  resourceType: string;
  filters: FilterCondition[];
  onChange: (filters: FilterCondition[]) => void;
  errors?: string[];
}

const COMMON_FILTER_DEFINITIONS: Record<string, FilterDefinition> = {
  tags: {
    type: 'tags',
    label: 'Tags',
    description: 'Filter resources by tag key/value.',
    operators: ['equals', 'contains', 'exists', 'not_exists'],
    value_type: 'object',
    ui_component: 'TagInput',
  },
  state: {
    type: 'state',
    label: 'State',
    description: 'Filter resources by runtime state.',
    operators: ['equals', 'in'],
    value_type: 'string',
    ui_component: 'TextInput',
  },
  region: {
    type: 'region',
    label: 'Region',
    description: 'Filter resources by AWS region.',
    operators: ['equals', 'in'],
    value_type: 'string',
    ui_component: 'TextInput',
  },
  name: {
    type: 'name',
    label: 'Name',
    description: 'Filter resources by resource name.',
    operators: ['equals', 'contains', 'starts_with', 'ends_with'],
    value_type: 'string',
    ui_component: 'TextInput',
  },
};

const RESOURCE_FILTERS: Record<string, Record<string, FilterDefinition>> = {
  ec2: {
    idle: {
      type: 'idle',
      label: 'Idle',
      description: 'Find resources idle for more than N days.',
      operators: ['greater_than'],
      value_type: 'number',
      ui_component: 'NumberInput',
      min: 1,
      default: 7,
      unit: 'days',
      example: 7,
    },
  },
  rds: {
    idle: {
      type: 'idle',
      label: 'Idle',
      description: 'Find resources idle for more than N days.',
      operators: ['greater_than'],
      value_type: 'number',
      ui_component: 'NumberInput',
      min: 1,
      default: 7,
      unit: 'days',
      example: 7,
    },
  },
  ebs: {
    unattached: {
      type: 'unattached',
      label: 'Unattached',
      description: 'Match unattached volumes.',
      operators: ['equals'],
      value_type: 'boolean',
      ui_component: 'Checkbox',
      default: true,
    },
  },
  snapshot: {
    age: {
      type: 'age',
      label: 'Age',
      description: 'Find snapshots older than N days.',
      operators: ['greater_than'],
      value_type: 'number',
      ui_component: 'NumberInput',
      min: 1,
      default: 30,
      unit: 'days',
      example: 30,
    },
  },
};

const getFallbackFilters = (resourceType: string): Record<string, FilterDefinition> => ({
  ...COMMON_FILTER_DEFINITIONS,
  ...(RESOURCE_FILTERS[resourceType] || {}),
});

export const FilterBuilder: React.FC<FilterBuilderProps> = ({
  resourceType,
  filters,
  onChange,
  errors = [],
}) => {
  const [isAddingFilter, setIsAddingFilter] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);

  const { data: apiFilters, isLoading } = useQuery(
    ['filters', resourceType],
    () => policiesApi.getFilters(resourceType),
    {
      enabled: !!resourceType,
      retry: false,
    }
  );

  const availableFilters = useMemo(() => {
    if (apiFilters && Object.keys(apiFilters).length > 0) {
      return apiFilters;
    }
    return getFallbackFilters(resourceType);
  }, [apiFilters, resourceType]);

  const handleAddFilter = (filter: FilterCondition) => {
    onChange([...filters, filter]);
    setIsAddingFilter(false);
  };

  const handleUpdateFilter = (index: number, filter: FilterCondition) => {
    const updated = [...filters];
    updated[index] = filter;
    onChange(updated);
    setEditingIndex(null);
  };

  const handleRemoveFilter = (index: number) => {
    const updated = filters.filter((_, i) => i !== index);
    onChange(updated);
  };

  const handleEditFilter = (index: number) => {
    setEditingIndex(index);
  };

  if (isLoading) {
    return (
      <div className="text-center py-8">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 dark:border-primary-400 mx-auto"></div>
        <p className="mt-4 text-gray-600 dark:text-gray-400">Loading filters...</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <label className="label mb-0">Filter Conditions</label>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Add conditions to filter resources for this policy
          </p>
        </div>
        {!isAddingFilter && editingIndex === null && (
          <button
            onClick={() => setIsAddingFilter(true)}
            className="flex items-center space-x-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
          >
            <Plus size={18} />
            <span>Add Filter</span>
          </button>
        )}
      </div>

      {/* Errors */}
      {errors.length > 0 && (
        <div className="bg-danger-50 dark:bg-danger-900/20 border border-danger-200 dark:border-danger-800 rounded-lg p-4">
          <div className="flex items-start space-x-2">
            <AlertCircle className="h-5 w-5 text-danger-600 dark:text-danger-400 mt-0.5" />
            <div>
              <h4 className="font-semibold text-danger-800 dark:text-danger-200 mb-1">Validation Errors</h4>
              <ul className="list-disc list-inside text-sm text-danger-700 dark:text-danger-300 space-y-1">
                {errors.map((error, idx) => (
                  <li key={idx}>{error}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Filter List */}
      <div className="space-y-2">
        {filters.map((filter, index) => (
          <div
            key={index}
            className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700"
          >
            {editingIndex === index ? (
              <FilterConditionEditor
                resourceType={resourceType}
                availableFilters={availableFilters}
                filter={filter}
                onSave={(updatedFilter) => handleUpdateFilter(index, updatedFilter)}
                onCancel={() => setEditingIndex(null)}
              />
            ) : (
              <>
                <div className="flex-1">
                  <div className="flex items-center space-x-3">
                    <span className="px-2 py-1 bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 rounded text-sm font-medium">
                      {availableFilters[filter.type]?.label || filter.type}
                    </span>
                    <span className="text-gray-600 dark:text-gray-400">{filter.operator}</span>
                    <span className="font-medium text-gray-900 dark:text-white">
                      {typeof filter.value === 'object' && filter.value !== null
                        ? JSON.stringify(filter.value)
                        : String(filter.value)}
                    </span>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => handleEditFilter(index)}
                    className="px-3 py-1 text-sm text-primary-600 dark:text-primary-400 hover:bg-primary-50 dark:hover:bg-primary-900/20 rounded"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleRemoveFilter(index)}
                    className="p-1 text-danger-600 dark:text-danger-400 hover:bg-danger-50 dark:hover:bg-danger-900/20 rounded"
                  >
                    <X size={18} />
                  </button>
                </div>
              </>
            )}
          </div>
        ))}

        {filters.length === 0 && !isAddingFilter && (
          <div className="text-center py-8 border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-lg">
            <p className="text-gray-600 dark:text-gray-400 mb-4">No filters added yet</p>
            <button
              onClick={() => setIsAddingFilter(true)}
              className="flex items-center space-x-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors mx-auto"
            >
              <Plus size={18} />
              <span>Add Your First Filter</span>
            </button>
          </div>
        )}
      </div>

      {/* Add Filter Editor */}
      {isAddingFilter && (
        <FilterConditionEditor
          resourceType={resourceType}
          availableFilters={availableFilters}
          onSave={handleAddFilter}
          onCancel={() => setIsAddingFilter(false)}
        />
      )}
    </div>
  );
};

