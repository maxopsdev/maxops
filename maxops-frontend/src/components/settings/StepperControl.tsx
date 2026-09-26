import React from 'react';

import type { CheckParameterDefinition } from '@/types/api';
import { clampControlValue, formatControlValue } from '@/components/settings/controlUtils';

export const StepperControl: React.FC<{
  value: number;
  onChange: (value: number) => void;
  definition: CheckParameterDefinition;
}> = ({ value, onChange, definition }) => (
  <div className="flex items-center gap-3 rounded-2xl border border-gray-200 bg-gray-200 p-3 dark:border-gray-800 dark:bg-gray-900">
    <button
      type="button"
      onClick={() => onChange(clampControlValue(value - Number(definition.step ?? 1), definition.min, definition.max))}
      className="h-10 w-10 rounded-full border border-gray-300 text-lg dark:border-gray-700"
    >
      -
    </button>
    <div className="flex-1 text-center">
      <div className="text-lg font-semibold text-gray-900 dark:text-white">{formatControlValue(value, definition.unit)}</div>
      <div className="text-xs text-gray-500 dark:text-gray-400">{definition.label}</div>
    </div>
    <button
      type="button"
      onClick={() => onChange(clampControlValue(value + Number(definition.step ?? 1), definition.min, definition.max))}
      className="h-10 w-10 rounded-full border border-gray-300 text-lg dark:border-gray-700"
    >
      +
    </button>
  </div>
);
