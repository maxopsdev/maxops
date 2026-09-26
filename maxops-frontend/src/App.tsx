import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery } from 'react-query';
import { OptimizationProfileProvider } from './contexts/OptimizationProfileContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { DashboardPage } from './pages/Dashboard';
import { Ec2CollectionPage } from './pages/Ec2Collection';
import { Ec2InstanceDetailsPage } from './pages/Ec2InstanceDetails';
import { Ec2OverviewPage } from './pages/Ec2Overview';
import { AsgOverviewPage } from './pages/AsgOverview';
import { AsgInstanceDetailsPage } from './pages/AsgInstanceDetails';
import { RightsizerDetailPage, RightsizerPage } from './pages/Rightsizer';
import { DynamoDbOverviewPage } from './pages/DynamoDbOverview';
import { DynamoDbResourceDetailsPage } from './pages/DynamoDbResourceDetails';
import { EbsOverviewPage } from './pages/EbsOverview';
import { EbsVolumeDetailsPage } from './pages/EbsVolumeDetails';
import { ElasticacheOverviewPage } from './pages/ElasticacheOverview';
import { ElasticacheResourceDetailsPage } from './pages/ElasticacheResourceDetails';
import { RdsInstanceDetailsPage } from './pages/RdsInstanceDetails';
import { RdsInstanceMetricsPage } from './pages/RdsInstanceMetrics';
import { RdsOverviewPage } from './pages/RdsOverview';
import { S3BucketDetailsPage } from './pages/S3BucketDetails';
import { S3OverviewPage } from './pages/S3Overview';
import { CheckDetailsPage } from './pages/CheckDetails';
import { ChecksManagerPage } from './pages/ChecksManager';
import { NotificationsPage } from './pages/Notifications';
import { SecurityPage } from './pages/Security';
import { SettingsPage } from './pages/Settings';
import { CostDataSetupPage } from './pages/CostDataSetup';
import { OnboardingPage } from './pages/Onboarding';
import { settingsApi } from './services/settings';
import type { UserSettings } from './types/api';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const getResumeOnboardingStep = (settings?: UserSettings | null) => {
  const step = settings?.onboarding_step;
  return step === 'information' || step === 'role' || step === 'account' ? step : 'information';
};

function AppRoutes() {
  const { data: onboardingStatus, isLoading } = useQuery(
    'onboarding-status',
    () => settingsApi.getOnboardingStatus(),
    {
      retry: false,
    }
  );

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  // Show onboarding if not completed
  if (!onboardingStatus || !onboardingStatus.onboarding_completed) {
    const resumeStep = getResumeOnboardingStep(onboardingStatus);
    return (
      <Routes>
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="*" element={<Navigate to={`/onboarding?step=${resumeStep}`} replace />} />
      </Routes>
    );
  }

  const hasConfiguredScope =
    Boolean(onboardingStatus.account?.trim()) &&
    Boolean(onboardingStatus.region?.trim());
  if (!hasConfiguredScope) {
    return (
      <Routes>
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="*" element={<Navigate to="/onboarding?step=account" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/rightsizer" element={<RightsizerPage />} />
      <Route path="/rightsizer/:resourceType/:inventoryId" element={<RightsizerDetailPage />} />
      <Route path="/dashboard/resources/ec2" element={<Ec2OverviewPage />} />
      <Route path="/dashboard/resources/asg" element={<AsgOverviewPage />} />
      <Route path="/dashboard/resources/asg/instances/:instanceId" element={<AsgInstanceDetailsPage />} />
      <Route path="/dashboard/resources/ec2/collection" element={<Ec2CollectionPage />} />
      <Route path="/dashboard/resources/ec2/instances/:instanceId" element={<Ec2InstanceDetailsPage />} />
      <Route path="/dashboard/resources/dynamodb" element={<DynamoDbOverviewPage />} />
      <Route path="/dashboard/resources/dynamodb/resources/:resourceId" element={<DynamoDbResourceDetailsPage />} />
      <Route path="/dashboard/resources/ebs" element={<EbsOverviewPage />} />
      <Route path="/dashboard/resources/ebs/volumes/:volumeId" element={<EbsVolumeDetailsPage />} />
      <Route path="/dashboard/resources/rds" element={<RdsOverviewPage />} />
      <Route path="/dashboard/resources/rds/instances/:instanceId" element={<RdsInstanceDetailsPage />} />
      <Route path="/dashboard/resources/rds/instances/:instanceId/workload" element={<RdsInstanceMetricsPage />} />
      <Route path="/dashboard/resources/s3" element={<S3OverviewPage />} />
      <Route path="/dashboard/resources/s3/buckets/:bucketId" element={<S3BucketDetailsPage />} />
      <Route path="/dashboard/resources/elasticache" element={<ElasticacheOverviewPage />} />
      <Route path="/dashboard/resources/elasticache/resources/:resourceId" element={<ElasticacheResourceDetailsPage />} />
      <Route path="/dashboard/checks/:checkId" element={<CheckDetailsPage />} />
      <Route path="/policies" element={<ChecksManagerPage />} />
      <Route path="/notifications" element={<NotificationsPage />} />
      <Route path="/security" element={<SecurityPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/settings/cost-data" element={<CostDataSetupPage />} />
      <Route path="/onboarding" element={<OnboardingPage />} />
    </Routes>
  );
}

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <OptimizationProfileProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </OptimizationProfileProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;
