// Ignore requests started before an app update, and retain refreshes requested
// while another request is still in flight.
export function createStateFeed({read, onState, onError}) {
  let generation = 0;
  let pending = null;
  let queued = false;
  let reportErrors = false;

  return {
    accept(snapshot) {
      generation += 1;
      onState(snapshot, false);
    },
    refresh(showError = false) {
      queued = true;
      reportErrors ||= showError;
      if (pending) return pending;
      pending = Promise.resolve().then(async () => {
        try {
          while (queued) {
            queued = false;
            const started = generation;
            const show = reportErrors;
            reportErrors = false;
            try {
              const snapshot = await read();
              if (started === generation) onState(snapshot, show);
            } catch (failure) {
              if (started === generation) onError(failure, show);
            }
          }
        } finally {
          pending = null;
        }
      });
      return pending;
    },
  };
}

export function isStateSnapshot(value) {
  return value !== null && typeof value === 'object'
    && ['sessions', 'accounts', 'macros', 'logs'].every(key => Array.isArray(value[key]))
    && Boolean(value.settings && value.proxy);
}
