import type { ReactNode } from "react";
import { Link } from "react-router";
import { Pencil, Trash2 } from "lucide-react";
import { ApiError } from "~/lib/api";
import { deleteItem, isStale, type ItemKind } from "./inventory";
import { DraftActions, DraftBadge, type DraftTarget } from "./ConfirmDraftSheet";
import { RowAction, RowActions } from "./RowAction";

export const CONFLICT = "This was changed meanwhile. Reload to see the latest.";

/** Removes an item by its revision. Done: `changed()`; a stale revision also shows CONFLICT; other refusals are shown. */
export async function removeItem(
  kind: ItemKind,
  item: { id: string; revision: number },
  changed: () => void,
  setError: (text: string | null) => void,
) {
  setError(null);
  try {
    await deleteItem(kind, item.id, item.revision);
    changed();
  } catch (e) {
    if (isStale(e)) {
      changed();
      setError(CONFLICT);
    } else setError(e instanceof ApiError ? e.message : "Could not remove it. Try again.");
  }
}

/** The Confirm / Discard target of a draft item; undefined when it is confirmed. */
export const draftOf = (
  kind: "deployment" | "connection" | "relation" | "code_dependency",
  item: { id: string; revision: number; status: string },
): DraftTarget | undefined => (item.status === "draft" ? { kind, id: item.id, revision: item.revision } : undefined);

export const List = ({ children }: { children: ReactNode }) => <ul className="flex flex-col gap-1">{children}</ul>;

/** A list row with Edit (when given) and Remove; `href` makes its text a link (a connection's other service), `detail`
 * is a line under it (a relation's description). A draft shows its badge and Confirm / Discard (then `onDone`). */
export function Row({
  text,
  href,
  detail,
  name,
  draft,
  onDone,
  onEdit,
  onRemove,
}: {
  text: string;
  href?: string;
  detail?: string;
  name: string;
  draft?: DraftTarget;
  onDone?: () => void;
  onEdit?: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="flex items-center justify-between gap-2 text-sm">
      <span className="flex min-w-0 flex-col">
        <span className="flex min-w-0 items-center gap-2">
          {href ? (
            <Link className="min-w-0 underline-offset-4 [overflow-wrap:anywhere] hover:underline" to={href}>
              {text}
            </Link>
          ) : (
            <span className="min-w-0 [overflow-wrap:anywhere]">{text}</span>
          )}
          {draft && <DraftBadge />}
        </span>
        {detail && <span className="text-muted-foreground [overflow-wrap:anywhere]">{detail}</span>}
      </span>
      <RowActions>
        {draft && <DraftActions name={name} confirm={draft} onDone={() => onDone?.()} />}
        {onEdit && (
          <RowAction label="Edit" name={name} onClick={onEdit}>
            <Pencil />
          </RowAction>
        )}
        <RowAction label="Remove" name={name} destructive onClick={onRemove}>
          <Trash2 />
        </RowAction>
      </RowActions>
    </li>
  );
}
