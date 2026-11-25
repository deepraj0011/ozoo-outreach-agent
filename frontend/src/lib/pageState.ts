import { errorText } from "../api";

export type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

/** One place that turns (loading, error, data) into what a page renders. */
export function pageState<T>(loading: boolean, error: unknown, data: T | null): Loadable<T> {
  if (error) return { status: "error", message: errorText(error) };
  if (data !== null && data !== undefined) return { status: "ready", data };
  // Not loading, no error, no data: still treated as loading until a load starts and settles.
  void loading;
  return { status: "loading" };
}
