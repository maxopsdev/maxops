import React from 'react';
import * as Slider from '@radix-ui/react-slider';

import type { CheckParameterDefinition } from '@/types/api';
import { formatControlValue } from '@/components/settings/controlUtils';

export const SliderControl: React.FC<{
  value: number;
  onChange: (value: number) => void;
  definition: CheckParameterDefinition;
}> = ({ value, onChange, definition }) => {
  const minimum = typeof definition.min === 'number' ? definition.min : 0;
  const maximum = typeof definition.max === 'number' ? definition.max : Math.max(value, 100);

  return (
    <div className="space-y-3 rounded-2xl border border-gray-200 bg-gray-200 p-4 dark:border-gray-800 dark:bg-gray-900">
      <div className="flex items-center justify-between gap-4">
        <div className="text-sm font-medium text-gray-700 dark:text-gray-200">{definition.label}</div>
        <div className="rounded-full bg-primary-50 px-3 py-1 text-sm font-semibold text-primary-700 dark:bg-gray-900 dark:text-gray-200">
          {formatControlValue(value, definition.unit)}
        </div>
      </div>
      <Slider.Root
        min={minimum}
        max={maximum}
        step={definition.step ?? 1}
        value={[value]}
        onValueChange={(values) => {
          const nextValue = values[0];
          if (typeof nextValue === 'number') {
            onChange(nextValue);
          }
        }}
        className="relative flex h-6 w-full touch-none select-none items-center"
      >
        <Slider.Track className="relative h-2 grow overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
          <Slider.Range className="absolute h-full rounded-full bg-primary-600" />
        </Slider.Track>
        <Slider.Thumb className="block h-5 w-5 rounded-full border border-primary-200 bg-white shadow-md outline-none ring-offset-2 transition hover:scale-105 focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-gray-700 dark:bg-gray-100" />
      </Slider.Root>
      <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
        <span>{minimum}</span>
        <span>{maximum}</span>
      </div>
    </div>
  );
};
