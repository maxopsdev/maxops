import React from 'react';

import type { CheckParameterDefinition } from '@/types/api';
import { CHART_COLORS } from '@/styles/chartColors';

export const DialControl: React.FC<{
  value: number;
  onChange: (value: number) => void;
  definition: CheckParameterDefinition;
}> = ({ value, onChange, definition }) => {
  const minimum = typeof definition.min === 'number' ? definition.min : 1;
  const maximum = typeof definition.max === 'number' ? definition.max : Math.max(value, 30);
  const percentage = ((value - minimum) / Math.max(maximum - minimum, 1)) * 100;
  // teal-600 (primary, deeper) — matches the accent-primary-600 used on the range input below
  const dialFillColor = CHART_COLORS[3];

  return (
    <div className="flex items-center gap-4 rounded-2xl border border-gray-200 bg-white/70 p-4 dark:border-gray-800 dark:bg-gray-950">
      <div
        className="relative h-20 w-20 rounded-full"
        style={{
          background: `conic-gradient(${dialFillColor} ${percentage * 3.6}deg, rgba(126, 141, 179, 0.28) 0deg)`,
          boxShadow:
            'inset 0 1px 1px rgba(255,255,255,0.45), inset 0 -2px 4px rgba(14,24,48,0.25), 0 8px 16px rgba(14,24,48,0.22)',
        }}
      >
        <div className="absolute inset-0 rounded-full dark:bg-gray-950/30" />
        <div
          className="absolute inset-1 rounded-full opacity-80"
          style={{
            background: 'linear-gradient(145deg, rgba(255,255,255,0.38), rgba(255,255,255,0.04))',
          }}
        />
        <div
          className="absolute inset-2 rounded-full border border-white/40 bg-white dark:border-gray-800 dark:bg-gray-950"
          style={{
            boxShadow: 'inset 0 1px 2px rgba(255,255,255,0.45), inset 0 -2px 4px rgba(14,24,48,0.25)',
          }}
        />
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-lg font-semibold text-gray-900 dark:text-white">{value}</span>
          <span className="text-[10px] uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
            {definition.unit ?? 'value'}
          </span>
        </div>
      </div>
      <div className="flex-1 space-y-2">
        <input
          type="range"
          min={minimum}
          max={maximum}
          step={definition.step ?? 1}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-gray-200 accent-primary-600 dark:bg-gray-800"
        />
        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
          <span>{minimum}</span>
          <span>{maximum}</span>
        </div>
      </div>
    </div>
  );
};
