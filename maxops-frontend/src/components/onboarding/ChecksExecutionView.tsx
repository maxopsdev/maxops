import React, { useState, useEffect, useRef } from 'react';
import { XCircle } from 'lucide-react';
import { onboardingApi } from '@/services/settings';
import { CheckExecutionCard } from '@/components/onboarding/CheckExecutionCard';
import type { Check } from '@/types/api';
import apiClient from '@/services/api';

// Add CSS animation
const style = document.createElement('style');
style.textContent = `
  @keyframes slideInFromBottom {
    from {
      transform: translateY(100px);
      opacity: 0;
    }
    to {
      transform: translateY(0);
      opacity: 1;
    }
  }
`;
if (!document.head.querySelector('style[data-check-animation]')) {
  style.setAttribute('data-check-animation', 'true');
  document.head.appendChild(style);
}

const CHECK_MAX_ATTEMPTS = 3;
const CHECK_RETRY_DELAY_MS = 2000;

// Some checks (e.g. S3 lifecycle/policy checks on accounts with dozens of
// buckets) legitimately take longer than a minute -- they're slow, not
// broken. Give real work room to finish before treating it as stuck.
const CHECK_TIMEOUT_MS = 120000;

class CheckTimeoutError extends Error {}

const callCheckOnce = (check: Check): Promise<any> => {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = setTimeout(() => {
      reject(new CheckTimeoutError(`Check execution timeout (${CHECK_TIMEOUT_MS / 1000}s)`));
    }, CHECK_TIMEOUT_MS);
  });

  const apiPromise = apiClient.post(`/checks/${check.check_id}/test`, {
    parameters: check.parameters || {},
  }).finally(() => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }
  });

  return Promise.race([apiPromise, timeoutPromise]);
};

// IAM permissions on a freshly created/updated role propagate across AWS's
// backend non-uniformly -- concurrent requests moments apart can land on
// different nodes, so one check can fail with a transient AccessDenied while
// another (or the same check retried a moment later) succeeds. A handful of
// checks landing in that window used to trip the 3-consecutive-same-error
// circuit breaker below and cancel the entire ~100-check run after only a
// few had a chance to execute. Giving each check a couple of quick retries
// before it's ever counted as a failure absorbs that window; a genuinely
// broken permission still fails consistently across all attempts.
const callCheckWithRetry = async (check: Check): Promise<any> => {
  let lastError: unknown;
  for (let attempt = 1; attempt <= CHECK_MAX_ATTEMPTS; attempt++) {
    try {
      return await callCheckOnce(check);
    } catch (error) {
      lastError = error;
      if (attempt < CHECK_MAX_ATTEMPTS) {
        console.warn(
          `[${new Date().toISOString()}] Check ${check.check_id} attempt ${attempt} failed, retrying in ${CHECK_RETRY_DELAY_MS}ms:`,
          error
        );
        await new Promise((resolve) => setTimeout(resolve, CHECK_RETRY_DELAY_MS));
      }
    }
  }
  throw lastError;
};

interface CheckResult {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  status: 'running' | 'completed' | 'failed';
  resources_found: number;
  error: string | null;
}

interface ChecksExecutionViewProps {
  onComplete: (executionId?: number, error?: string) => void;
}

export const ChecksExecutionView: React.FC<ChecksExecutionViewProps> = ({ onComplete }) => {
  const [checks, setChecks] = useState<CheckResult[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [isCanceled, setIsCanceled] = useState(false);
  const [cancelReason, setCancelReason] = useState<string | null>(null);
  const [totalChecks, setTotalChecks] = useState(0);
  const [, setProgressTick] = useState(0); // forces a re-render each second so the ETA counts down
  const containerRef = useRef<HTMLDivElement>(null);
  const checkRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const hasStartedRef = useRef(false); // Prevent duplicate runs in StrictMode
  const isRunningRef = useRef(false); // Track if checks are running (avoids stale state)
  const runningCheckIdsRef = useRef<Set<string>>(new Set()); // Track which checks are currently executing
  const startTimeRef = useRef<number | null>(null);

  useEffect(() => {
    // Prevent duplicate execution in React StrictMode
    if (hasStartedRef.current) {
      return;
    }
    hasStartedRef.current = true;
    
    runAllChecks();
    
    // Cleanup function to reset if component unmounts
    return () => {
      // Reset flag on unmount so it can run again if component remounts
      hasStartedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!isRunning) {
      return;
    }
    const interval = setInterval(() => setProgressTick((tick) => tick + 1), 1000);
    return () => clearInterval(interval);
  }, [isRunning]);

  useEffect(() => {
    // Scroll to bottom when new check is added
    if (containerRef.current && checks.length > 0) {
      const lastCheck = checkRefs.current.get(checks.length - 1);
      if (lastCheck) {
        setTimeout(() => {
          lastCheck.scrollIntoView({ behavior: 'smooth', block: 'end' });
        }, 500); // Wait for animation to complete
      }
    }
  }, [checks.length]);

  const runAllChecks = async () => {
    // Prevent duplicate execution if already running (using ref to avoid stale state)
    if (isRunningRef.current) {
      console.warn('Checks are already running, skipping duplicate call');
      return;
    }
    
    isRunningRef.current = true;
    setIsRunning(true);
    
    try {
      // First, get all checks
      const response = await apiClient.get('/checks');
      const allChecks: Check[] = response.data;

      console.log(`Found ${allChecks.length} checks to run`);

      // Start with empty array - add checks one by one as they start
      setChecks([]);
      setTotalChecks(allChecks.length);
      startTimeRef.current = Date.now();
      setIsCanceled(false);
      setCancelReason(null);
      runningCheckIdsRef.current.clear(); // Clear any previously running checks
      
      // Track consecutive errors with the same message
      let consecutiveErrorCount = 0;
      let lastErrorMessage: string | null = null;
      let canceled = false;
      
      // Parallel execution with max 3 concurrent checks
      const MAX_CONCURRENT = 3;
      let nextIndex = 0;
      const runningPromises: Promise<void>[] = [];
      const allPromises: Promise<void>[] = []; // Track all promises for logging/debugging

      // `Promise.all()` snapshots its argument array once, synchronously, at
      // call time -- it does NOT pick up items pushed in later. The pool
      // below starts by launching only MAX_CONCURRENT checks and chains the
      // rest reactively via startNextCheck's .finally() handlers, so most of
      // the per-check promises don't exist yet at the moment the wait below begins.
      // A `Promise.all(allPromises)` there would resolve as soon as just the
      // first MAX_CONCURRENT checks settle, treat the run as "done", and mark
      // whatever's still in flight as stuck/failed -- while the real checks
      // keep running disconnected in the background. This promise instead
      // resolves only when the pool itself reports nothing left to start and
      // nothing still running.
      let resolveAllDone: (() => void) | null = null;
      const allDonePromise = new Promise<void>((resolve) => {
        resolveAllDone = resolve;
      });
      const signalIfAllDone = (): void => {
        const nothingLeftToStart = canceled || nextIndex >= allChecks.length;
        if (nothingLeftToStart && runningPromises.length === 0) {
          resolveAllDone?.();
        }
      };

      // Helper function to normalize error messages
      const normalizeError = (msg: string): string => {
        return msg
          .replace(/http[s]?:\/\/[^\s]+/g, '<URL>') // Replace URLs
          .replace(/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/gi, '<UUID>') // Replace UUIDs
          .replace(/localhost:\d+/g, '<HOST>') // Replace localhost:port
          .replace(/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/g, '<IP>') // Replace IPs
          .trim();
      };
      
      // Function to execute a single check
      const executeCheck = async (checkIndex: number): Promise<void> => {
        if (canceled || checkIndex >= allChecks.length) {
          return;
        }
        
        const check = allChecks[checkIndex];
        
        // Prevent duplicate check execution - check if this check is already running
        if (runningCheckIdsRef.current.has(check.check_id)) {
          console.warn(`Check ${check.check_id} is already executing, skipping duplicate`);
          return;
        }
        
        // Mark check as running
        runningCheckIdsRef.current.add(check.check_id);
        
        // Add new check and mark as running (this will trigger slide-in animation)
        setChecks((prev) => {
          const existingCheck = prev.find((c) => c.check_id === check.check_id);
          if (existingCheck) {
            console.warn(`Check ${check.check_id} already in checks array, skipping duplicate`);
            return prev; // Don't add duplicate
          }
          
          const newCheck: CheckResult = {
            check_id: check.check_id,
            name: check.name,
            description: check.description,
            resource_type: check.resource_type,
            status: 'running',
            resources_found: 0,
            error: null,
          };
          
          return [...prev, newCheck];
        });
        
        // Wait for animation to show the new check sliding in
        await new Promise((resolve) => setTimeout(resolve, 600));
        
        try {
          console.log(`[${new Date().toISOString()}] Starting check ${checkIndex + 1}/${allChecks.length}: ${check.check_id}`);

          const testResponse = await callCheckWithRetry(check);

          const result = testResponse.data;
          
          console.log(`[${new Date().toISOString()}] Check ${check.check_id} completed: ${result.resources_found} resources found`);
          
          // Update check status
          setChecks((prev) => {
            const updated = [...prev];
            const checkIdx = updated.findIndex((c) => c.check_id === check.check_id);
            if (checkIdx !== -1) {
              updated[checkIdx] = {
                ...updated[checkIdx],
                status: 'completed',
                resources_found: result.resources_found || 0,
              };
            }
            return updated;
          });
          
          // Reset error counter on success
          consecutiveErrorCount = 0;
          lastErrorMessage = null;
        } catch (error: any) {
          console.error(`[${new Date().toISOString()}] Check ${check.check_id} failed:`, error);
          
          // Extract error message
          let errorMessage = 'Unknown error';
          if (error.response?.data?.detail) {
            errorMessage = typeof error.response.data.detail === 'string' 
              ? error.response.data.detail 
              : JSON.stringify(error.response.data.detail);
          } else if (error.response?.data?.message) {
            errorMessage = error.response.data.message;
          } else if (error.message) {
            errorMessage = error.message;
          }
          
          // A plain client-side timeout just means this particular check took
          // longer than CHECK_TIMEOUT_MS -- often because it's enumerating a
          // lot of resources (e.g. an account with dozens of S3 buckets), not
          // because anything is systemically broken. Several unrelated slow
          // checks can easily produce this identical message back-to-back;
          // that shouldn't cancel the other ~100 checks that are running
          // fine. Only count real AWS/API errors toward the breaker below --
          // those are the actual signal of a systemic problem (e.g. a broad
          // permission gap) worth stopping for.
          const isTimeout = error instanceof CheckTimeoutError;

          const normalizedError = normalizeError(errorMessage);
          const normalizedLastError = lastErrorMessage ? normalizeError(lastErrorMessage) : null;

          if (isTimeout) {
            console.log(`Timeout on ${check.check_id} -- not counted toward the repeated-error circuit breaker`);
          } else if (normalizedError === normalizedLastError) {
            // Check if this is the same error as the previous one (using normalized messages)
            consecutiveErrorCount++;
            console.log(`Same error detected (${consecutiveErrorCount} times): ${normalizedError}`);
          } else {
            // Different error, reset counter
            consecutiveErrorCount = 1;
            lastErrorMessage = errorMessage;
            console.log(`New error detected: ${normalizedError}`);
          }

          // Update check status with error
          setChecks((prev) => {
            const updated = [...prev];
            const checkIdx = updated.findIndex((c) => c.check_id === check.check_id);
            if (checkIdx !== -1) {
              updated[checkIdx] = {
                ...updated[checkIdx],
                status: 'failed',
                error: errorMessage,
              };
            }
            return updated;
          });

          // Cancel if the same non-timeout error occurred 3+ times in a row
          if (!isTimeout && consecutiveErrorCount >= 3) {
            console.error(`Run canceled: Same error occurred ${consecutiveErrorCount} times: ${normalizedError}`);
            canceled = true;
            setIsCanceled(true);
            setCancelReason(`Run canceled: The same error occurred ${consecutiveErrorCount} times in a row: ${errorMessage}`);
            isRunningRef.current = false;
            setIsRunning(false);
            // Don't return here - let the finally block in startNextCheck handle cleanup
          }
        } finally {
          console.log(`[${new Date().toISOString()}] Check ${check.check_id} finally block executing`);
          
          // Remove from running set
          runningCheckIdsRef.current.delete(check.check_id);
          
          // Ensure check is not left in running state if something goes wrong
          setChecks((prev) => {
            const updated = [...prev];
            const checkIdx = updated.findIndex((c) => c.check_id === check.check_id);
            if (checkIdx !== -1) {
              if (updated[checkIdx].status === 'running') {
                // If still running, mark as failed (shouldn't normally happen)
                console.warn(`Check ${check.check_id} was still in running state in finally block, marking as failed`);
                updated[checkIdx] = {
                  ...updated[checkIdx],
                  status: 'failed',
                  error: 'Check execution incomplete - check did not update status',
                };
              } else {
                console.log(`Check ${check.check_id} has final status: ${updated[checkIdx].status}`);
              }
            } else {
              console.warn(`Check ${check.check_id} not found in checks array in finally block`);
            }
            return updated;
          });
        }
      };
      
      // Function to start next check when a slot becomes available
      const startNextCheck = (): void => {
        if (canceled) {
          console.log('startNextCheck: canceled, not starting new check');
          return;
        }
        
        if (nextIndex >= allChecks.length) {
          console.log(`startNextCheck: all checks started (nextIndex=${nextIndex}, total=${allChecks.length})`);
          return;
        }
        
        const checkIndex = nextIndex++;
        console.log(`startNextCheck: starting check at index ${checkIndex} (${nextIndex}/${allChecks.length})`);
        
        const promise = executeCheck(checkIndex).finally(() => {
          console.log(`startNextCheck: check ${checkIndex} finished, checking for next check`);
          
          // Remove this promise from running list (for concurrency control)
          const index = runningPromises.indexOf(promise);
          if (index > -1) {
            runningPromises.splice(index, 1);
          }
          
          // Start next check if available and not canceled
          if (!canceled && nextIndex < allChecks.length) {
            console.log(`startNextCheck: starting next check (${nextIndex}/${allChecks.length})`);
            startNextCheck();
          } else {
            console.log(`startNextCheck: no more checks to start (nextIndex=${nextIndex}, total=${allChecks.length}, canceled=${canceled})`);
            signalIfAllDone();
          }
        });
        runningPromises.push(promise); // For concurrency control
        allPromises.push(promise); // For Promise.all - never remove from here
        console.log(`startNextCheck: promise added, total promises: ${allPromises.length}`);
      };
      
      // Start initial batch of checks (up to MAX_CONCURRENT)
      console.log(`Starting ${Math.min(MAX_CONCURRENT, allChecks.length)} initial checks`);
      for (let i = 0; i < Math.min(MAX_CONCURRENT, allChecks.length); i++) {
        if (!canceled) {
          startNextCheck();
        }
      }
      signalIfAllDone(); // covers the allChecks.length === 0 edge case

      console.log(`Total checks to run: ${allChecks.length}, promises started so far: ${allPromises.length} (more are chained in reactively as each one finishes)`);

      // Set up a periodic check to detect stuck checks (every 10 seconds)
      const stuckCheckInterval = setInterval(() => {
        setChecks((prev) => {
                const stuckChecks = prev.filter((check) => {
            if (check.status === 'running') {
              // Check if this check has been running for more than 70 seconds (10s buffer after 60s timeout)
              // We can't track individual start times easily, so we'll just check periodically
              return true; // Mark all running checks as potentially stuck after interval
            }
            return false;
          });
          
          if (stuckChecks.length > 0) {
            console.warn(`Detected ${stuckChecks.length} potentially stuck checks:`, stuckChecks.map(c => c.check_id));
          }
          
          return prev;
        });
      }, 10000); // Check every 10 seconds
      
      // Wait for all checks to complete -- see allDonePromise above for why
      // this can't be a plain Promise.all(allPromises).
      // Add a timeout to prevent infinite waiting. Generous ceiling: with a
      // 120s per-check timeout and up to 3 attempts each, a handful of
      // genuinely slow checks (e.g. S3 lifecycle checks on an account with
      // dozens of buckets) can legitimately need several minutes; this is a
      // one-time onboarding step, not a tight SLA.
      const ALL_CHECKS_TIMEOUT_MS = 15 * 60 * 1000;
      const allChecksTimeout = new Promise((_, reject) =>
        setTimeout(
          () => reject(new Error(`Overall check execution timeout (${ALL_CHECKS_TIMEOUT_MS / 60000} minutes)`)),
          ALL_CHECKS_TIMEOUT_MS
        )
      );

      try {
        await Promise.race([
          allDonePromise,
          allChecksTimeout
        ]);
        console.log('All checks completed successfully');
      } catch (error: any) {
        console.error('Error waiting for checks:', error);
        // If timeout, mark remaining running checks as failed
        if (error.message?.includes('timeout')) {
          console.warn('Check execution timed out, marking remaining checks as failed');
          setChecks((prev) => {
            return prev.map((check) => {
              if (check.status === 'running') {
                console.warn(`Marking stuck check ${check.check_id} as failed due to timeout`);
                return {
                  ...check,
                  status: 'failed' as const,
                  error: 'Check execution timed out',
                };
              }
              return check;
            });
          });
        }
      }
      
      // Clear the stuck check interval
      clearInterval(stuckCheckInterval);
      
      // Wait a bit for React state updates to complete
      await new Promise((resolve) => setTimeout(resolve, 200));
      
      // Final check: ensure all checks have a final status (not stuck in 'running')
      // Use a callback to get the latest state
      let finalChecks: CheckResult[] = [];
      setChecks((prev) => {
        finalChecks = prev.map((check) => {
          if (check.status === 'running') {
            console.warn(`Check ${check.check_id} was still in running state after all promises completed, marking as failed`);
            return {
              ...check,
              status: 'failed' as const,
              error: 'Check did not complete properly',
            };
          }
          return check;
        });
        return finalChecks;
      });
      
      // Wait one more tick for state update
      await new Promise((resolve) => setTimeout(resolve, 100));
      
      isRunningRef.current = false;
      setIsRunning(false);

      const allFailed =
        finalChecks.length > 0 &&
        finalChecks.every((check) => check.status === 'failed');
      
      // Handle completion - navigate to dashboard
      if (canceled) {
        // Run was canceled - navigate to dashboard with error
        const errorMessage = cancelReason || 'Run was canceled';
        setTimeout(() => {
          onComplete(undefined, errorMessage);
        }, 1000);
      } else if (allFailed) {
        // If everything failed, still save results but navigate to dashboard
        try {
          const resultsToSave = finalChecks.map((check) => ({
            check_id: check.check_id,
            name: check.name,
            description: check.description,
            resource_type: check.resource_type,
            status: 'failed' as const,
            resources_found: check.resources_found,
            error: check.error,
          }));

          await onboardingApi.saveResults(resultsToSave);
        } catch (error) {
          console.error('Failed to save results:', error);
        }

        const errorMessage = 'All checks failed';
        setTimeout(() => {
          onComplete(undefined, errorMessage);
        }, 1000);
      } else {
        // Save results to backend via onboarding API
        try {
          // Prepare results in the format expected by the backend
          const resultsToSave = finalChecks.map((check) => ({
            check_id: check.check_id,
            name: check.name,
            description: check.description,
            resource_type: check.resource_type,
            status: (check.status === 'completed' ? 'completed' : 'failed') as 'completed' | 'failed',
            resources_found: check.resources_found,
            error: check.error,
          }));

          // Save results and get execution ID
          const saveResponse = await onboardingApi.saveResults(resultsToSave);
          setTimeout(() => {
            onComplete(saveResponse.execution_id);
          }, 1000);
        } catch (error: any) {
          console.error('Failed to save results:', error);
          const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to save results';
          // Navigate to dashboard with error
          setTimeout(() => {
            onComplete(undefined, errorMessage);
          }, 1000);
        }
      }
    } catch (error: any) {
      console.error('Failed to run checks:', error);
      isRunningRef.current = false;
      setIsRunning(false);
      
      // Show error in UI
      setChecks((prev) => {
        if (prev.length === 0) {
          return [{
            check_id: 'error',
            name: 'Failed to load checks',
            description: 'Could not retrieve checks from server',
            resource_type: 'error',
            status: 'failed' as const,
            resources_found: 0,
            error: error.response?.data?.detail || error.message || 'Unknown error',
          }];
        }
        return prev;
      });

      const errorMessage =
        error?.response?.data?.detail ||
        error?.message ||
        'Failed to run checks';
      // Navigate to dashboard with error if the run fails before completion
      setTimeout(() => {
        onComplete(undefined, errorMessage);
      }, 1000);
    }
  };

  const finishedCount = checks.filter((c) => c.status !== 'running').length;
  const remainingCount = Math.max(totalChecks - finishedCount, 0);
  const progressPercent = totalChecks > 0 ? Math.round((finishedCount / totalChecks) * 100) : 0;
  let etaLabel: string | null = null;
  if (isRunning && startTimeRef.current && finishedCount >= 2 && remainingCount > 0) {
    const elapsedMs = Date.now() - startTimeRef.current;
    const etaSeconds = Math.round(((elapsedMs / finishedCount) * remainingCount) / 1000);
    etaLabel = etaSeconds >= 60 ? `~${Math.ceil(etaSeconds / 60)} min remaining` : `~${etaSeconds}s remaining`;
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 p-6">
      <div className="max-w-4xl mx-auto space-y-4">
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
            Running Initial Checks
          </h1>
          <p className="text-gray-600 dark:text-gray-400">
            Scanning your AWS infrastructure for optimization opportunities...
          </p>
        </div>

        {totalChecks > 0 && (
          <div className="sticky top-0 z-10 rounded-lg border border-gray-200 bg-white/95 p-4 shadow-sm backdrop-blur dark:border-gray-800 dark:bg-gray-900/95">
            <div className="flex items-center justify-between text-sm font-medium text-gray-700 dark:text-gray-300">
              <span>
                {isRunning
                  ? `Running check ${Math.min(finishedCount + 1, totalChecks)} of ${totalChecks}`
                  : `Finished ${finishedCount} of ${totalChecks} checks`}
              </span>
              {etaLabel && <span className="text-gray-500 dark:text-gray-400">{etaLabel}</span>}
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
              <div
                className="h-full rounded-full bg-primary-500 transition-all duration-500 ease-out"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>
        )}

        <div
          ref={containerRef}
          className="space-y-4 max-h-[calc(100vh-200px)] overflow-y-auto"
        >
          {checks.map((check, index) => (
            <div
              key={check.check_id}
              ref={(el) => {
                if (el) checkRefs.current.set(index, el);
              }}
              className="animate-slide-in"
              style={{
                animation: `slideInFromBottom 0.7s ease-out forwards`,
              }}
            >
              <CheckExecutionCard check={check} />
            </div>
          ))}
        </div>

        {/* Canceled Message */}
        {isCanceled && cancelReason && (
          <div className="bg-danger-50 dark:bg-danger-900/20 border-2 border-danger-500 rounded-lg p-4 mb-4">
            <div className="flex items-center space-x-2">
              <XCircle className="text-danger-600 dark:text-danger-400" size={24} />
              <div>
                <h3 className="font-semibold text-danger-800 dark:text-danger-300">Run Canceled</h3>
                <p className="text-sm text-danger-700 dark:text-danger-400 mt-1">{cancelReason}</p>
              </div>
            </div>
          </div>
        )}

        {!isRunning && checks.length > 0 && (
          <div className="text-center py-4">
            <p className="text-gray-600 dark:text-gray-400">
              {isCanceled ? (
                <>
                  Run was canceled. Completed {checks.filter((c) => c.status === 'completed').length} of {checks.length} checks before cancellation.
                </>
              ) : (
                <>
                  Completed {checks.filter((c) => c.status === 'completed').length} of {checks.length} checks
                </>
              )}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

