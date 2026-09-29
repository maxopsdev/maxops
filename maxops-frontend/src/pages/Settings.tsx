import React, { useEffect, useMemo, useState } from 'react';
import {
  ChevronRight,
  Coins,
  Database,
  Gauge,
  Globe2,
  Layers3,
  Plus,
  Save,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from 'react-query';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { Input } from '@/components/common/Input';
import { ControlRenderer } from '@/components/settings/ControlRenderer';
import { ToggleControl } from '@/components/settings/ToggleControl';
import { formatControlValue } from '@/components/settings/controlUtils';
import { PRESET_ACCENTS, PRESET_OPTIONS, type PresetKey } from '@/components/settings/PresetConfig';
import { ScanCredentialsCard } from '@/components/settings/ScanCredentialsCard';
import { SegmentedPreset } from '@/components/settings/SegmentedPreset';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { Layout } from '@/components/layout/Layout';
import { costDataApi } from '@/services/costData';
import { onboardingApi, settingsApi } from '@/services/settings';
import { ACCOUNT_ROLE_ACCESS_DESCRIPTION } from '@/constants/accountSettings';
import { AWS_REGION_VALUES } from '@/constants/awsRegions';
import type {
  ActionResourceGroup,
  CheckParameterDefinition,
  CheckSettingsCatalogItem,
  PricingDatabaseStatus,
  SettingsCatalogResponse,
  SettingsResourceGroup,
  SettingsUpdateRequest,
  UserSettings,
} from '@/types/api';

type DraftCheck = CheckSettingsCatalogItem & { expandedAdvanced?: boolean };
type DraftGroup = Omit<SettingsResourceGroup, 'checks'> & { checks: DraftCheck[] };
type DraftActionGroup = ActionResourceGroup;

const ENVIRONMENT_OPTIONS = ['Development', 'Staging', 'Production', 'UAT', 'QA', 'Testing', 'Sandbox'];
type PendingAction =
  | { kind: 'group'; resourceType: string }
  | { kind: 'preset'; preset: PresetKey }
  | null;
type PendingAccountChange = {
  payload: SettingsUpdateRequest;
  onSuccess?: () => void;
  previousAccount: string;
  nextAccount: string;
} | null;
type PresetSnapshots = Record<PresetKey, Record<string, Record<string, any>>>;
const GLOBAL_PRESET_DESCRIPTIONS: Record<PresetKey, string> = {
  conservative: 'Flags only the coldest and safest optimization opportunities.',
  normal: 'Balanced detection tuned for typical workloads and steady savings.',
  aggressive: 'Surfaces more opportunities with tighter thresholds across checks.',
};

const cloneCatalog = (catalog: SettingsCatalogResponse | undefined): DraftGroup[] =>
  (catalog?.resource_groups ?? [])
    .map((group) => {
      const checks = group.checks.map((check) => ({
        ...check,
        parameters: { ...check.parameters },
        parameter_definitions: check.parameter_definitions.map((definition) => ({ ...definition })),
        expandedAdvanced: false,
      }));
      return {
        ...group,
        check_count: checks.length,
        checks,
      };
    })
    .filter((group) => group.checks.length > 0);

const cloneActionCatalog = (catalog: SettingsCatalogResponse | undefined): DraftActionGroup[] =>
  (catalog?.action_groups ?? []).map((group) => ({
    ...group,
    actions: group.actions.map((action) => ({ ...action })),
  }));

const buildSettingsPayload = (
  globalSettings: Pick<UserSettings, 'environment' | 'account' | 'environment_options' | 'region' | 'regions'>,
  draftGroups: DraftGroup[],
  draftActionGroups: DraftActionGroup[]
): SettingsUpdateRequest => ({
  global_settings: {
    environment: globalSettings.environment,
    account: globalSettings.account,
    environment_options: globalSettings.environment_options,
    region: globalSettings.regions[0] ?? globalSettings.region,
    regions: globalSettings.regions,
  },
  check_settings: draftGroups.flatMap((group) =>
    group.checks.map((check) => ({
      check_id: check.check_id,
      preset: check.preset,
      parameters: check.parameters,
      enabled: check.enabled,
    }))
  ),
  action_settings: Array.from(
    new Map(
      draftActionGroups
        .flatMap((group) => group.actions)
        .map((action) => [action.action_key, { action_key: action.action_key, enabled: action.enabled }])
    ).values()
  ),
});

const cloneValue = <T,>(value: T): T => {
  if (Array.isArray(value)) {
    return [...value] as T;
  }
  if (value && typeof value === 'object') {
    return { ...(value as Record<string, any>) } as T;
  }
  return value;
};

const buildSettingAnchorId = (checkId: string, settingKey: string): string => {
  const normalize = (value: string) => value.replace(/[^a-zA-Z0-9_-]/g, '-');
  return `setting-${normalize(checkId)}-${normalize(settingKey)}`;
};

const normalizeAccount = (value: string | null | undefined): string => String(value ?? '').trim();

const buildPresetSnapshots = (groups: DraftGroup[]): PresetSnapshots => {
  const snapshots: PresetSnapshots = {
    conservative: {},
    normal: {},
    aggressive: {},
  };

  groups.forEach((group) => {
    group.checks.forEach((check) => {
      PRESET_OPTIONS.forEach((preset) => {
        snapshots[preset][check.check_id] = Object.fromEntries(
          check.parameter_definitions.map((definition) => [
            definition.key,
            cloneValue(definition.preset_values[preset]),
          ])
        );
      });
      snapshots[check.preset][check.check_id] = Object.fromEntries(
        Object.entries(check.parameters).map(([key, value]) => [key, cloneValue(value)])
      );
    });
  });

  return snapshots;
};

export const SettingsPage: React.FC = () => {
  const { setProfile: setSelectedProfile } = useOptimizationProfile();
  const queryClient = useQueryClient();
  const [draftGroups, setDraftGroups] = useState<DraftGroup[]>([]);
  const [draftActionGroups, setDraftActionGroups] = useState<DraftActionGroup[]>([]);
  const [actionsEnabled, setActionsEnabled] = useState(false);
  const [activeGroup, setActiveGroup] = useState<string>('');
  const [globalSettings, setGlobalSettings] = useState<UserSettings | null>(null);
  const [globalPreset, setGlobalPreset] = useState<PresetKey>('normal');
  const [presetSnapshots, setPresetSnapshots] = useState<PresetSnapshots>({
    conservative: {},
    normal: {},
    aggressive: {},
  });
  const [hasPendingEdits, setHasPendingEdits] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [pendingAccountChange, setPendingAccountChange] = useState<PendingAccountChange>(null);
  const [saveSuccessMessage, setSaveSuccessMessage] = useState<string | null>(null);
  const [saveErrorMessage, setSaveErrorMessage] = useState<string | null>(null);
  const [regionsMenuOpen, setRegionsMenuOpen] = useState(false);
  const [customEnvironmentInput, setCustomEnvironmentInput] = useState('');

  const { data: catalogData, isLoading } = useQuery<SettingsCatalogResponse>(
    'settings-catalog',
    () => settingsApi.getSettingsCatalog(),
    {
      retry: false,
    }
  );

  const { data: pricingDatabaseStatus, refetch: refetchPricingDatabaseStatus } = useQuery<PricingDatabaseStatus>(
    ['settings-pricing-database-status'],
    () => onboardingApi.getPricingDatabaseStatus(),
    {
      retry: false,
    }
  );

  const { data: costDataStatus } = useQuery(
    ['settings-cost-data-status'],
    () => costDataApi.getSetupStatus(),
    {
      retry: false,
    }
  );
  const costDataPricingStep = costDataStatus?.steps.find((step) => step.id === 'pricing');
  const costDataIsLive = costDataPricingStep?.state === 'done';

  useEffect(() => {
    if (!catalogData) {
      return;
    }
    const nextGroups = cloneCatalog(catalogData);
    setDraftGroups(nextGroups);
    setDraftActionGroups(cloneActionCatalog(catalogData));
    setActionsEnabled(Boolean(catalogData.actions_enabled));
    setPresetSnapshots(buildPresetSnapshots(nextGroups));
    setGlobalSettings(catalogData.global_settings);
    setHasPendingEdits(false);
    const initialPreset = catalogData.resource_groups.flatMap((group) => group.checks)[0]?.preset;
    const resolvedPreset = (initialPreset as PresetKey | undefined) ?? 'normal';
    setGlobalPreset(resolvedPreset);
    setSelectedProfile(resolvedPreset);
    if (!activeGroup && catalogData.resource_groups.length > 0) {
      setActiveGroup(catalogData.resource_groups[0].resource_type);
    }
  }, [catalogData, activeGroup]);

  const updateSettingsMutation = useMutation((data: SettingsUpdateRequest) => settingsApi.updateSettings(data), {
    onMutate: () => {
      setSaveSuccessMessage(null);
      setSaveErrorMessage(null);
    },
  });

  const unpackPricingDatabaseMutation = useMutation(
    () => onboardingApi.unpackPricingDatabase(false),
    {
      onSuccess: async () => {
        await refetchPricingDatabaseStatus();
        setSaveErrorMessage(null);
        setSaveSuccessMessage('Pricing database extracted. Street pricing is ready.');
      },
      onError: (error: any) => {
        setSaveSuccessMessage(null);
        setSaveErrorMessage(
          error?.response?.data?.detail ?? error?.message ?? 'Failed to extract pricing database.'
        );
      },
    }
  );

  const handleMutationSuccess = (data: SettingsCatalogResponse, accountChanged = false) => {
    queryClient.setQueryData('settings-catalog', data);
    queryClient.setQueryData('user-settings', data.global_settings);
    queryClient.setQueryData('onboarding-status', data.global_settings);
    if (accountChanged) {
      queryClient.invalidateQueries('check-last-runs');
      queryClient.invalidateQueries('latest-check-results');
      queryClient.invalidateQueries('check-resources');
      queryClient.invalidateQueries('dashboard-savings-histories-v2');
      queryClient.invalidateQueries('dashboard-shared-overview-filter-options');
      queryClient.invalidateQueries('inventory-ec2-overview');
      queryClient.invalidateQueries('inventory-rds-overview');
      queryClient.invalidateQueries('inventory-s3-overview');
      queryClient.invalidateQueries('inventory-dynamodb-overview');
      queryClient.invalidateQueries('inventory-ebs-overview');
      queryClient.invalidateQueries('inventory-elasticache-overview');
      queryClient.invalidateQueries('policies');
      queryClient.invalidateQueries('executions');
      queryClient.invalidateQueries('cost-savings');
    }
    const nextGroups = cloneCatalog(data);
    setDraftGroups(nextGroups);
    setDraftActionGroups(cloneActionCatalog(data));
    setActionsEnabled(Boolean(data.actions_enabled));
    setPresetSnapshots(buildPresetSnapshots(nextGroups));
    setGlobalSettings(data.global_settings);
    setHasPendingEdits(false);
    const nextPreset = data.resource_groups.flatMap((group) => group.checks)[0]?.preset;
    const resolvedPreset = (nextPreset as PresetKey | undefined) ?? 'normal';
    setGlobalPreset(resolvedPreset);
    setSelectedProfile(resolvedPreset);
    setSaveSuccessMessage('Settings saved. New thresholds will be used on the next check run.');
  };

  const totalChecks = useMemo(
    () => draftGroups.reduce((sum, group) => sum + group.check_count, 0),
    [draftGroups]
  );

  const customizedChecks = useMemo(
    () =>
      draftGroups.reduce(
        (sum, group) => sum + group.checks.filter((check) => check.is_customized).length,
        0
      ),
    [draftGroups]
  );

  const selectedGroup = useMemo(
    () => draftGroups.find((group) => group.resource_type === activeGroup) ?? draftGroups[0],
    [activeGroup, draftGroups]
  );

  const selectedActionGroup = useMemo(
    () => draftActionGroups.find((group) => group.resource_type === selectedGroup?.resource_type),
    [draftActionGroups, selectedGroup]
  );

  const currentPayload = useMemo(() => {
    if (!globalSettings) {
      return null;
    }
    return buildSettingsPayload(globalSettings, draftGroups, draftActionGroups);
  }, [draftGroups, draftActionGroups, globalSettings]);

  const updateEnvironment = (environment: string) => {
    setGlobalSettings((current) => {
      if (!current) {
        return current;
      }
      return { ...current, environment };
    });
    setHasPendingEdits(true);
  };

  const environmentOptions = useMemo(() => {
    const currentEnvironment = globalSettings?.environment?.trim();
    const merged = [
      ...ENVIRONMENT_OPTIONS,
      ...((globalSettings?.environment_options || []).map((value) => String(value || '').trim()).filter(Boolean)),
      ...(currentEnvironment ? [currentEnvironment] : []),
    ];
    return merged.filter((value, index, values) => values.findIndex((entry) => entry.toLowerCase() === value.toLowerCase()) === index);
  }, [globalSettings?.environment, globalSettings?.environment_options]);

  const addCustomEnvironment = () => {
    const nextEnvironment = customEnvironmentInput.trim();
    if (!nextEnvironment || !globalSettings) {
      return;
    }

    const nextOptions = [...environmentOptions, nextEnvironment].filter(
      (value, index, values) => values.findIndex((entry) => entry.toLowerCase() === value.toLowerCase()) === index
    );
    setGlobalSettings((current) => {
      if (!current) {
        return current;
      }
      return { ...current, environment: nextEnvironment, environment_options: nextOptions };
    });
    setHasPendingEdits(true);
    setCustomEnvironmentInput('');
  };

  const applyGlobalPreset = (preset: PresetKey) => {
    setGlobalPreset(preset);
    setSelectedProfile(preset);
    setDraftGroups((groups) =>
      groups.map((group) => ({
        ...group,
        checks: group.checks.map((check) => {
          const snapshot = presetSnapshots[preset][check.check_id];
          const nextParameters = snapshot
            ? Object.fromEntries(Object.entries(snapshot).map(([key, value]) => [key, cloneValue(value)]))
            : Object.fromEntries(
                check.parameter_definitions.map((definition) => [
                  definition.key,
                  cloneValue(definition.preset_values[preset]),
                ])
              );
          const presetDefaults = Object.fromEntries(
            check.parameter_definitions.map((definition) => [definition.key, definition.preset_values[preset]])
          );
          return {
            ...check,
            preset,
            parameters: nextParameters,
            is_customized: JSON.stringify(nextParameters) !== JSON.stringify(presetDefaults),
          };
        }),
      }))
    );
  };

  const performPendingAction = (action: PendingAction) => {
    if (!action) {
      return;
    }
    setHasPendingEdits(false);
    if (action.kind === 'group') {
      setActiveGroup(action.resourceType);
      window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    } else {
      applyGlobalPreset(action.preset);
    }
    setPendingAction(null);
  };

  const requestGroupChange = (resourceType: string) => {
    if (resourceType === activeGroup) {
      return;
    }
    if (hasPendingEdits) {
      setPendingAction({ kind: 'group', resourceType });
      return;
    }
    setActiveGroup(resourceType);
    window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
  };

  const requestGlobalPresetChange = (preset: PresetKey) => {
    if (preset === globalPreset) {
      return;
    }
    if (hasPendingEdits) {
      setPendingAction({ kind: 'preset', preset });
      return;
    }
    applyGlobalPreset(preset);
  };

  const updateCheckParameter = (resourceType: string, checkId: string, definition: CheckParameterDefinition, value: any) => {
    setHasPendingEdits(true);
    setDraftGroups((groups) =>
      groups.map((group) =>
        group.resource_type !== resourceType
          ? group
          : {
              ...group,
              checks: group.checks.map((check) => {
                if (check.check_id !== checkId) {
                  return check;
                }
                const nextParameters = { ...check.parameters, [definition.key]: value };
                const presetDefault = definition.preset_values[check.preset];
                const parameterCustomized = nextParameters[definition.key] !== presetDefault;
                const isCustomized =
                  parameterCustomized ||
                  check.parameter_definitions.some((item) =>
                    item.key === definition.key
                      ? false
                      : nextParameters[item.key] !== item.preset_values[check.preset]
                  );
                return {
                  ...check,
                  parameters: nextParameters,
                  is_customized: isCustomized,
                };
              }),
            }
      )
    );
    setPresetSnapshots((snapshots) => {
      const currentCheck = draftGroups
        .flatMap((group) => group.checks)
        .find((check) => check.check_id === checkId);
      if (!currentCheck) {
        return snapshots;
      }
      const nextCheckParameters = { ...currentCheck.parameters, [definition.key]: value };
      return {
        ...snapshots,
        [currentCheck.preset]: {
          ...snapshots[currentCheck.preset],
          [checkId]: Object.fromEntries(
            Object.entries(nextCheckParameters).map(([key, snapshotValue]) => [key, cloneValue(snapshotValue)])
          ),
        },
      };
    });
  };

  const toggleCheckEnabled = (resourceType: string, checkId: string) => {
    setHasPendingEdits(true);
    setDraftGroups((groups) =>
      groups.map((group) =>
        group.resource_type !== resourceType
          ? group
          : {
              ...group,
              checks: group.checks.map((check) =>
                check.check_id === checkId ? { ...check, enabled: !check.enabled } : check
              ),
            }
      )
    );
  };

  const toggleActionEnabled = (actionKey: string) => {
    setHasPendingEdits(true);
    setDraftActionGroups((groups) =>
      groups.map((group) => ({
        ...group,
        actions: group.actions.map((action) =>
          action.action_key === actionKey ? { ...action, enabled: !action.enabled } : action
        ),
      }))
    );
  };

  const toggleAdvanced = (resourceType: string, checkId: string) => {
    setDraftGroups((groups) =>
      groups.map((group) =>
        group.resource_type !== resourceType
          ? group
          : {
              ...group,
              checks: group.checks.map((check) =>
                check.check_id === checkId
                  ? { ...check, expandedAdvanced: !check.expandedAdvanced }
                  : check
              ),
            }
      )
    );
  };

  const scrollToSetting = (checkId: string, definition: CheckParameterDefinition) => {
    const scroll = () => {
      document
        .getElementById(buildSettingAnchorId(checkId, definition.key))
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };

    if (definition.advanced) {
      setDraftGroups((groups) =>
        groups.map((group) =>
          group.resource_type !== selectedGroup?.resource_type
            ? group
            : {
                ...group,
                checks: group.checks.map((check) =>
                  check.check_id === checkId ? { ...check, expandedAdvanced: true } : check
                ),
              }
        )
      );
      window.requestAnimationFrame(() => window.requestAnimationFrame(scroll));
      return;
    }

    scroll();
  };

  const toggleRegion = (region: string) => {
    setHasPendingEdits(true);
    setGlobalSettings((current) => {
      if (!current) return current;
      const exists = current.regions.includes(region);
      const nextRegions = exists
        ? current.regions.length === 1
          ? current.regions
          : current.regions.filter((item) => item !== region)
        : [...current.regions, region];
      return { ...current, regions: nextRegions, region: nextRegions[0] };
    });
  };

  const submitSettings = async (
    payload: SettingsUpdateRequest,
    onSuccess?: () => void,
    accountChanged = false
  ) => {
    try {
      const data = await updateSettingsMutation.mutateAsync(payload);
      handleMutationSuccess(data, accountChanged);
      setPendingAccountChange(null);
      onSuccess?.();
    } catch (error: any) {
      setSaveErrorMessage(error?.response?.data?.detail ?? 'Unable to save settings. Please try again.');
    }
  };

  const handleSave = async (onSuccess?: () => void) => {
    if (!currentPayload) {
      return;
    }

    const previousAccount = catalogData?.global_settings?.account ?? '';
    const nextAccount = currentPayload.global_settings.account;
    const accountChanged = normalizeAccount(previousAccount) !== normalizeAccount(nextAccount);
    if (accountChanged) {
      setSaveSuccessMessage(null);
      setSaveErrorMessage(null);
      setPendingAccountChange({
        payload: currentPayload,
        onSuccess,
        previousAccount,
        nextAccount,
      });
      return;
    }

    await submitSettings(currentPayload, onSuccess, false);
  };

  const cancelAccountChange = () => {
    const previousAccount = pendingAccountChange?.previousAccount ?? catalogData?.global_settings?.account;
    setPendingAccountChange(null);
    if (previousAccount === undefined) {
      return;
    }
    setGlobalSettings((current) => (current ? { ...current, account: previousAccount } : current));
  };

  if (isLoading || !globalSettings) {
    return (
      <Layout>
        <div className="flex min-h-[50vh] items-center justify-center">
          <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-primary-600" />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">
        <section className="rounded-[2rem] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(13,157,137,0.12),_transparent_32%),linear-gradient(135deg,#f7fdfb_0%,#ecfdf9_44%,#ffffff_100%)] p-6 shadow-sm dark:border-gray-800 dark:bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.08),_transparent_26%),linear-gradient(135deg,#0b1020_0%,#0a0f1a_45%,#050814_100%)]">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div className="max-w-4xl space-y-3">
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-primary-700 dark:text-gray-300">
                Optimization Controls
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-3xl font-semibold text-gray-900 dark:text-white">Settings</h1>
                <div className="flex flex-wrap gap-3">
                  <div className="rounded-2xl border border-white/60 bg-white/80 px-4 py-3 shadow-sm backdrop-blur dark:border-gray-800 dark:bg-gray-900">
                    <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      <ShieldCheck size={15} />
                      Account
                    </div>
                    <p className="mt-1 truncate text-base font-semibold text-gray-900 dark:text-white">{globalSettings.account}</p>
                  </div>
                  <div className="rounded-2xl border border-white/60 bg-white/80 px-4 py-3 shadow-sm backdrop-blur dark:border-gray-800 dark:bg-gray-900">
                    <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      <Settings2 size={15} />
                      Checks Tuned
                    </div>
                    <p className="mt-1 text-base font-semibold text-gray-900 dark:text-white">
                      {customizedChecks} / {totalChecks}
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/60 bg-white/80 px-4 py-3 shadow-sm backdrop-blur dark:border-gray-800 dark:bg-gray-900">
                    <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      <Globe2 size={15} />
                      Regions
                    </div>
                    <p className="mt-1 text-base font-semibold text-gray-900 dark:text-white">{globalSettings.regions.length}</p>
                  </div>
                </div>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Tune how aggressively MaxOps flags savings opportunities. Use presets for speed, then open advanced controls only where precision matters.
              </p>
            </div>
          </div>
        </section>

        {saveSuccessMessage && (
          <div className="rounded-2xl border border-success-200 bg-success-50 px-4 py-3 text-sm text-success-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
            {saveSuccessMessage}
          </div>
        )}
        {saveErrorMessage && (
          <div className="rounded-2xl border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
            {saveErrorMessage}
          </div>
        )}

        {pricingDatabaseStatus && !pricingDatabaseStatus.ready && (
          <Card className="rounded-[1.75rem] border border-warning-200 bg-warning-50/70 p-0 shadow-sm dark:border-warning-900/60 dark:bg-gray-900">
            <div className="flex flex-wrap items-start justify-between gap-4 p-5">
              <div className="min-w-0 space-y-3">
                <div className="flex items-center gap-2">
                  <Database size={18} className="text-warning-700 dark:text-warning-300" />
                  <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-warning-900 dark:text-warning-200">
                    Pricing Database
                  </h2>
                </div>
                <p className="text-sm text-warning-900 dark:text-warning-100">
                  {pricingDatabaseStatus.message}
                </p>
                <div className="space-y-1 text-xs text-warning-800/80 dark:text-warning-200/80">
                  <div>Artifact: {pricingDatabaseStatus.artifact_path}</div>
                  <div>Database: {pricingDatabaseStatus.db_path}</div>
                </div>
              </div>
              {pricingDatabaseStatus.state === 'artifact_available' && (
                <Button
                  variant="secondary"
                  onClick={() => unpackPricingDatabaseMutation.mutate()}
                  isLoading={unpackPricingDatabaseMutation.isLoading}
                >
                  Extract Pricing Database
                </Button>
              )}
            </div>
          </Card>
        )}

        <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-0 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <div className="flex flex-wrap items-start justify-between gap-4 p-5">
            <div className="min-w-0 space-y-2">
              <div className="flex items-center gap-2">
                <Coins size={16} className="text-primary-600 dark:text-gray-300" />
                <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
                  Cost Data
                </h2>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                {costDataIsLive
                  ? `Findings are priced from your actual bill — ${costDataPricingStep?.billing_month}, ${costDataPricingStep?.resource_count} resources.`
                  : 'Findings are priced from public list prices. Connect your Cost and Usage Report to use what resources actually cost.'}
              </p>
            </div>
            <Link
              to="/settings/cost-data"
              className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              {costDataIsLive ? 'Manage' : 'Set up'}
              <ChevronRight size={16} />
            </Link>
          </div>
        </Card>

        <ScanCredentialsCard />

        <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-0 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <div className="border-b border-gray-200 px-5 py-4 dark:border-gray-800">
            <div className="flex items-center gap-2">
              <Layers3 size={16} className="text-primary-600 dark:text-gray-300" />
              <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
                Global Settings
              </h2>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-5 p-5 sm:grid-cols-12 sm:gap-x-6 lg:gap-x-8">
            <div className="rounded-2xl border border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-950 sm:col-span-3">
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">Environment</label>
              <select
                value={globalSettings.environment}
                onChange={(event) => {
                  updateEnvironment(event.target.value);
                }}
                className="w-full rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm text-gray-700 shadow-sm dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
              >
                {environmentOptions.map((environment) => (
                  <option key={environment} value={environment}>
                    {environment}
                  </option>
                ))}
              </select>
              <div className="mt-3 flex items-center gap-2">
                <Input
                  value={customEnvironmentInput}
                  onChange={(event) => setCustomEnvironmentInput(event.target.value)}
                  placeholder="Add custom environment"
                />
                <button
                  type="button"
                  onClick={addCustomEnvironment}
                  disabled={!customEnvironmentInput.trim()}
                  className="inline-flex shrink-0 items-center gap-2 rounded-2xl border border-gray-300 bg-white px-3 py-3 text-sm font-medium text-gray-700 shadow-sm transition hover:border-primary-300 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-primary-700 dark:hover:text-primary-300"
                >
                  <Plus size={14} />
                  Add
                </button>
              </div>
            </div>
            <div className="rounded-2xl border border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-950 sm:col-span-4">
              <Input
                label="Account Number"
                value={globalSettings.account}
                onChange={(event) =>
                  setGlobalSettings((current) => {
                    if (!current) {
                      return current;
                    }
                    setHasPendingEdits(true);
                    return { ...current, account: event.target.value };
                  })
                }
                placeholder="AWS account ID"
              />
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                {ACCOUNT_ROLE_ACCESS_DESCRIPTION}
              </p>
            </div>
            <div className="relative rounded-2xl border border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-950 sm:col-span-5">
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">Regions</label>
              <button
                type="button"
                onClick={() => setRegionsMenuOpen((current) => !current)}
                className="flex w-full items-center justify-between rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm text-gray-700 shadow-sm dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
              >
                <span className="truncate">
                  {globalSettings.regions.length > 0 ? globalSettings.regions.join(', ') : 'Select regions'}
                </span>
                <span className="ml-3 text-xs text-gray-500 dark:text-gray-400">
                  {globalSettings.regions.length} selected
                </span>
              </button>
              {regionsMenuOpen && (
                <div className="absolute left-0 right-0 top-full z-20 mt-2 h-72 overflow-auto rounded-2xl border border-gray-200 bg-white p-2 shadow-xl dark:border-gray-800 dark:bg-gray-900">
                  <div className="space-y-1">
                    {AWS_REGION_VALUES.map((region) => {
                      const active = globalSettings.regions.includes(region);
                      return (
                        <label
                          key={region}
                          className="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-sm text-gray-700 hover:bg-primary-50 dark:text-gray-200 dark:hover:bg-gray-800"
                        >
                          <input
                            type="checkbox"
                            checked={active}
                            onChange={() => toggleRegion(region)}
                            className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                          />
                          <span>{region}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                The first selected region is treated as the primary region for single-region fallbacks.
              </p>
            </div>
          </div>
        </Card>

        <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-0 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <div className="border-b border-gray-200 px-5 py-4 dark:border-gray-800">
            <div className="flex items-center gap-2">
              <Settings2 size={16} className="text-primary-600 dark:text-gray-300" />
              <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
                Optimization Profile
              </h2>
            </div>
          </div>
          <div className="p-5">
            <SegmentedPreset
              preset={globalPreset}
              descriptions={GLOBAL_PRESET_DESCRIPTIONS}
              onChange={requestGlobalPresetChange}
            />
          </div>
        </Card>

        <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)]">
          <div className="xl:sticky xl:top-24 xl:self-start">
            <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-0 shadow-sm dark:border-gray-800 dark:bg-gray-900">
              <div className="border-b border-gray-200 px-5 py-4 dark:border-gray-800">
                <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-primary-600 dark:text-gray-300" />
                  <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
                    Resource Types
                  </h2>
                </div>
              </div>
              <div className="space-y-2 p-3">
                {draftGroups.map((group) => (
                  <button
                    key={group.resource_type}
                    type="button"
                    onClick={() => requestGroupChange(group.resource_type)}
                    className={`w-full rounded-2xl px-4 py-3 text-left transition ${
                      activeGroup === group.resource_type
                        ? 'bg-primary-600 text-gray-200 shadow-sm shadow-primary-200 dark:bg-gray-800 dark:text-gray-100 dark:shadow-none'
                        : 'bg-transparent text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-900'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <div className="font-semibold">{group.label}</div>
                        <div className={`text-xs ${activeGroup === group.resource_type ? 'text-primary-100 dark:text-gray-300' : 'text-gray-500 dark:text-gray-400'}`}>
                          {group.check_count} checks
                        </div>
                      </div>
                      <div
                        className={`rounded-full px-2 py-1 text-xs font-semibold ${
                          activeGroup === group.resource_type
                            ? 'bg-white/15 text-white dark:bg-gray-700 dark:text-gray-100'
                            : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
                        }`}
                      >
                        {group.checks.filter((check) => check.is_customized).length} tuned
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </Card>
          </div>

          <div className="min-w-0 space-y-6">
            {selectedGroup && (
              <>
                <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                  <Gauge size={18} className="text-primary-600 dark:text-gray-300" />
                        <h2 className="text-2xl font-semibold text-gray-900 dark:text-white">{selectedGroup.label}</h2>
                      </div>
                      <p className="mt-2 max-w-3xl text-sm text-gray-600 dark:text-gray-400">{selectedGroup.description}</p>
                    </div>
                    <div className="rounded-2xl border border-primary-100 bg-primary-50 px-4 py-3 text-sm text-primary-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
                      {selectedGroup.checks.filter((check) => check.is_customized).length} of {selectedGroup.check_count} checks customized
                    </div>
                  </div>
                  <div className="mt-5 border-t border-gray-100 pt-5 dark:border-gray-800">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-gray-900 dark:text-white">Settings Summary</div>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          Select a setting to jump to its control.
                        </p>
                      </div>
                      <div className="text-xs font-medium text-gray-500 dark:text-gray-400">
                        {selectedGroup.checks.reduce(
                          (sum, check) => sum + check.parameter_definitions.length,
                          0
                        )} settings
                      </div>
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
                      {selectedGroup.checks.map((check) => (
                        <div
                          key={`${check.check_id}-summary`}
                          className="rounded-2xl border border-gray-200 bg-gray-50/80 p-3 dark:border-gray-700 dark:bg-gray-950"
                        >
                          <div className="mb-2 truncate text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                            {check.name}
                          </div>
                          <div className="divide-y divide-gray-200 dark:divide-gray-800">
                            {check.parameter_definitions.map((definition) => {
                              const value = check.parameters[definition.key];
                              return (
                                <button
                                  key={`${check.check_id}-${definition.key}-summary`}
                                  type="button"
                                  onClick={() => scrollToSetting(check.check_id, definition)}
                                  className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 py-2 text-left text-xs transition hover:text-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-500 dark:hover:text-primary-300"
                                >
                                  <span className="min-w-0">
                                    <span className="block truncate font-semibold text-gray-700 dark:text-gray-200">
                                      {definition.label}
                                    </span>
                                    {definition.advanced && (
                                      <span className="mt-1 inline-flex rounded-full bg-gray-200 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                                        Advanced
                                      </span>
                                    )}
                                  </span>
                                  <span className="max-w-[9rem] truncate rounded-full bg-white px-2.5 py-1 font-semibold text-gray-600 shadow-sm dark:bg-gray-900 dark:text-gray-300">
                                    {formatControlValue(value, definition.unit)}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </Card>

                <div className="space-y-5">
                  {selectedGroup.checks.map((check) => {
                    const primaryDefinitions = check.parameter_definitions.filter((definition) => !definition.advanced);
                    const advancedDefinitions = check.parameter_definitions.filter((definition) => definition.advanced);

                    return (
                      <Card
                        key={check.check_id}
                        className="rounded-[1.75rem] border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900"
                      >
                        <div className="space-y-5">
                          <div className="flex flex-wrap items-start justify-between gap-4">
                            <div className="max-w-3xl">
                              <div className="flex flex-wrap items-center gap-3">
                                <h3 className="text-xl font-semibold text-gray-900 dark:text-white">{check.name}</h3>
                                <span className={`rounded-full border px-3 py-1 text-xs font-semibold capitalize ${PRESET_ACCENTS[check.preset]}`}>
                                  {check.preset}
                                </span>
                                {check.is_customized && (
                                  <span className="rounded-full border border-gray-300 bg-gray-50 px-3 py-1 text-xs font-semibold text-gray-700 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-300">
                                    Custom overrides
                                  </span>
                                )}
                              </div>
                              <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">{check.description}</p>
                            </div>
                            <div className="flex flex-col items-stretch gap-2">
                              <div className="w-44">
                                <ToggleControl
                                  value={check.enabled}
                                  onChange={() => toggleCheckEnabled(selectedGroup.resource_type, check.check_id)}
                                />
                              </div>
                              <div className="rounded-2xl border border-gray-200 bg-gray-100/80 px-4 py-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                                Action: {check.default_action.replace(/_/g, ' ')}
                              </div>
                            </div>
                          </div>

                          {primaryDefinitions.length > 0 && (
                            <div className="grid gap-4 xl:grid-cols-2">
                              {primaryDefinitions.map((definition) => (
                                <div
                                  key={definition.key}
                                  id={buildSettingAnchorId(check.check_id, definition.key)}
                                  className="scroll-mt-32 space-y-2 rounded-2xl focus-within:ring-2 focus-within:ring-primary-500"
                                >
                                  {definition.control !== 'slider' && (
                                    <div>
                                      <div className="text-sm font-medium text-gray-700 dark:text-gray-200">{definition.label}</div>
                                      {definition.control !== 'dial' && (
                                        <p className="text-xs text-gray-500 dark:text-gray-400">{definition.description}</p>
                                      )}
                                    </div>
                                  )}
                                  <ControlRenderer
                                    definition={definition}
                                    value={check.parameters[definition.key]}
                                    onChange={(value) =>
                                      updateCheckParameter(selectedGroup.resource_type, check.check_id, definition, value)
                                    }
                                  />
                                </div>
                              ))}
                            </div>
                          )}

                          {advancedDefinitions.length > 0 && (
                            <div className="rounded-3xl border border-dashed border-gray-300 p-4 dark:border-gray-700 dark:bg-gray-950/60">
                              <div className="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                  <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.16em] text-gray-600 dark:text-gray-300">
                                    <SlidersHorizontal size={15} />
                                    Advanced Controls
                                  </div>
                                  <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                                    Fine-tune supporting parameters only when the default preset does not fit your workload.
                                  </p>
                                </div>
                                <Button
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => toggleAdvanced(selectedGroup.resource_type, check.check_id)}
                                >
                                  {check.expandedAdvanced ? 'Hide Advanced' : 'Show Advanced'}
                                </Button>
                              </div>
                              {check.expandedAdvanced && (
                                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                                  {advancedDefinitions.map((definition) => (
                                    <div
                                      key={definition.key}
                                      id={buildSettingAnchorId(check.check_id, definition.key)}
                                      className="scroll-mt-32 space-y-2 rounded-2xl focus-within:ring-2 focus-within:ring-primary-500"
                                    >
                                      <div>
                                        <div className="text-sm font-medium text-gray-700 dark:text-gray-200">{definition.label}</div>
                                        <p className="text-xs text-gray-500 dark:text-gray-400">{definition.description}</p>
                                      </div>
                                      <ControlRenderer
                                        definition={definition}
                                        value={check.parameters[definition.key]}
                                        onChange={(value) =>
                                          updateCheckParameter(selectedGroup.resource_type, check.check_id, definition, value)
                                        }
                                      />
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </Card>
                    );
                  })}
                </div>

                {selectedActionGroup && selectedActionGroup.actions.length > 0 && (
                  <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
                    <div className="flex items-center gap-2">
                      <ShieldCheck size={18} className="text-primary-600 dark:text-gray-300" />
                      <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Actions</h2>
                    </div>
                    <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
                      Control which {selectedGroup.label} actions can be executed from the UI or MCP.
                    </p>
                    {!actionsEnabled && (
                      <div className="mt-4 rounded-2xl border border-warning-300 bg-warning-50 px-4 py-3 text-sm text-warning-800 dark:border-warning-700/60 dark:bg-warning-950/40 dark:text-warning-300">
                        Actions are disabled for this environment. Set <code>MAXOPS_ENABLE_ACTIONS=true</code> in the
                        backend environment to allow any action to run; the toggles below only take effect once that
                        is set.
                      </div>
                    )}
                    <div className="mt-5 grid gap-3 md:grid-cols-2">
                      {selectedActionGroup.actions.map((action) => (
                        <div
                          key={action.action_key}
                          className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-gray-50/80 p-4 dark:border-gray-700 dark:bg-gray-950"
                        >
                          <div>
                            <div className="text-sm font-semibold text-gray-900 dark:text-white">{action.name}</div>
                            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{action.description}</p>
                          </div>
                          <ToggleControl
                            value={actionsEnabled && action.enabled}
                            onChange={() => toggleActionEnabled(action.action_key)}
                          />
                        </div>
                      ))}
                    </div>
                  </Card>
                )}
              </>
            )}

            <div className="sticky bottom-4 z-10">
              <div className="rounded-[1.75rem] border border-primary-100 bg-white/95 p-4 shadow-xl backdrop-blur dark:border-gray-700 dark:bg-gray-900">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-semibold text-gray-900 dark:text-white">Ready to apply</div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">
                      Save account settings and all current check tuning in one action.
                    </p>
                  </div>
                  <Button variant="primary" onClick={() => void handleSave()} isLoading={updateSettingsMutation.isLoading}>
                    <Save size={16} className="mr-2" />
                    Save Settings
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {pendingAction && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/70 p-4 backdrop-blur-sm">
            <div className="w-full max-w-xl rounded-[1.75rem] border border-gray-800 bg-white p-6 shadow-2xl dark:bg-gray-900">
              <div className="space-y-3">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Unsaved changes</h2>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  {pendingAction.kind === 'group'
                    ? 'You have unsaved changes. Save them before switching resource types, or continue without saving.'
                    : 'You have unsaved changes. Save them before changing the optimization profile, or continue without saving and replace the current tuning.'}
                </p>
              </div>
              <div className="mt-6 flex flex-wrap justify-end gap-3">
                <Button variant="ghost" onClick={() => setPendingAction(null)}>
                  Cancel
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    const action = pendingAction;
                    void handleSave(() => performPendingAction(action));
                  }}
                  isLoading={updateSettingsMutation.isLoading}
                >
                  Save and Continue
                </Button>
                <Button variant="primary" onClick={() => performPendingAction(pendingAction)}>
                  Continue Without Saving
                </Button>
              </div>
            </div>
          </div>
        )}

        {pendingAccountChange && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-gray-950/70 p-4 backdrop-blur-sm">
            <div className="w-full max-w-xl rounded-[1.75rem] border border-warning-200 bg-white p-6 shadow-2xl dark:border-warning-900/60 dark:bg-gray-900">
              <div className="space-y-3">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Change AWS account?</h2>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Changing the account from {pendingAccountChange.previousAccount || 'the current account'} to{' '}
                  {pendingAccountChange.nextAccount || 'the new account'} will remove all scanned inventory, scan results,
                  resource snapshots, and per-resource snoozes for the current account.
                </p>
                <p className="text-sm font-medium text-warning-700 dark:text-warning-300">
                  Continue only if you want MaxOps to start fresh for the new account.
                </p>
              </div>
              <div className="mt-6 flex flex-wrap justify-end gap-3">
                <Button variant="ghost" onClick={cancelAccountChange}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  onClick={() =>
                    void submitSettings(
                      pendingAccountChange.payload,
                      pendingAccountChange.onSuccess,
                      true
                    )
                  }
                  isLoading={updateSettingsMutation.isLoading}
                >
                  Change Account
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};
