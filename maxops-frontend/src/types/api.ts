/** API response types */

export interface Policy {
  id: number;
  policy_code?: string;
  name: string;
  description?: string;
  check_id?: string;
  policy_yaml?: string;  // Deprecated, kept for backward compatibility
  filters_json?: FilterCondition[];  // New filter-based approach
  resource_type: string;
  status: 'active' | 'inactive';
  created_at: string;
  updated_at: string;
}

export interface FilterCondition {
  type: string;
  operator: string;
  value: any;
}

export interface FilterDefinition {
  type: string;
  label: string;
  description?: string;
  operators: string[];
  value_type: string;
  ui_component?: string;
  options?: string[];
  min?: number;
  max?: number;
  unit?: string;
  example?: any;
  default?: any;
}

export interface PolicyCreate {
  name: string;
  description?: string;
  policy_yaml?: string;  // Deprecated, kept for backward compatibility
  filters_json?: FilterCondition[];  // New filter-based approach
  resource_type: string;
  status?: 'active' | 'inactive';
}

export interface PolicyUpdate {
  name?: string;
  description?: string;
  policy_yaml?: string;  // Deprecated, kept for backward compatibility
  filters_json?: FilterCondition[];  // New filter-based approach
  resource_type?: string;
  status?: 'active' | 'inactive';
}

export interface PolicyExecution {
  id: number;
  policy_id: number;
  execution_type: string;  // New rows are 'scan'; older rows may be 'dry-run'
  status: 'running' | 'completed' | 'failed';
  resources_found: number;
  results_json?: Record<string, any>;
  error_message?: string;
  started_at: string;
  completed_at?: string;
  results?: PolicyExecutionResult[];
  // Cost aggregation fields
  total_monthly_cost?: number;
  cost_by_resource_type?: Record<string, number>;
  cost_by_region?: Record<string, number>;
  resource_count_by_type?: Record<string, number>;
}

export interface PolicyExecutionResult {
  id: number;
  resource_id: string;
  resource_type: string;
  resource_name?: string;
  region?: string;
  account_id?: string;
  reason?: string;
  metadata_json?: Record<string, any>;
  // Cost fields
  monthly_cost?: number;
  cost_breakdown?: Record<string, any>;
}

export interface PolicyTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  resource_type: string;
  policy_yaml?: string;  // Deprecated, kept for backward compatibility
  filters_json?: FilterCondition[];  // New filter-based format
}

export interface PolicyValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface PolicyCostSavings {
  id: number;
  policy_id: number;
  execution_id?: number;
  date: string;
  cost_saved: number;
  resources_fixed: number;
  notes?: string;
}

export interface ApiError {
  detail: string;
}

export interface OnboardingRequest {
  environment: string;
  account: string;
  region?: string;
  regions: string[];
  idle_days?: number;
  a_days?: number;
  b_days?: number;
}

export interface UserSettings {
  id: number;
  environment: string;
  account: string;
  environment_options: string[];
  region?: string;
  regions: string[];
  onboarding_completed: boolean;
  onboarding_step?: 'information' | 'role' | 'account' | 'checks' | 'results' | 'completed';
  onboarding_data?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface OnboardingProgressRequest {
  onboarding_step?: 'information' | 'role' | 'account' | 'checks' | 'results' | 'completed';
  onboarding_data?: Record<string, any>;
}

export interface OnboardingDraftRequest {
  environment?: string;
  account?: string;
  region?: string;
  regions?: string[];
}

export interface GlobalSettingsPayload {
  environment: string;
  account: string;
  environment_options: string[];
  region?: string;
  regions: string[];
}

export interface CheckSettingsPayload {
  check_id: string;
  preset: 'conservative' | 'normal' | 'aggressive';
  parameters: Record<string, any>;
  enabled: boolean;
}

export interface ActionSettingsPayload {
  action_key: string;
  enabled: boolean;
}

export interface SettingsUpdateRequest {
  global_settings: GlobalSettingsPayload;
  check_settings: CheckSettingsPayload[];
  action_settings: ActionSettingsPayload[];
}

export interface CheckParameterDefinition {
  key: string;
  label: string;
  description: string;
  control: 'slider' | 'dial' | 'toggle' | 'stepper' | 'select' | 'text' | 'token-list';
  unit?: string | null;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  advanced: boolean;
  options?: Array<string | boolean> | null;
  preset_values: Record<'conservative' | 'normal' | 'aggressive', any>;
  value: any;
}

export interface CheckSettingsCatalogItem {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  default_action: string;
  preset: 'conservative' | 'normal' | 'aggressive';
  parameters: Record<string, any>;
  is_customized: boolean;
  enabled: boolean;
  preset_descriptions: Record<'conservative' | 'normal' | 'aggressive', string>;
  parameter_definitions: CheckParameterDefinition[];
}

export interface SettingsResourceGroup {
  resource_type: string;
  label: string;
  description: string;
  check_count: number;
  checks: CheckSettingsCatalogItem[];
}

export interface ActionSettingsCatalogItem {
  action_key: string;
  name: string;
  description: string;
  resource_types: string[];
  enabled: boolean;
}

export interface ActionResourceGroup {
  resource_type: string;
  label: string;
  action_count: number;
  actions: ActionSettingsCatalogItem[];
}

export interface SettingsCatalogResponse {
  global_settings: UserSettings | null;
  resource_groups: SettingsResourceGroup[];
  actions_enabled: boolean;
  action_groups: ActionResourceGroup[];
}

export interface OnboardingExecution {
  id: number;
  status: 'running' | 'completed' | 'failed' | 'canceled';
  total_checks: number;
  completed_checks: number;
  failed_checks: number;
  started_at: string | null;
  completed_at: string | null;
  error_message?: string | null;
  results?: OnboardingCheckResult[];
}

export interface OnboardingCheckResult {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  status: 'completed' | 'failed';
  resources_found: number;
  potential_savings_yearly?: number;
  error: string | null;
}

export interface OnboardingAwsProfile {
  profile_name: string | null;
  display_name: string;
  account_id?: string | null;
  arn?: string | null;
  is_default: boolean;
  error?: string | null;
}

export interface OnboardingAwsProfilesResponse {
  profiles: OnboardingAwsProfile[];
}

export interface OnboardingIamRoleResponse {
  role_name: string;
  role_arn?: string | null;
  policy_name?: string | null;
  trusted_principal_arn?: string | null;
  aws_profile_name?: string | null;
  aws_account_id?: string | null;
  scan_profile_name?: string | null;
  scan_profile_config_path?: string | null;
  scan_profile_source_profile?: string | null;
  scan_profile_credential_source?: string | null;
  status: 'created' | 'updated_existing' | 'existing_role_registered';
  message: string;
}

export interface PricingDatabaseStatus {
  ready: boolean;
  state: 'ready' | 'artifact_available' | 'missing_artifact';
  message: string;
  db_path: string;
  artifact_path: string;
}

export interface Check {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  default_action: string;
  parameters?: Record<string, any>;
}
