import React from 'react';
import { Link } from 'react-router-dom';
import { Settings } from 'lucide-react';
import { ThemeToggle } from '@/components/common/ThemeToggle';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { OPTIMIZATION_PROFILE_OPTIONS } from '@/utils/optimizationProfile';

export const Header: React.FC = () => {
  const { profile, setProfile } = useOptimizationProfile();

  return (
    <header className="bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 fixed top-0 left-0 right-0 z-40 ml-64">
      <div className="px-6 py-4 flex items-center justify-between">
        <Link to="/dashboard" className="flex items-center space-x-2">
          <img
            src="/maxops-mark.svg"
            alt=""
            width={32}
            height={32}
            className="w-8 h-8 rounded-lg"
          />
          <span className="text-xl font-bold text-gray-900 dark:text-white">MaxOps</span>
        </Link>
        <nav className="flex items-center space-x-4">
          <div className="flex items-center gap-2">
            <label htmlFor="optimization-profile" className="text-sm text-gray-600 dark:text-gray-400">
              Optimization Profile
            </label>
            <select
              id="optimization-profile"
              value={profile}
              onChange={(event) => setProfile(event.target.value as typeof profile)}
              className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
            >
              {OPTIMIZATION_PROFILE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <ThemeToggle />
          <Link
            to="/settings"
            className="text-gray-700 dark:text-gray-300 hover:text-primary-600 dark:hover:text-primary-400 transition-colors"
          >
            <Settings size={20} />
          </Link>
        </nav>
      </div>
    </header>
  );
};

