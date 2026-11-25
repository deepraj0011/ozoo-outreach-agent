import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, TextField } from "@mui/material";

const KEY = "ozoo.outreach.by";
const DEFAULT_NAME = "PM";

function readStored(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function store(name: string): void {
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    // private window or blocked storage: the name lasts for this visit only
  }
}

interface UserCtx {
  name: string;
  askName: () => void;
}

const Ctx = createContext<UserCtx>({ name: DEFAULT_NAME, askName: () => undefined });

export function useUser(): UserCtx {
  return useContext(Ctx);
}

/** Every change records who made it (`by`): ask once, remember in this browser. */
export function UserProvider({ children }: { children: ReactNode }) {
  const stored = readStored();
  const [name, setName] = useState<string>(stored || DEFAULT_NAME);
  const [open, setOpen] = useState<boolean>(!stored);
  const [draft, setDraft] = useState<string>(stored || DEFAULT_NAME);

  const askName = useCallback(() => {
    setDraft(name);
    setOpen(true);
  }, [name]);

  const save = () => {
    const clean = draft.trim().slice(0, 80) || DEFAULT_NAME;
    setName(clean);
    store(clean);
    setOpen(false);
  };

  const value = useMemo(() => ({ name, askName }), [name, askName]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <Dialog open={open} onClose={save} maxWidth="xs" fullWidth>
        <DialogTitle>What is your name?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Every change to templates, the schedule or a lead records who made it.
          </DialogContentText>
          <TextField
            autoFocus
            fullWidth
            label="Your name"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
        </DialogContent>
        <DialogActions>
          <Button variant="contained" onClick={save}>
            Continue
          </Button>
        </DialogActions>
      </Dialog>
    </Ctx.Provider>
  );
}
