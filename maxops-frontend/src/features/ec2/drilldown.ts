import type { Ec2OverviewInstance } from '@/services/inventory';
import { formatSharedTagSelectionLabel, matchesSharedTagSelections, type SharedTagLogic } from '@/utils/sharedOverviewFilters';

export type Ec2CollectionSortKey = 'cpu' | 'memory' | 'sent' | 'received' | 'monthlyCost';

export interface Ec2CollectionFilters {
  search: string;
  environments: string[];
  tags: string[];
  tagLogic: SharedTagLogic;
  states: string[];
  regions: string[];
  instanceTypes: string[];
  findings: string[];
  actionable: boolean;
  hasSavings: boolean;
  lowCpu: boolean;
  lowMemory: boolean;
  sortBy?: Ec2CollectionSortKey;
}

export const EMPTY_EC2_COLLECTION_FILTERS: Ec2CollectionFilters = {
  search: '',
  environments: [],
  tags: [],
  tagLogic: 'and',
  states: [],
  regions: [],
  instanceTypes: [],
  findings: [],
  actionable: false,
  hasSavings: false,
  lowCpu: false,
  lowMemory: false,
};

const normalize = (value: string | null | undefined) => (value || '').trim().toLowerCase();

export const parseEc2CollectionFilters = (searchParams: URLSearchParams): Ec2CollectionFilters => ({
  search: searchParams.get('search')?.trim() || '',
  environments: searchParams.getAll('env').map(normalize).filter(Boolean),
  tags: searchParams.getAll('tag').map((value) => value.trim()).filter(Boolean),
  tagLogic: searchParams.get('tagLogic') === 'or' ? 'or' : 'and',
  states: searchParams.getAll('state').map(normalize).filter(Boolean),
  regions: searchParams.getAll('region').map(normalize).filter(Boolean),
  instanceTypes: searchParams.getAll('type').map(normalize).filter(Boolean),
  findings: searchParams.getAll('finding').map(normalize).filter(Boolean),
  actionable: searchParams.get('actionable') === '1',
  hasSavings: searchParams.get('hasSavings') === '1',
  lowCpu: searchParams.get('lowCpu') === '1',
  lowMemory: searchParams.get('lowMemory') === '1',
  sortBy: (searchParams.get('sort') as Ec2CollectionSortKey | null) || undefined,
});

export const buildEc2CollectionSearch = (filters: Ec2CollectionFilters): string => {
  const params = new URLSearchParams();

  if (filters.search.trim()) {
    params.set('search', filters.search.trim());
  }
  filters.environments.forEach((value) => params.append('env', value));
  filters.tags.forEach((value) => params.append('tag', value));
  if (filters.tagLogic === 'or') {
    params.set('tagLogic', 'or');
  }
  filters.states.forEach((value) => params.append('state', value));
  filters.regions.forEach((value) => params.append('region', value));
  filters.instanceTypes.forEach((value) => params.append('type', value));
  filters.findings.forEach((value) => params.append('finding', value));

  if (filters.actionable) {
    params.set('actionable', '1');
  }
  if (filters.hasSavings) {
    params.set('hasSavings', '1');
  }
  if (filters.lowCpu) {
    params.set('lowCpu', '1');
  }
  if (filters.lowMemory) {
    params.set('lowMemory', '1');
  }
  if (filters.sortBy) {
    params.set('sort', filters.sortBy);
  }

  const query = params.toString();
  return query ? `?${query}` : '';
};

export const mergeEc2CollectionFilters = (
  base: Ec2CollectionFilters,
  next: Partial<Ec2CollectionFilters>
): Ec2CollectionFilters => ({
  search: next.search !== undefined ? next.search : base.search,
  environments: next.environments !== undefined ? next.environments : base.environments,
  tags: next.tags !== undefined ? next.tags : base.tags,
  tagLogic: next.tagLogic !== undefined ? next.tagLogic : base.tagLogic,
  states: next.states !== undefined ? next.states : base.states,
  regions: next.regions !== undefined ? next.regions : base.regions,
  instanceTypes: next.instanceTypes !== undefined ? next.instanceTypes : base.instanceTypes,
  findings: next.findings !== undefined ? next.findings : base.findings,
  actionable: next.actionable !== undefined ? next.actionable : base.actionable,
  hasSavings: next.hasSavings !== undefined ? next.hasSavings : base.hasSavings,
  lowCpu: next.lowCpu !== undefined ? next.lowCpu : base.lowCpu,
  lowMemory: next.lowMemory !== undefined ? next.lowMemory : base.lowMemory,
  sortBy: Object.prototype.hasOwnProperty.call(next, 'sortBy') ? next.sortBy : base.sortBy,
});

export const applyEc2CollectionFilters = (
  instances: Ec2OverviewInstance[],
  filters: Ec2CollectionFilters
): Ec2OverviewInstance[] => {
  const filtered = instances.filter((instance) => {
    const search = filters.search.trim().toLowerCase();
    const environment = normalize(instance.tags.env || 'unknown');
    const state = normalize(instance.state || 'unknown');
    const region = normalize(instance.region || 'unknown');
    const instanceType = normalize(instance.instance_type || 'unknown');
    const finding = normalize(instance.maxops.finding_type || 'healthy');

    if (search) {
      const haystacks = [
        instance.resource_id,
        instance.resource_name,
        instance.region,
        instance.instance_type,
        instance.state,
        instance.maxops.finding_type,
        instance.maxops.title,
        ...Object.entries(instance.tags || {}).flatMap(([key, value]) => [key, `${key}:${value}`, String(value)]),
      ]
        .filter(Boolean)
        .map((value) => String(value).toLowerCase());

      if (!haystacks.some((value) => value.includes(search))) {
        return false;
      }
    }

    if (filters.environments.length > 0 && !filters.environments.includes(environment)) {
      return false;
    }

    if (!matchesSharedTagSelections(instance.tags, filters.tags, filters.tagLogic)) {
      return false;
    }

    if (filters.states.length > 0 && !filters.states.includes(state)) {
      return false;
    }

    if (filters.regions.length > 0 && !filters.regions.includes(region)) {
      return false;
    }

    if (filters.instanceTypes.length > 0 && !filters.instanceTypes.includes(instanceType)) {
      return false;
    }

    if (filters.findings.length > 0 && !filters.findings.includes(finding)) {
      return false;
    }

    if (filters.actionable && normalize(instance.maxops.status) !== 'actionable') {
      return false;
    }

    if (filters.hasSavings && Number(instance.maxops.potential_savings_yearly || 0) <= 0) {
      return false;
    }

    if (filters.lowCpu && !(state === 'running' && instance.usage.cpu_utilization <= 10)) {
      return false;
    }

    if (filters.lowMemory && !(state === 'running' && instance.usage.memory_utilization <= 18)) {
      return false;
    }

    return true;
  });

  if (!filters.sortBy) {
    return filtered;
  }

  const sorted = [...filtered];
  sorted.sort((left, right) => {
    switch (filters.sortBy) {
      case 'cpu':
        return right.usage.cpu_utilization - left.usage.cpu_utilization;
      case 'memory':
        return right.usage.memory_utilization - left.usage.memory_utilization;
      case 'sent':
        return right.usage.sent_bytes_mb - left.usage.sent_bytes_mb;
      case 'received':
        return right.usage.received_bytes_mb - left.usage.received_bytes_mb;
      case 'monthlyCost':
        return Number(right.metadata.monthly_cost_estimate || 0) - Number(left.metadata.monthly_cost_estimate || 0);
      default:
        return 0;
    }
  });
  return sorted;
};

export const getEc2CollectionTitle = (filters: Ec2CollectionFilters): string => {
  if (filters.lowCpu) {
    return 'Instances with low CPU utilization';
  }
  if (filters.lowMemory) {
    return 'Instances with low memory utilization';
  }
  if (filters.actionable) {
    return 'Actionable EC2 instances';
  }
  if (filters.hasSavings) {
    return 'EC2 instances with potential savings';
  }
  if (filters.states.length === 1) {
    return `${filters.states[0].toUpperCase()} EC2 instances`;
  }
  if (filters.regions.length === 1) {
    return `EC2 instances in ${filters.regions[0]}`;
  }
  if (filters.instanceTypes.length === 1) {
    return `${filters.instanceTypes[0]} instances`;
  }
  if (filters.findings.length === 1) {
    return `${filters.findings[0]} findings`;
  }
  if (filters.environments.length === 1) {
    return `${filters.environments[0]} environment instances`;
  }
  return 'EC2 instance collection';
};

export const getEc2CollectionScopeChips = (filters: Ec2CollectionFilters): string[] => {
  const chips: string[] = [];

  if (filters.search.trim()) {
    chips.push(`Search: ${filters.search.trim()}`);
  }
  filters.environments.forEach((value) => chips.push(`Env: ${value}`));
  if (filters.tags.length > 0) {
    chips.push(filters.tagLogic === 'or' ? 'Tags: Match any' : 'Tags: Match all');
  }
  filters.tags.forEach((value) => chips.push(`Tag: ${formatSharedTagSelectionLabel(value)}`));
  filters.states.forEach((value) => chips.push(`State: ${value}`));
  filters.regions.forEach((value) => chips.push(`Region: ${value}`));
  filters.instanceTypes.forEach((value) => chips.push(`Type: ${value}`));
  filters.findings.forEach((value) => chips.push(`Finding: ${value}`));

  if (filters.actionable) {
    chips.push('Actionable');
  }
  if (filters.hasSavings) {
    chips.push('Potential savings');
  }
  if (filters.lowCpu) {
    chips.push('Low CPU');
  }
  if (filters.lowMemory) {
    chips.push('Low memory');
  }
  if (filters.sortBy) {
    chips.push(`Sorted by ${filters.sortBy}`);
  }

  return chips;
};
