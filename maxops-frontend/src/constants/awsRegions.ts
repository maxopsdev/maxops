export interface AwsRegionOption {
  value: string;
  label: string;
  group: string;
}

export const AWS_REGION_OPTIONS: AwsRegionOption[] = [
  { value: 'us-east-1', label: 'US East (N. Virginia)', group: 'US' },
  { value: 'us-east-2', label: 'US East (Ohio)', group: 'US' },
  { value: 'us-west-1', label: 'US West (N. California)', group: 'US' },
  { value: 'us-west-2', label: 'US West (Oregon)', group: 'US' },
  { value: 'af-south-1', label: 'Africa (Cape Town)', group: 'Africa' },
  { value: 'ap-east-1', label: 'Asia Pacific (Hong Kong)', group: 'Asia Pacific' },
  { value: 'ap-east-2', label: 'Asia Pacific (Taipei)', group: 'Asia Pacific' },
  { value: 'ap-south-1', label: 'Asia Pacific (Mumbai)', group: 'Asia Pacific' },
  { value: 'ap-south-2', label: 'Asia Pacific (Hyderabad)', group: 'Asia Pacific' },
  { value: 'ap-southeast-1', label: 'Asia Pacific (Singapore)', group: 'Asia Pacific' },
  { value: 'ap-southeast-2', label: 'Asia Pacific (Sydney)', group: 'Asia Pacific' },
  { value: 'ap-southeast-3', label: 'Asia Pacific (Jakarta)', group: 'Asia Pacific' },
  { value: 'ap-southeast-4', label: 'Asia Pacific (Melbourne)', group: 'Asia Pacific' },
  { value: 'ap-southeast-5', label: 'Asia Pacific (Malaysia)', group: 'Asia Pacific' },
  { value: 'ap-southeast-6', label: 'Asia Pacific (New Zealand)', group: 'Asia Pacific' },
  { value: 'ap-southeast-7', label: 'Asia Pacific (Thailand)', group: 'Asia Pacific' },
  { value: 'ap-northeast-1', label: 'Asia Pacific (Tokyo)', group: 'Asia Pacific' },
  { value: 'ap-northeast-2', label: 'Asia Pacific (Seoul)', group: 'Asia Pacific' },
  { value: 'ap-northeast-3', label: 'Asia Pacific (Osaka)', group: 'Asia Pacific' },
  { value: 'ca-central-1', label: 'Canada (Central)', group: 'Canada' },
  { value: 'ca-west-1', label: 'Canada West (Calgary)', group: 'Canada' },
  { value: 'cn-north-1', label: 'China (Beijing)', group: 'China' },
  { value: 'cn-northwest-1', label: 'China (Ningxia)', group: 'China' },
  { value: 'eu-central-1', label: 'Europe (Frankfurt)', group: 'Europe' },
  { value: 'eu-central-2', label: 'Europe (Zurich)', group: 'Europe' },
  { value: 'eu-west-1', label: 'Europe (Ireland)', group: 'Europe' },
  { value: 'eu-west-2', label: 'Europe (London)', group: 'Europe' },
  { value: 'eu-west-3', label: 'Europe (Paris)', group: 'Europe' },
  { value: 'eu-south-1', label: 'Europe (Milan)', group: 'Europe' },
  { value: 'eu-south-2', label: 'Europe (Spain)', group: 'Europe' },
  { value: 'eu-north-1', label: 'Europe (Stockholm)', group: 'Europe' },
  { value: 'il-central-1', label: 'Israel (Tel Aviv)', group: 'Middle East' },
  { value: 'me-central-1', label: 'Middle East (UAE)', group: 'Middle East' },
  { value: 'me-south-1', label: 'Middle East (Bahrain)', group: 'Middle East' },
  { value: 'mx-central-1', label: 'Mexico (Central)', group: 'Americas' },
  { value: 'sa-east-1', label: 'South America (Sao Paulo)', group: 'Americas' },
  { value: 'us-gov-east-1', label: 'AWS GovCloud (US-East)', group: 'GovCloud' },
  { value: 'us-gov-west-1', label: 'AWS GovCloud (US-West)', group: 'GovCloud' },
];

export const AWS_REGION_VALUES = AWS_REGION_OPTIONS.map((region) => region.value);
