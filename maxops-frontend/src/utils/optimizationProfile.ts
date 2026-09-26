export type OptimizationProfile = 'conservative' | 'normal' | 'aggressive';

const PROFILE_FACTORS: Record<OptimizationProfile, number> = {
  conservative: 0.7,
  normal: 1,
  aggressive: 1.35,
};

export const OPTIMIZATION_PROFILE_OPTIONS: Array<{
  label: string;
  value: OptimizationProfile;
}> = [
  { label: 'Conservative', value: 'conservative' },
  { label: 'Normal', value: 'normal' },
  { label: 'Aggressive', value: 'aggressive' },
];

export const adjustOptimizationSavings = (
  value: number | null | undefined,
  profile: OptimizationProfile
): number => Number((Number(value || 0) * PROFILE_FACTORS[profile]).toFixed(2));

export const adjustOptimizationCount = (
  value: number | null | undefined,
  profile: OptimizationProfile
): number => Math.max(0, Math.round(Number(value || 0) * PROFILE_FACTORS[profile]));
