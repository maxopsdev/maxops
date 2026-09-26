import React, { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from 'react-query';
import { Button } from '@/components/common/Button';
import { policiesApi } from '@/services/policies';
import type { Policy, PolicyTemplate, FilterCondition } from '@/types/api';
import { TemplateSelector } from './TemplateSelector';
import { FilterBuilder } from './FilterBuilder';

interface PolicyEditorProps {
  policy?: Policy | null;
  onClose: () => void;
}

export const PolicyEditor: React.FC<PolicyEditorProps> = ({ policy, onClose }) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [filters, setFilters] = useState<FilterCondition[]>([]);
  const [resourceType, setResourceType] = useState('ec2');
  const [status, setStatus] = useState<'active' | 'inactive'>('active');
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [validationWarnings, setValidationWarnings] = useState<string[]>([]);
  const queryClient = useQueryClient();

  const { data: templates = [] } = useQuery('policy-templates', () =>
    policiesApi.listTemplates()
  );

  useEffect(() => {
    if (policy) {
      setName(policy.name);
      setDescription(policy.description || '');
      // Use filters_json if available, otherwise fall back to empty array
      setFilters(policy.filters_json || []);
      setResourceType(policy.resource_type);
      setStatus(policy.status);
    }
  }, [policy]);

  const validateMutation = useMutation(
    ({ filters, resourceType }: { filters: FilterCondition[]; resourceType: string }) =>
      policiesApi.validate(undefined, filters, resourceType),
    {
      onSuccess: (data) => {
        setValidationErrors(data.errors);
        setValidationWarnings(data.warnings);
      },
    }
  );

  const createMutation = useMutation(
    (data: any) => policiesApi.create(data),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('policies');
        onClose();
      },
    }
  );

  const updateMutation = useMutation(
    (data: any) => policiesApi.update(policy!.id, data),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('policies');
        onClose();
      },
      onError: (error: any) => {
        const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to update check.';
        alert(`Failed to update check: ${errorMessage}`);
      },
    }
  );

  const handleValidate = () => {
    if (filters.length > 0 && resourceType) {
      validateMutation.mutate({ filters, resourceType });
    }
  };

  const handleTemplateSelect = (template: PolicyTemplate) => {
    setResourceType(template.resource_type);
    
    // Use filters_json if available (new format), otherwise fall back to YAML parsing
    if ((template as any).filters_json && Array.isArray((template as any).filters_json)) {
      setFilters((template as any).filters_json);
    } else if (template.policy_yaml) {
      // Fallback: Try to extract basic info from YAML
      const parsed = template.policy_yaml;
      const nameMatch = parsed.match(/^name:\s*(.+)$/m);
      if (nameMatch) {
        setName(nameMatch[1].trim());
      }
      const descMatch = parsed.match(/^description:\s*(.+)$/m);
      if (descMatch) {
        setDescription(descMatch[1].trim());
      }
      // Note: For YAML-only templates, user will need to add filters manually
      // or we could add a conversion utility in the future
    }
    
    // Set name and description from template
    if (template.name) {
      setName(template.name);
    }
    if (template.description) {
      setDescription(template.description);
    }
  };

  const handleSubmit = () => {
    if (policy) {
      const updateData = {
        name,
        description,
        filters_json: filters,
        status,
      };
      updateMutation.mutate(updateData);
    } else {
      const createData = {
        name,
        description,
        filters_json: filters.length > 0 ? filters : undefined,
        resource_type: resourceType,
        status,
      };
      createMutation.mutate(createData);
    }
  };

  const isLoading = createMutation.isLoading || updateMutation.isLoading;

  return (
    <div className="space-y-6">
      {/* Template Selector */}
      {!policy && (
        <div>
          <label className="label">Start from Template (Optional)</label>
          <TemplateSelector
            templates={templates}
            onSelect={handleTemplateSelect}
          />
        </div>
      )}

      {/* Basic Info */}
      <div>
        <label className="label">Policy Name *</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="input"
          placeholder="e.g., Idle EC2 Instances"
        />
      </div>

      <div>
        <label className="label">Description</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="input"
          rows={2}
          placeholder="Describe what this policy does..."
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="label">Resource Type *</label>
          {policy ? (
            <input
              type="text"
              value={resourceType.toUpperCase()}
              className="input bg-gray-100 dark:bg-gray-800 cursor-not-allowed"
              readOnly
            />
          ) : (
            <select
              value={resourceType}
              onChange={(e) => {
                setResourceType(e.target.value);
                // Clear filters when resource type changes (they may not be compatible)
                setFilters([]);
              }}
              className="input"
            >
              <option value="ec2">EC2</option>
              <option value="rds">RDS</option>
              <option value="ebs">EBS</option>
              <option value="snapshot">Snapshot</option>
            </select>
          )}
        </div>
        <div>
          <label className="label">Status</label>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as 'active' | 'inactive')}
            className="input"
          >
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </div>
      </div>

      {/* Policy Code Display (for existing policies) */}
      {policy?.policy_code && (
        <div>
          <label className="label">Policy Code</label>
          <div className="px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded border border-gray-300 dark:border-gray-700">
            <code className="text-sm font-mono text-gray-900 dark:text-white">{policy.policy_code}</code>
          </div>
        </div>
      )}

      {/* Filter Builder */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="label mb-0">Filter Conditions *</label>
          <Button variant="secondary" size="sm" onClick={handleValidate} disabled={filters.length === 0}>
            Validate
          </Button>
        </div>
        <FilterBuilder
          resourceType={resourceType}
          filters={filters}
          onChange={setFilters}
          errors={validationErrors}
        />
      </div>

      {/* Validation Results */}
      {validationErrors.length > 0 && (
        <div className="bg-danger-50 border border-danger-200 rounded-lg p-4">
          <h4 className="font-semibold text-danger-800 mb-2">Validation Errors</h4>
          <ul className="list-disc list-inside text-sm text-danger-700 space-y-1">
            {validationErrors.map((error, idx) => (
              <li key={idx}>{error}</li>
            ))}
          </ul>
        </div>
      )}

      {validationWarnings.length > 0 && (
        <div className="bg-warning-50 border border-warning-200 rounded-lg p-4">
          <h4 className="font-semibold text-warning-800 mb-2">Warnings</h4>
          <ul className="list-disc list-inside text-sm text-warning-700 space-y-1">
            {validationWarnings.map((warning, idx) => (
              <li key={idx}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-end space-x-3 pt-4 border-t border-gray-200">
        <Button variant="secondary" onClick={onClose} disabled={isLoading}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleSubmit}
          isLoading={isLoading}
          disabled={!name}
        >
          {policy ? 'Update Check' : 'Create Check'}
        </Button>
      </div>
    </div>
  );
};

