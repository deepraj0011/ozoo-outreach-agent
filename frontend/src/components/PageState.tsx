import type { ReactNode } from "react";
import { Alert, Button } from "@mui/material";
import type { Loadable } from "../lib/pageState";

interface Props<T> {
  state: Loadable<T>;
  onRetry: () => void;
  skeleton: ReactNode;
  children: (data: T) => ReactNode;
}

export default function PageState<T>({ state, onRetry, skeleton, children }: Props<T>) {
  if (state.status === "loading") return <>{skeleton}</>;
  if (state.status === "error") {
    return (
      <Alert severity="error" action={<Button color="inherit" size="small" onClick={onRetry}>Retry</Button>}>
        {state.message}
      </Alert>
    );
  }
  return <>{children(state.data)}</>;
}
