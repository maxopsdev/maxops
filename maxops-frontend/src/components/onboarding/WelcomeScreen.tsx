import React from 'react';
import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { ArrowRight, Database, Shield, TrendingDown, Zap } from 'lucide-react';
import {
  OnboardingStepIndicator,
  type OnboardingStepItem,
} from '@/components/onboarding/OnboardingStepIndicator';
import type { PricingDatabaseStatus } from '@/types/api';

interface WelcomeScreenProps {
  steps: readonly OnboardingStepItem[];
  currentStepId: string;
  onContinue: () => void;
  pricingDatabaseStatus?: PricingDatabaseStatus;
  isUnpackingPricingDatabase?: boolean;
  onUnpackPricingDatabase: () => void;
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({
  steps,
  currentStepId,
  onContinue,
  pricingDatabaseStatus,
  isUnpackingPricingDatabase = false,
  onUnpackPricingDatabase,
}) => {
  const pricingReady = pricingDatabaseStatus?.ready;
  const artifactAvailable = pricingDatabaseStatus?.state === 'artifact_available';

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 to-primary-100 dark:from-gray-900 dark:to-gray-950 flex items-center justify-center p-6">
      <Card className="max-w-4xl w-full">
        <div className="p-8 space-y-8">
          <OnboardingStepIndicator steps={steps} currentStepId={currentStepId} />

          <div className="text-center space-y-4">
            <img
              src="/maxops-mark.svg"
              alt=""
              width={80}
              height={80}
              className="w-20 h-20 rounded-2xl mx-auto"
            />
            <h1 className="text-4xl font-bold text-gray-900 dark:text-white">
              Welcome to MaxOps
            </h1>
            <p className="text-xl text-gray-600 dark:text-gray-400">
              Your FinOps Cost Optimization Platform
            </p>
          </div>

          {/* Features */}
          <div className="grid md:grid-cols-3 gap-6">
            <div className="text-center space-y-3">
              <div className="w-16 h-16 bg-primary-100 dark:bg-primary-900/30 rounded-full flex items-center justify-center mx-auto">
                <Zap className="text-primary-600 dark:text-primary-400" size={32} />
              </div>
              <h3 className="font-semibold text-gray-900 dark:text-white">Automated Optimization</h3>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Automatically identify and optimize underutilized AWS resources to reduce costs
              </p>
            </div>

            <div className="text-center space-y-3">
              <div className="w-16 h-16 bg-primary-100 dark:bg-primary-900/30 rounded-full flex items-center justify-center mx-auto">
                <Shield className="text-primary-600 dark:text-primary-400" size={32} />
              </div>
              <h3 className="font-semibold text-gray-900 dark:text-white">Policy-Based</h3>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Create and manage policies to enforce cost optimization rules across your infrastructure
              </p>
            </div>

            <div className="text-center space-y-3">
              <div className="w-16 h-16 bg-primary-100 dark:bg-primary-900/30 rounded-full flex items-center justify-center mx-auto">
                <TrendingDown className="text-primary-600 dark:text-primary-400" size={32} />
              </div>
              <h3 className="font-semibold text-gray-900 dark:text-white">Cost Savings</h3>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Track and visualize cost savings from your optimization efforts
              </p>
            </div>
          </div>

          {/* Description */}
          <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-6 space-y-4">
            <h2 className="font-semibold text-gray-900 dark:text-white">About MaxOps</h2>
            <p className="text-gray-700 dark:text-gray-300 leading-relaxed">
              MaxOps is a comprehensive FinOps platform designed to help you optimize your AWS infrastructure costs.
              Our platform automatically scans your resources, identifies optimization opportunities, and provides
              actionable recommendations to reduce spending while maintaining performance and reliability.
            </p>
            <p className="text-gray-700 dark:text-gray-300 leading-relaxed">
              Get started by configuring your environment and running our comprehensive checks to discover
              cost optimization opportunities across your AWS infrastructure.
            </p>
          </div>

          <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white/80 dark:bg-gray-900/60 p-6 space-y-4">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-primary-100 dark:bg-primary-900/30 flex items-center justify-center shrink-0">
                <Database className="text-primary-600 dark:text-primary-400" size={20} />
              </div>
              <div className="space-y-2">
                <div>
                  <h2 className="font-semibold text-gray-900 dark:text-white">Pricing Database</h2>
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    Pricing data ships in this repository as a bundled artifact and is only extracted when you explicitly choose to do it.
                  </p>
                </div>
                <p className="text-sm text-gray-700 dark:text-gray-300">
                  {pricingDatabaseStatus?.message ?? 'Checking bundled pricing database status...'}
                </p>
                {pricingDatabaseStatus && (
                  <div className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                    <div>Artifact: {pricingDatabaseStatus.artifact_path}</div>
                    <div>Database: {pricingDatabaseStatus.db_path}</div>
                  </div>
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              {!pricingReady && artifactAvailable && (
                <Button
                  variant="secondary"
                  onClick={onUnpackPricingDatabase}
                  isLoading={isUnpackingPricingDatabase}
                >
                  Extract Pricing Database
                </Button>
              )}
              {pricingReady && (
                <div className="inline-flex items-center rounded-md bg-success-50 px-3 py-2 text-sm font-medium text-success-700 dark:bg-success-900/30 dark:text-success-300">
                  Pricing database ready
                </div>
              )}
            </div>
          </div>

          <div className="flex justify-center pt-4">
            <Button
              variant="primary"
              size="lg"
              onClick={onContinue}
              className="px-8 py-3 text-lg"
            >
              Get Started
              <ArrowRight className="ml-2" size={20} />
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
};
