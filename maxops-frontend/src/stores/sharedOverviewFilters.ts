import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface SavedSharedOverviewFilter {
  id: string;
  name: string;
  region: string;
  environment: string;
  tagQuery: string;
  tagLogic?: 'and' | 'or';
  createdAt: string;
}

interface SharedOverviewFiltersState {
  region: string;
  environment: string;
  tagQuery: string;
  tagLogic: 'and' | 'or';
  activeSavedFilterId: string | null;
  savedFilters: SavedSharedOverviewFilter[];
  setRegion: (region: string) => void;
  setEnvironment: (environment: string) => void;
  setTagQuery: (tagQuery: string) => void;
  setTagLogic: (tagLogic: 'and' | 'or') => void;
  applySavedFilter: (filterId: string) => void;
  saveCurrentFilter: (name: string) => void;
  clearActiveSavedFilter: () => void;
  reset: () => void;
}

const DEFAULT_SHARED_OVERVIEW_FILTERS = {
  region: 'all',
  environment: 'all',
  tagQuery: '',
  tagLogic: 'and' as const,
};

export const useSharedOverviewFilters = create<SharedOverviewFiltersState>()(
  persist(
    (set, get) => ({
      ...DEFAULT_SHARED_OVERVIEW_FILTERS,
      activeSavedFilterId: null,
      savedFilters: [],
      setRegion: (region) => set({ region, activeSavedFilterId: null }),
      setEnvironment: (environment) => set({ environment, activeSavedFilterId: null }),
      setTagQuery: (tagQuery) => set({ tagQuery, activeSavedFilterId: null }),
      setTagLogic: (tagLogic) => set({ tagLogic, activeSavedFilterId: null }),
      applySavedFilter: (filterId) => {
        if (!filterId) {
          set({ activeSavedFilterId: null });
          return;
        }

        const savedFilter = get().savedFilters.find((filter) => filter.id === filterId);
        if (!savedFilter) {
          return;
        }

        set({
          region: savedFilter.region,
          environment: savedFilter.environment,
          tagQuery: savedFilter.tagQuery,
          tagLogic: savedFilter.tagLogic || 'and',
          activeSavedFilterId: savedFilter.id,
        });
      },
      saveCurrentFilter: (name) => {
        const trimmedName = name.trim();
        if (!trimmedName) {
          return;
        }

        const savedFilter: SavedSharedOverviewFilter = {
          id: `shared-filter-${Date.now()}`,
          name: trimmedName,
          region: get().region,
          environment: get().environment,
          tagQuery: get().tagQuery,
          tagLogic: get().tagLogic,
          createdAt: new Date().toISOString(),
        };

        set((state) => ({
          savedFilters: [savedFilter, ...state.savedFilters],
          activeSavedFilterId: savedFilter.id,
        }));
      },
      clearActiveSavedFilter: () => set({ activeSavedFilterId: null }),
      reset: () =>
        set({
          ...DEFAULT_SHARED_OVERVIEW_FILTERS,
          activeSavedFilterId: null,
        }),
    }),
    {
      name: 'maxops-shared-overview-filters',
      partialize: (state) => ({
        region: state.region,
        environment: state.environment,
        tagQuery: state.tagQuery,
        tagLogic: state.tagLogic,
        activeSavedFilterId: state.activeSavedFilterId,
        savedFilters: state.savedFilters,
      }),
    }
  )
);
