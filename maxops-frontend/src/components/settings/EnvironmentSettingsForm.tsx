import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Globe2, Layers3, Search, X } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { Input } from '@/components/common/Input';
import { ACCOUNT_ROLE_ACCESS_DESCRIPTION } from '@/constants/accountSettings';
import { AWS_REGION_OPTIONS } from '@/constants/awsRegions';
import type { OnboardingDraftRequest, OnboardingRequest } from '@/types/api';

const STANDARD_ENVIRONMENTS = [
  'Development',
  'Staging',
  'Production',
  'UAT',
  'QA',
  'Testing',
  'Sandbox',
];

interface EnvironmentSettingsFormProps {
  initialValues?: Partial<OnboardingRequest>;
  accountAutofill?: { accountId: string; nonce: number } | null;
  onSubmit: (data: OnboardingRequest) => void;
  onCancel?: () => void;
  submitLabel?: string;
  cancelLabel?: string;
  isSubmitting?: boolean;
  showSubmitArrow?: boolean;
  fitViewport?: boolean;
  onDraftChange?: (data: OnboardingDraftRequest) => void;
}

const resolveEnvironmentSelection = (environment?: string) => {
  if (!environment) {
    return { selected: '', custom: '', useCustom: false };
  }
  if (STANDARD_ENVIRONMENTS.includes(environment)) {
    return { selected: environment, custom: '', useCustom: false };
  }
  return { selected: '', custom: environment, useCustom: true };
};

const isValidAwsAccountId = (value: string) => /^\d{12}$/.test(value.trim());

export const EnvironmentSettingsForm: React.FC<EnvironmentSettingsFormProps> = ({
  initialValues,
  accountAutofill,
  onSubmit,
  onCancel,
  submitLabel = 'Continue',
  cancelLabel = 'Cancel',
  isSubmitting = false,
  showSubmitArrow = true,
  fitViewport = false,
  onDraftChange,
}) => {
  const initialEnvironment = useMemo(
    () => resolveEnvironmentSelection(initialValues?.environment),
    [initialValues?.environment]
  );

  const [selectedEnvironment, setSelectedEnvironment] = useState(initialEnvironment.selected);
  const [customEnvironment, setCustomEnvironment] = useState(initialEnvironment.custom);
  const [useCustom, setUseCustom] = useState(initialEnvironment.useCustom);
  const [account, setAccount] = useState(initialValues?.account ?? '');
  const [regions, setRegions] = useState<string[]>(
    initialValues?.regions?.length ? initialValues.regions : initialValues?.region ? [initialValues.region] : ['us-east-1']
  );
  const [regionSearch, setRegionSearch] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const latestDraftRef = useRef<OnboardingDraftRequest | null>(null);

  useEffect(() => {
    const environment = resolveEnvironmentSelection(initialValues?.environment);
    setSelectedEnvironment(environment.selected);
    setCustomEnvironment(environment.custom);
    setUseCustom(environment.useCustom);
    setAccount(initialValues?.account ?? '');
    setRegions(
      initialValues?.regions?.length
        ? initialValues.regions
        : initialValues?.region
          ? [initialValues.region]
          : ['us-east-1']
    );
  }, [initialValues]);

  useEffect(() => {
    if (!accountAutofill?.accountId) {
      return;
    }

    setAccount(accountAutofill.accountId);
    setErrors((current) => ({ ...current, account: '' }));
  }, [accountAutofill?.accountId, accountAutofill?.nonce]);

  const normalizedRegionSearch = regionSearch.trim().toLowerCase();
  const filteredRegionOptions = useMemo(() => {
    if (!normalizedRegionSearch) {
      return AWS_REGION_OPTIONS;
    }

    return AWS_REGION_OPTIONS.filter((region) => {
      const searchableText = `${region.label} ${region.value} ${region.group}`.toLowerCase();
      return searchableText.includes(normalizedRegionSearch);
    });
  }, [normalizedRegionSearch]);

  const groupedRegions = useMemo(() => {
    return filteredRegionOptions.reduce<Record<string, typeof AWS_REGION_OPTIONS>>((acc, region) => {
      if (!acc[region.group]) {
        acc[region.group] = [];
      }
      acc[region.group].push(region);
      return acc;
    }, {});
  }, [filteredRegionOptions]);

  const selectedEnvironmentValue = useCustom ? customEnvironment.trim() : selectedEnvironment.trim();
  const normalizedAccount = account.trim();
  const normalizedRegions = useMemo(() => regions.filter(Boolean), [regions]);
  const isFormValid =
    Boolean(selectedEnvironmentValue) &&
    isValidAwsAccountId(normalizedAccount) &&
    normalizedRegions.length > 0;
  const currentDraft = useMemo<OnboardingDraftRequest>(() => ({
    environment: selectedEnvironmentValue,
    account: normalizedAccount,
    region: normalizedRegions[0],
    regions: normalizedRegions,
  }), [selectedEnvironmentValue, normalizedAccount, normalizedRegions]);

  useEffect(() => {
    latestDraftRef.current = currentDraft;
  }, [currentDraft]);

  useEffect(() => {
    if (!onDraftChange) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      onDraftChange(currentDraft);
    }, 600);

    return () => window.clearTimeout(timeoutId);
  }, [onDraftChange, currentDraft]);

  useEffect(() => {
    return () => {
      if (latestDraftRef.current) {
        onDraftChange?.(latestDraftRef.current);
      }
    };
  }, [onDraftChange]);

  const handleSubmit = () => {
    const nextErrors: Record<string, string> = {};
    const environment = selectedEnvironmentValue;

    if (!environment) {
      nextErrors.environment = 'Choose an environment or enter a custom one.';
    }
    if (!isValidAwsAccountId(normalizedAccount)) {
      nextErrors.account = 'Enter a valid 12-digit AWS account ID.';
    }
    if (!normalizedRegions.length) {
      nextErrors.regions = 'Select at least one AWS region.';
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }

    onSubmit({
      environment,
      account: normalizedAccount,
      region: normalizedRegions[0],
      regions: normalizedRegions,
    });
  };

  const toggleRegion = (region: string) => {
    setRegions((current) => {
      if (current.includes(region)) {
        return current.length === 1 ? current : current.filter((item) => item !== region);
      }
      return [...current, region];
    });
    setErrors((current) => ({ ...current, regions: '' }));
  };

  return (
    <div className={fitViewport ? 'flex min-h-0 flex-1 flex-col gap-4 overflow-hidden' : 'space-y-8'}>
      {!fitViewport && (
      <div className="rounded-3xl border border-primary-100 bg-gradient-to-br from-primary-50 via-white to-gray-50 p-6 shadow-sm dark:border-primary-900/40 dark:from-gray-900 dark:via-gray-900 dark:to-gray-950">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-primary-700 dark:text-primary-300">
              Account Setup
            </p>
            <h2 className="text-2xl font-semibold text-gray-900 dark:text-white">
              Define the account context once, then tune checks later.
            </h2>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              Start with environment, account, and target regions. Detailed optimization tuning lives in the main settings page after onboarding.
            </p>
          </div>
          <div className="rounded-2xl border border-primary-200 bg-white/80 px-4 py-3 text-sm text-gray-700 shadow-sm backdrop-blur dark:border-primary-900/50 dark:bg-gray-900/80 dark:text-gray-200">
            <div className="flex items-center gap-2">
              <Globe2 size={16} className="text-primary-600 dark:text-primary-300" />
              <span>{regions.length} region{regions.length === 1 ? '' : 's'} selected</span>
            </div>
          </div>
        </div>
      </div>
      )}

      <div className={fitViewport ? 'shrink-0 space-y-3' : 'space-y-4'}>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">Environment</label>
        {!useCustom ? (
          <div className={fitViewport ? 'grid grid-cols-2 gap-2 md:grid-cols-4' : 'grid grid-cols-2 gap-3 md:grid-cols-4'}>
            {STANDARD_ENVIRONMENTS.map((env) => (
              <button
                key={env}
                type="button"
                onClick={() => {
                  setSelectedEnvironment(env);
                  setUseCustom(false);
                  setErrors((current) => ({ ...current, environment: '' }));
                }}
                className={`${fitViewport ? 'rounded-xl px-3 py-2' : 'rounded-2xl px-4 py-3'} border text-left text-sm font-medium transition-all ${
                  selectedEnvironment === env
                    ? 'border-primary-600 bg-primary-600 text-white shadow-lg shadow-primary-200 dark:shadow-primary-950/40'
                    : 'border-gray-200 bg-white text-gray-700 hover:border-primary-300 hover:bg-primary-50 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-primary-700 dark:hover:bg-primary-950/30'
                }`}
              >
                {env}
              </button>
            ))}
          </div>
        ) : (
          <Input
            type="text"
            value={customEnvironment}
            onChange={(event) => {
              setCustomEnvironment(event.target.value);
              setErrors((current) => ({ ...current, environment: '' }));
            }}
            placeholder="Custom environment name"
            error={errors.environment}
          />
        )}
        <button
          type="button"
          onClick={() => {
            setUseCustom((current) => !current);
            setSelectedEnvironment('');
            setCustomEnvironment('');
            setErrors((current) => ({ ...current, environment: '' }));
          }}
          className="text-sm font-medium text-primary-700 hover:underline dark:text-primary-300"
        >
          {useCustom ? 'Choose from standard environments' : 'Use a custom environment'}
        </button>
        {errors.environment && <p className="text-sm text-danger-600 dark:text-danger-400">{errors.environment}</p>}
      </div>

      <div className={fitViewport ? 'grid min-h-0 flex-1 gap-4 overflow-hidden lg:grid-cols-[0.95fr_1.45fr]' : 'grid gap-6 lg:grid-cols-[1.05fr_1.4fr]'}>
        <div className={`${fitViewport ? 'min-h-0 space-y-3 overflow-hidden rounded-2xl p-4' : 'space-y-4 rounded-3xl p-5'} border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900`}>
          <div className="flex items-center gap-2">
            <Layers3 size={16} className="text-primary-600 dark:text-primary-300" />
            <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
              Account
            </h3>
          </div>
          <Input
            type="text"
            value={account}
            onChange={(event) => {
              setAccount(event.target.value);
              setErrors((current) => ({ ...current, account: '' }));
            }}
            placeholder="AWS account ID or display name"
            error={errors.account}
          />
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Enter the 12-digit AWS account ID for this MaxOps workspace.
          </p>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {ACCOUNT_ROLE_ACCESS_DESCRIPTION}
          </p>
        </div>

        <div className={`${fitViewport ? 'min-h-0 gap-3 rounded-2xl p-4' : 'h-[32rem] gap-4 rounded-3xl p-5'} flex flex-col overflow-hidden border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900`}>
          <div className={fitViewport ? 'shrink-0 space-y-2' : 'space-y-3'}>
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
                Regions
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Select all regions you want checks to run against.
              </p>
            </div>
            <div>
              <label htmlFor="region-search" className="sr-only">
                Search regions
              </label>
              <div className="relative">
                <Search
                  size={16}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500"
                />
                <input
                  id="region-search"
                  type="search"
                  value={regionSearch}
                  onChange={(event) => setRegionSearch(event.target.value)}
                  placeholder="Search by region name or code"
                  className="h-10 w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-10 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500 dark:border-gray-700 dark:bg-gray-950 dark:text-white dark:placeholder:text-gray-500"
                />
                {regionSearch && (
                  <button
                    type="button"
                    onClick={() => setRegionSearch('')}
                    aria-label="Clear region search"
                    className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-500 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
              {normalizedRegionSearch && (
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  {filteredRegionOptions.length} matching region{filteredRegionOptions.length === 1 ? '' : 's'}
                </p>
              )}
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
            {filteredRegionOptions.length === 0 ? (
              <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-400">
                No regions match your search.
              </div>
            ) : Object.entries(groupedRegions).map(([group, groupRegions]) => (
              <div key={group}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                  {group}
                </p>
                <div className="flex flex-wrap gap-2">
                  {groupRegions.map((region) => {
                    const active = regions.includes(region.value);
                    return (
                      <button
                        key={region.value}
                        type="button"
                        onClick={() => toggleRegion(region.value)}
                        className={`rounded-full border px-3 py-2 text-sm transition-all ${
                          active
                            ? 'border-primary-600 bg-primary-600 text-white shadow-md shadow-primary-200 dark:shadow-primary-950/40'
                            : 'border-gray-300 bg-gray-50 text-gray-700 hover:border-primary-300 hover:bg-primary-50 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 dark:hover:border-primary-700 dark:hover:bg-primary-950/40'
                        }`}
                      >
                        {region.label}
                        <span className="ml-2 text-xs opacity-80">{region.value}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
          {errors.regions && <p className="text-sm text-danger-600 dark:text-danger-400">{errors.regions}</p>}
        </div>
      </div>

      <div className={`${fitViewport ? 'shrink-0 pb-0 pt-4' : 'pb-2 pt-6'} flex justify-between gap-3 border-t border-gray-200 dark:border-gray-800`}>
        {onCancel && (
          <Button variant="secondary" onClick={onCancel} disabled={isSubmitting}>
            {cancelLabel}
          </Button>
        )}
        <Button variant="primary" onClick={handleSubmit} disabled={isSubmitting || !isFormValid}>
          {submitLabel}
          {showSubmitArrow && <ArrowRight className="ml-2" size={16} />}
        </Button>
      </div>
    </div>
  );
};
