type ElasticacheAction = 'Downsize' | 'Migrate to Graviton node type' | string;

// List derived from AWS ElastiCache supported node types (Redis/Valkey).
// Keep this aligned with AWS docs when new node types are released.
export const ELASTICACHE_NODE_TYPES: string[] = [
  'cache.t4g.micro',
  'cache.t4g.small',
  'cache.t4g.medium',
  'cache.t3.micro',
  'cache.t3.small',
  'cache.t3.medium',
  'cache.m7g.large',
  'cache.m7g.xlarge',
  'cache.m7g.2xlarge',
  'cache.m7g.4xlarge',
  'cache.m7g.8xlarge',
  'cache.m7g.12xlarge',
  'cache.m7g.16xlarge',
  'cache.m6g.large',
  'cache.m6g.xlarge',
  'cache.m6g.2xlarge',
  'cache.m6g.4xlarge',
  'cache.m6g.8xlarge',
  'cache.m6g.12xlarge',
  'cache.m6g.16xlarge',
  'cache.m5.large',
  'cache.m5.xlarge',
  'cache.m5.2xlarge',
  'cache.m5.4xlarge',
  'cache.m5.12xlarge',
  'cache.m5.24xlarge',
  'cache.r7g.large',
  'cache.r7g.xlarge',
  'cache.r7g.2xlarge',
  'cache.r7g.4xlarge',
  'cache.r7g.8xlarge',
  'cache.r7g.12xlarge',
  'cache.r7g.16xlarge',
  'cache.r6g.large',
  'cache.r6g.xlarge',
  'cache.r6g.2xlarge',
  'cache.r6g.4xlarge',
  'cache.r6g.8xlarge',
  'cache.r6g.12xlarge',
  'cache.r6g.16xlarge',
  'cache.r6gd.xlarge',
  'cache.r6gd.2xlarge',
  'cache.r6gd.4xlarge',
  'cache.r6gd.8xlarge',
  'cache.r6gd.12xlarge',
  'cache.r6gd.16xlarge',
  'cache.r5.large',
  'cache.r5.xlarge',
  'cache.r5.2xlarge',
  'cache.r5.4xlarge',
  'cache.r5.12xlarge',
  'cache.r5.24xlarge',
  'cache.c7g.large',
  'cache.c7g.xlarge',
  'cache.c7g.2xlarge',
  'cache.c7g.4xlarge',
  'cache.c7g.8xlarge',
  'cache.c7g.12xlarge',
  'cache.c7g.16xlarge',
  'cache.c6g.large',
  'cache.c6g.xlarge',
  'cache.c6g.2xlarge',
  'cache.c6g.4xlarge',
  'cache.c6g.8xlarge',
  'cache.c6g.12xlarge',
  'cache.c6g.16xlarge',
  'cache.c5.large',
  'cache.c5.xlarge',
  'cache.c5.2xlarge',
  'cache.c5.4xlarge',
  'cache.c5.9xlarge',
  'cache.c5.12xlarge',
  'cache.c5.18xlarge',
  'cache.c5.24xlarge',
];

const NODE_SIZE_ORDER = [
  'micro',
  'small',
  'medium',
  'large',
  'xlarge',
  '2xlarge',
  '4xlarge',
  '8xlarge',
  '12xlarge',
  '16xlarge',
  '24xlarge',
  '32xlarge',
];

const parseNodeType = (nodeType?: string) => {
  if (!nodeType) return null;
  const parts = nodeType.split('.');
  if (parts.length < 3) return null;
  return {
    family: parts[1],
    size: parts.slice(2).join('.'),
  };
};

const familyBase = (family: string): string =>
  family.replace(/\d+/g, '').replace('g', '');

const familyGeneration = (family: string): number | null => {
  const match = family.match(/\d+/);
  if (!match) return null;
  const value = Number(match[0]);
  return Number.isFinite(value) ? value : null;
};

export const getElasticacheNodeTypeOptions = (action?: ElasticacheAction): string[] => {
  if (action === 'Migrate to Graviton node type') {
    return ELASTICACHE_NODE_TYPES.filter((type) => type.includes('g.'));
  }
  return ELASTICACHE_NODE_TYPES;
};

export const getPreferredElasticacheNodeType = (
  action: ElasticacheAction,
  currentNodeType?: string,
  options: string[] = ELASTICACHE_NODE_TYPES
): string => {
  const parsed = parseNodeType(currentNodeType);
  if (!parsed) return '';

  if (action === 'Downsize') {
    const sizeIndex = NODE_SIZE_ORDER.indexOf(parsed.size);
    if (sizeIndex <= 0) return '';
    const targetSize = NODE_SIZE_ORDER[sizeIndex - 1];
    const prefix = `cache.${parsed.family}.`;
    const match = options.find(
      (nodeType) => nodeType.startsWith(prefix) && nodeType.endsWith(`.${targetSize}`)
    );
    return match || '';
  }

  if (action === 'Migrate to Graviton node type') {
    const currentBase = familyBase(parsed.family);
    const currentGen = familyGeneration(parsed.family);
    const candidates = options
      .map((nodeType) => ({ nodeType, parsed: parseNodeType(nodeType) }))
      .filter((entry) => {
        if (!entry.parsed) return false;
        if (entry.parsed.size !== parsed.size) return false;
        if (!entry.parsed.family.includes('g')) return false;
        return familyBase(entry.parsed.family) === currentBase;
      });
    if (!candidates.length) return '';
    if (currentGen === null) return candidates[0].nodeType;
    candidates.sort((a, b) => {
      const genA = familyGeneration(a.parsed!.family) ?? 0;
      const genB = familyGeneration(b.parsed!.family) ?? 0;
      return Math.abs(genA - currentGen) - Math.abs(genB - currentGen);
    });
    return candidates[0].nodeType;
  }

  return '';
};
