import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';

import type { OptimizationProfile } from '@/utils/optimizationProfile';

const STORAGE_KEY = 'maxops.optimization-profile';

type OptimizationProfileContextValue = {
  profile: OptimizationProfile;
  setProfile: (profile: OptimizationProfile) => void;
};

const OptimizationProfileContext = createContext<OptimizationProfileContextValue | undefined>(undefined);

const getInitialProfile = (): OptimizationProfile => {
  if (typeof window === 'undefined') {
    return 'normal';
  }

  const storedValue = window.localStorage.getItem(STORAGE_KEY);
  if (storedValue === 'conservative' || storedValue === 'normal' || storedValue === 'aggressive') {
    return storedValue;
  }

  return 'normal';
};

export const OptimizationProfileProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [profile, setProfileState] = useState<OptimizationProfile>(getInitialProfile);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, profile);
  }, [profile]);

  const value = useMemo(
    () => ({
      profile,
      setProfile: setProfileState,
    }),
    [profile]
  );

  return <OptimizationProfileContext.Provider value={value}>{children}</OptimizationProfileContext.Provider>;
};

export const useOptimizationProfile = (): OptimizationProfileContextValue => {
  const context = useContext(OptimizationProfileContext);
  if (!context) {
    throw new Error('useOptimizationProfile must be used within OptimizationProfileProvider');
  }
  return context;
};
