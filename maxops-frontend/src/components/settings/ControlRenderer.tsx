import React from 'react';

import { Input } from '@/components/common/Input';
import { DialControl } from '@/components/settings/DialControl';
import { SliderControl } from '@/components/settings/SliderControl';
import { StepperControl } from '@/components/settings/StepperControl';
import { ToggleControl } from '@/components/settings/ToggleControl';
import { normalizeBooleanValue } from '@/components/settings/controlUtils';
import type { CheckParameterDefinition } from '@/types/api';

export const ControlRenderer: React.FC<{
  definition: CheckParameterDefinition;
  value: any;
  onChange: (value: any) => void;
}> = ({ definition, value, onChange }) => {
  if (definition.control === 'toggle') {
    const normalized = normalizeBooleanValue(value);
    return <ToggleControl value={normalized} onChange={(next) => onChange(Boolean(next))} />;
  }
  if (definition.control === 'dial' && typeof value === 'number') {
    return <DialControl value={value} onChange={onChange} definition={definition} />;
  }
  if (definition.control === 'slider' && typeof value === 'number') {
    return <SliderControl value={value} onChange={onChange} definition={definition} />;
  }
  if (definition.control === 'stepper' && typeof value === 'number') {
    return <StepperControl value={value} onChange={onChange} definition={definition} />;
  }
  if (definition.control === 'select') {
    return (
      <select
        value={String(value ?? '')}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
      >
        {(definition.options ?? []).map((option) => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    );
  }
  if (definition.control === 'token-list') {
    return (
      <Input
        value={Array.isArray(value) ? value.join(', ') : ''}
        onChange={(event) =>
          onChange(
            event.target.value
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean)
          )
        }
        placeholder="Comma-separated values"
      />
    );
  }

  return (
    <Input
      value={typeof value === 'string' ? value : String(value ?? '')}
      onChange={(event) => onChange(event.target.value)}
      placeholder={definition.label}
    />
  );
};
