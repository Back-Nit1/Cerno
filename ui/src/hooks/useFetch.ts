import { useEffect, useState } from "react";

type FetchState<T> = { data: T | null; error: string | null; loading: boolean };

// Deliberately not react-query: two consumers, no caching/refetch needs yet.
// ponytail: swap to react-query if a third screen needs shared cache invalidation.
export function useFetch<T>(fn: () => Promise<T>, deps: unknown[]): FetchState<T> {
  const [state, setState] = useState<FetchState<T>>({ data: null, error: null, loading: true });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, loading: true });
    fn()
      .then((data) => !cancelled && setState({ data, error: null, loading: false }))
      .catch((err: Error) => !cancelled && setState({ data: null, error: err.message, loading: false }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
