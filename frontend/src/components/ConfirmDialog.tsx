import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from "@mui/material";

interface Props {
  open: boolean;
  title: string;
  text: string;
  confirm: string;
  onConfirm: () => void;
  onClose: () => void;
}

export default function ConfirmDialog({ open, title, text, confirm, onConfirm, onClose }: Props) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>{text}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button color="error" variant="contained" onClick={onConfirm}>{confirm}</Button>
      </DialogActions>
    </Dialog>
  );
}
