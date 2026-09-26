import React, { useState, useEffect } from 'react';
import { Save, X } from 'lucide-react';
import { Button } from '@/components/common/Button';
import type { FilterCondition, FilterDefinition } from '@/types/api';

interface FilterConditionEditorProps {
  resourceType: string;
  availableFilters: Record<string, FilterDefinition>;
  filter?: FilterCondition;
  onSave: (filter: FilterCondition) => void;
  onCancel: () => void;
}

export const FilterConditionEditor: React.FC<FilterConditionEditorProps> = ({
  availableFilters,
  filter,
  onSave,
  onCancel,
}) => {
  const [filterType, setFilterType] = useState<string>(filter?.type || '');
  const [operator, setOperator] = useState<string>(filter?.operator || '');
  const [value, setValue] = useState<any>(filter?.value ?? '');

  const selectedFilterDef = filterType ? availableFilters[filterType] : null;

  useEffect(() => {
    if (filterType && selectedFilterDef) {
      // Set default operator if not set
      if (!operator && selectedFilterDef.operators.length > 0) {
        setOperator(selectedFilterDef.operators[0]);
      }
      // Set default value if not set
      if (value === '' && selectedFilterDef.default !== undefined) {
        setValue(selectedFilterDef.default);
      }
    }
  }, [filterType, selectedFilterDef]);

  const handleSave = () => {
    if (!filterType || !operator) {
      return;
    }

    // Validate value based on filter definition
    if (selectedFilterDef) {
      if (selectedFilterDef.value_type === 'number' && typeof value !== 'number') {
        const numValue = parseFloat(value);
        if (isNaN(numValue)) {
          alert('Please enter a valid number');
          return;
        }
        setValue(numValue);
      }
    }

    onSave({
      type: filterType,
      operator,
      value,
    });
  };

  const renderValueInput = () => {
    if (!selectedFilterDef) return null;

      const uiComponent = selectedFilterDef.ui_component || 'TextInput';

    switch (uiComponent) {
      case 'NumberInput':
        return (
          <div>
            <label className="label">Value {selectedFilterDef.unit && `(${selectedFilterDef.unit})`}</label>
            <input
              type="number"
              value={value}
              onChange={(e) => {
                const numValue = parseFloat(e.target.value);
                setValue(isNaN(numValue) ? '' : numValue);
              }}
              className="input"
              min={selectedFilterDef.min}
              max={selectedFilterDef.max}
              placeholder={selectedFilterDef.example ? `e.g., ${selectedFilterDef.example}` : ''}
            />
          </div>
        );

      case 'Select':
        return (
          <div>
            <label className="label">Value</label>
            <select
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="input"
            >
              <option value="">Select...</option>
              {selectedFilterDef.options?.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        );

      case 'Checkbox':
        return (
          <div className="flex items-center space-x-2">
            <input
              type="checkbox"
              checked={value === true}
              onChange={(e) => setValue(e.target.checked)}
              className="w-4 h-4 text-primary-600 rounded"
            />
            <label className="label mb-0">{selectedFilterDef.label}</label>
          </div>
        );

      case 'TagInput':
        return (
          <div className="space-y-2">
            <div>
              <label className="label">Tag Key</label>
              <input
                type="text"
                value={typeof value === 'object' && value !== null ? value.key || '' : ''}
                onChange={(e) => setValue({ ...(typeof value === 'object' && value !== null ? value : {}), key: e.target.value })}
                className="input"
                placeholder="e.g., Environment"
              />
            </div>
            {operator === 'equals' && (
              <div>
                <label className="label">Tag Value (optional)</label>
                <input
                  type="text"
                  value={typeof value === 'object' && value !== null ? value.value || '' : ''}
                  onChange={(e) => setValue({ ...(typeof value === 'object' && value !== null ? value : {}), value: e.target.value })}
                  className="input"
                  placeholder="e.g., Production"
                />
              </div>
            )}
          </div>
        );

      default:
        return (
          <div>
            <label className="label">Value</label>
            <input
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="input"
              placeholder={selectedFilterDef.example ? `e.g., ${selectedFilterDef.example}` : ''}
            />
          </div>
        );
    }
  };

  return (
    <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="label">Filter Type</label>
          <select
            value={filterType}
            onChange={(e) => {
              setFilterType(e.target.value);
              setOperator('');
              setValue('');
            }}
            className="input"
          >
            <option value="">Select filter type...</option>
            {Object.entries(availableFilters).map(([key, def]) => (
              <option key={key} value={key}>
                {def.label} {def.description && `- ${def.description}`}
              </option>
            ))}
          </select>
        </div>

        {selectedFilterDef && (
          <div>
            <label className="label">Operator</label>
            <select
              value={operator}
              onChange={(e) => setOperator(e.target.value)}
              className="input"
            >
              <option value="">Select operator...</option>
              {selectedFilterDef.operators.map((op) => (
                <option key={op} value={op}>
                  {op.replace('_', ' ')}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {selectedFilterDef && operator && (
        <div>{renderValueInput()}</div>
      )}

      {selectedFilterDef?.description && (
        <p className="text-sm text-gray-600 dark:text-gray-400">{selectedFilterDef.description}</p>
      )}

      <div className="flex items-center justify-end space-x-3 pt-4 border-t border-gray-200 dark:border-gray-700">
        <Button variant="secondary" onClick={onCancel} size="sm">
          <X size={16} className="mr-2" />
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleSave}
          size="sm"
          disabled={!filterType || !operator || (selectedFilterDef?.value_type !== 'boolean' && value === '')}
        >
          <Save size={16} className="mr-2" />
          Save Filter
        </Button>
      </div>
    </div>
  );
};

